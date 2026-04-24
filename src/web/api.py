"""FastAPI backend for the w3_risk_dashboard Web UI.

Contract: docs/WEB_API_CONTRACT.md — DO NOT drift from the field shapes there
without also updating the doc.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import asyncio

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import AppConfig, load_config
from ..hidden_pools import HiddenPoolStore
from ..lark_notifier import CHAIN_ZH, LEVEL_ZH, PROTOCOL_ZH
from ..logger import log
from ..mute_store import MuteStore, parse_duration
from ..rpc_pool import RpcPool
from ..sqlite_sink import SqliteSink
from ..holders_subgraph import fetch_top_holders_subgraph
from .cache import TTLCache
from .on_demand import (
    fetch_permission_events,
    fetch_pool_activity,
    fetch_top_holders_by_netflow,
)
from .resolver import AaveDeploymentResolver
from .ws_hub import WsHub, sqlite_watcher


ROOT = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
PID_PATH = ROOT / "data" / "w3risk.pid"
DB_PATH = ROOT / "data" / "snapshots.db"


# ---------- state ----------
class AppState:
    def __init__(self):
        self.cfg: AppConfig = load_config()
        self.sink = SqliteSink(db_path=str(DB_PATH), retention_days=7)
        self.mute_store = MuteStore()
        self.hidden_pools = HiddenPoolStore()
        self.rpc_pools: dict[str, RpcPool] = {}
        for chain in self.cfg.enabled_chains:
            if chain not in self.cfg.chains:
                continue
            self.rpc_pools[chain] = RpcPool(
                chain, self.cfg.chains[chain], self.cfg.defaults
            )
        self.resolver = AaveDeploymentResolver(self.rpc_pools, self.cfg)
        self.activity_cache = TTLCache(ttl_sec=30)
        self.holders_cache = TTLCache(ttl_sec=300)
        self.permissions_cache = TTLCache(ttl_sec=60)
        self.server_started_at = time.time()
        self.ws_hub = WsHub()
        self.ws_watcher_task: asyncio.Task | None = None

    async def close(self) -> None:
        if self.ws_watcher_task is not None and not self.ws_watcher_task.done():
            self.ws_watcher_task.cancel()
            try:
                await self.ws_watcher_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for pool in self.rpc_pools.values():
            try:
                await pool.close()
            except Exception as exc:  # noqa: BLE001
                log.debug("rpc pool close failed: %s", exc)
        try:
            self.sink.close()
        except Exception as exc:  # noqa: BLE001
            log.debug("sink close failed: %s", exc)


state: AppState | None = None


app = FastAPI(title="w3_risk_dashboard", version="1.0.0")

# CORS (loose for dev — frontend is served from same origin in prod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    global state
    state = AppState()
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    # Background watcher: polls SQLite every 2s, broadcasts new alerts/events
    # to all WebSocket clients. Running inside the web process, so it sees
    # rows written by the separate monitor process via SQLite WAL.
    st = state
    state.ws_watcher_task = asyncio.create_task(
        sqlite_watcher(st.ws_hub, st.sink, _status_payload)
    )

    log.info(
        "web api started (chains=%s protocols=%s ws=on)",
        list(state.rpc_pools.keys()), state.cfg.enabled_protocols,
    )


@app.on_event("shutdown")
async def _shutdown() -> None:
    global state
    if state is not None:
        await state.close()
        state = None


# ---------- helpers ----------
def _require_state() -> AppState:
    if state is None:
        raise HTTPException(status_code=503, detail="server not ready")
    return state


def _pool_row_to_obj(row: dict) -> dict:
    return {
        "pool_key": row["pool_key"],
        "chain": row["chain"],
        "protocol": row["protocol"],
        "symbol": row["symbol"],
        "asset": row["asset"],
        "ts": float(row["ts"]),
        "block_number": int(row["block_number"]),
        "supply_usd": float(row.get("supply_usd") or 0.0),
        "borrow_usd": float(row.get("borrow_usd") or 0.0),
        "available_liquidity_usd": float(row.get("available_liquidity_usd") or 0.0),
        "utilization_pct": float(row.get("utilization_pct") or 0.0),
        "price_usd": float(row.get("price_usd") or 0.0),
    }


def _alert_row_to_obj(row: dict, *, include_pool_info: bool = False) -> dict:
    out = {
        "ts": float(row["ts"]),
        "level": row["level"],
        "level_zh": LEVEL_ZH.get(row["level"], row["level"]),
        "rule": row["rule"],
        "message": row.get("message") or "",
        "metrics": row.get("metrics") or {},
    }
    if include_pool_info:
        out["pool_key"] = row.get("pool_key")
        out["chain"] = row.get("chain")
        out["protocol"] = row.get("protocol")
        out["symbol"] = row.get("symbol")
    return out


def _monitor_status() -> tuple[bool, int | None]:
    if not PID_PATH.exists():
        return False, None
    try:
        pid = int(PID_PATH.read_text().strip())
    except (OSError, ValueError):
        return False, None
    try:
        os.kill(pid, 0)  # signal 0 = existence check
        return True, pid
    except OSError:
        return False, pid


def _mute_to_obj(m) -> dict:
    return {
        "pool_key": m.pool_key,
        "rule": m.rule,
        "until": float(m.until) if m.until else None,
        "human_until": m.human_until(),
        "reason": m.reason or "",
        "muted_at": float(m.muted_at),
    }


# ---------- static & index ----------
# 前端是单文件 SPA,每次发布即热更新。浏览器缓存旧 JS 会导致"加载不出来"的
# 假象(老版本 boot 流程和新后端不兼容)。index.html 一律 no-cache,让浏览器
# 每次都拿最新。JS/CSS 变化很低频,no-cache 成本可忽略。
NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/")
async def index() -> FileResponse:
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        # placeholder for when the frontend isn't built yet
        return JSONResponse(
            {
                "ok": True,
                "message": "w3_risk_dashboard backend is running",
                "static_dir": str(STATIC_DIR),
                "frontend_present": False,
            }
        )
    return FileResponse(str(idx), headers=NO_CACHE_HEADERS)


# Mount static after startup creates the dir (safe to mount empty dir).
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")


# ---------- /api/status ----------
def _status_payload() -> dict:
    st = _require_state()
    running, pid = _monitor_status()
    stats = st.sink.stats()
    return {
        "ok": True,
        "server_started_at": st.server_started_at,
        "uptime_sec": int(time.time() - st.server_started_at),
        "monitor_running": running,
        "monitor_pid": pid,
        "db": {
            "snapshots": int(stats.get("snapshots", 0)),
            "alerts": int(stats.get("alerts", 0)),
            "path": stats.get("path", str(DB_PATH)),
        },
        "chains_configured": list(st.rpc_pools.keys()),
        "protocols_configured": list(st.cfg.enabled_protocols),
        "ws_clients": st.ws_hub.client_count(),
    }


@app.get("/api/status")
async def api_status() -> dict:
    return _status_payload()


# ---------- /api/protocols ----------
@app.get("/api/protocols")
async def api_protocols() -> dict:
    st = _require_state()
    protocols_out: list[dict] = []
    for proto in st.cfg.enabled_protocols:
        proto_entry = st.cfg.protocols.get(proto) or {}
        chains_list = []
        for chain in st.cfg.enabled_chains:
            if chain not in proto_entry:
                continue
            chain_cfg = st.cfg.chains.get(chain)
            chains_list.append({
                "name": chain,
                "display": CHAIN_ZH.get(chain, chain),
                "chain_id": chain_cfg.chain_id if chain_cfg else None,
            })
        protocols_out.append({
            "name": proto,
            "display": PROTOCOL_ZH.get(proto, proto),
            "chains": chains_list,
        })
    return {"ok": True, "protocols": protocols_out}


# ---------- /api/pools ----------
@app.get("/api/pools")
async def api_pools() -> dict:
    st = _require_state()
    # reload from disk so CLI / parallel API changes are visible
    st.hidden_pools.reload_if_changed()
    hidden = st.hidden_pools.hidden_set()
    rows = st.sink.list_pools_latest()
    rows = [r for r in rows if r["pool_key"] not in hidden]
    return {"ok": True, "pools": [_pool_row_to_obj(r) for r in rows]}


@app.delete("/api/pools/{pool_key}")
async def api_pool_delete(pool_key: str, reason: str = Query("UI 删除")) -> dict:
    """Hide a pool from the dashboard. No new snapshots/alerts will be
    generated for it; existing history stays in SQLite and can be
    restored via POST /api/hidden_pools/{pool_key}/restore."""
    st = _require_state()
    entry = st.hidden_pools.add(pool_key, reason=reason)
    log.info("pool hidden pool_key=%s reason=%r", pool_key, entry.reason)
    return {
        "ok": True,
        "pool_key": entry.pool_key,
        "hidden_at": entry.hidden_at,
        "reason": entry.reason,
    }


@app.get("/api/hidden_pools")
async def api_hidden_pools_list() -> dict:
    st = _require_state()
    st.hidden_pools.reload_if_changed()
    return {
        "ok": True,
        "hidden": [
            {"pool_key": h.pool_key, "hidden_at": h.hidden_at, "reason": h.reason}
            for h in st.hidden_pools.list_all()
        ],
    }


@app.post("/api/hidden_pools/{pool_key}/restore")
async def api_pool_restore(pool_key: str) -> dict:
    st = _require_state()
    removed = st.hidden_pools.remove(pool_key)
    if removed:
        log.info("pool restored pool_key=%s", pool_key)
    return {"ok": True, "pool_key": pool_key, "restored": bool(removed)}


# ---------- /api/pools/{pool_key}/overview ----------
@app.get("/api/pools/{pool_key}/overview")
async def api_pool_overview(pool_key: str) -> JSONResponse:
    st = _require_state()
    row = st.sink.get_pool_latest(pool_key)
    if row is None:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": "pool not found"},
        )
    pool_obj = _pool_row_to_obj(row)
    pool_obj["chain_zh"] = CHAIN_ZH.get(pool_obj["chain"], pool_obj["chain"])
    pool_obj["protocol_zh"] = PROTOCOL_ZH.get(pool_obj["protocol"], pool_obj["protocol"])
    return JSONResponse({"ok": True, "pool": pool_obj})


# ---------- /api/pools/{pool_key}/history ----------
@app.get("/api/pools/{pool_key}/history")
async def api_pool_history(
    pool_key: str,
    hours: float = Query(24.0, ge=0.1, le=24 * 7),
) -> dict:
    st = _require_state()
    rows = st.sink.get_history(pool_key, hours=hours, max_points=500)
    series = [
        {
            "ts": float(r["ts"]),
            "supply_usd": float(r.get("supply_usd") or 0.0),
            "borrow_usd": float(r.get("borrow_usd") or 0.0),
            "available_liquidity_usd": float(r.get("available_liquidity_usd") or 0.0),
            "utilization_pct": float(r.get("utilization_pct") or 0.0),
            "price_usd": float(r.get("price_usd") or 0.0),
        }
        for r in rows
    ]
    return {
        "ok": True,
        "pool_key": pool_key,
        "hours": hours,
        "series": series,
    }


# ---------- /api/pools/{pool_key}/activity ----------
def _parse_pool_key(pool_key: str) -> tuple[str, str, str]:
    parts = pool_key.split(":")
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail="invalid pool_key format")
    return parts[0], parts[1], parts[2]


@app.get("/api/pools/{pool_key}/activity")
async def api_pool_activity(
    pool_key: str,
    hours: float = Query(1.0, ge=0.1, le=24.0),
    min_usd: float = Query(100_000.0, ge=0.0),
) -> dict:
    st = _require_state()
    row = st.sink.get_pool_latest(pool_key)
    if row is None:
        return {"ok": False, "error": "pool not found"}
    chain, protocol, _symbol = _parse_pool_key(pool_key)
    if protocol != "aave_v3":
        return {"ok": False, "error": f"protocol {protocol} not supported for activity"}

    rpc_pool = st.rpc_pools.get(chain)
    if rpc_pool is None:
        return {"ok": False, "error": f"no RpcPool for chain={chain}"}

    async def _fetch():
        deployment = await st.resolver.resolve(chain)
        return await fetch_pool_activity(
            rpc_pool=rpc_pool,
            pool_addr=deployment["pool"],
            reserve_addr=row["asset"],
            hours=hours,
            min_usd=min_usd,
            price_usd=float(row.get("price_usd") or 0.0),
            decimals=_infer_decimals(row["symbol"]),
        )

    key = (pool_key, round(hours, 3), round(min_usd, 2))
    try:
        events, cached_at = await st.activity_cache.get_or_fetch(key, _fetch)
    except Exception as exc:  # noqa: BLE001
        log.warning("activity fetch failed pool=%s err=%s", pool_key, exc)
        return {"ok": False, "error": f"activity fetch failed: {exc!s}"}

    return {
        "ok": True,
        "pool_key": pool_key,
        "hours": hours,
        "min_usd": min_usd,
        "cached_at": cached_at,
        "events": events,
    }


# decimals table — needed because SQLite schema doesn't store decimals.
# For tokens not listed we default to 18.
_DECIMALS_BY_SYMBOL: dict[str, int] = {
    "USDC": 6, "USDT": 6, "USDC.e": 6,
    "WBTC": 8, "cbBTC": 8, "BTCB": 18,  # BTCB on BSC is 18
    "DAI": 18, "WETH": 18, "wstETH": 18, "weETH": 18, "rETH": 18,
    "WMATIC": 18, "WBNB": 18, "MATIC": 18,
}


def _infer_decimals(symbol: str) -> int:
    return _DECIMALS_BY_SYMBOL.get(symbol, 18)


# ---------- /api/pools/{pool_key}/holders ----------
@app.get("/api/pools/{pool_key}/holders")
async def api_pool_holders(
    pool_key: str,
    hours: float = Query(24.0, ge=0.1, le=24 * 3),
    method: str = Query("auto", pattern="^(auto|subgraph|net_flow)$"),
) -> dict:
    st = _require_state()
    row = st.sink.get_pool_latest(pool_key)
    if row is None:
        return {"ok": False, "error": "pool not found"}
    chain, protocol, _ = _parse_pool_key(pool_key)
    if protocol != "aave_v3":
        return {"ok": False, "error": f"protocol {protocol} not supported"}
    rpc_pool = st.rpc_pools.get(chain)
    if rpc_pool is None:
        return {"ok": False, "error": f"no RpcPool for chain={chain}"}

    decimals = _infer_decimals(row["symbol"])
    price_usd = float(row.get("price_usd") or 0.0)
    subgraph_url = st.cfg.subgraph_urls_aave_v3.get(chain)

    # Resolve effective method
    if method == "auto":
        effective = "subgraph" if subgraph_url else "net_flow"
    else:
        effective = method

    if effective == "subgraph" and not subgraph_url:
        return {
            "ok": False,
            "error": (
                f"subgraph URL not configured for chain={chain}; "
                f"set THE_GRAPH_AAVE_V3_URL_{chain.upper()} in .env "
                "or use method=net_flow"
            ),
        }

    if effective == "subgraph":
        async def _fetch():
            return await fetch_top_holders_subgraph(
                subgraph_url=subgraph_url,
                reserve_addr=row["asset"],
                price_usd=price_usd,
                decimals=decimals,
            )
        key = (pool_key, "subgraph")
    else:
        async def _fetch():
            deployment = await st.resolver.resolve(chain)
            return await fetch_top_holders_by_netflow(
                rpc_pool=rpc_pool,
                pool_addr=deployment["pool"],
                reserve_addr=row["asset"],
                hours=hours,
                price_usd=price_usd,
                decimals=decimals,
            )
        key = (pool_key, "net_flow", round(hours, 3))

    try:
        top, cached_at = await st.holders_cache.get_or_fetch(key, _fetch)
    except Exception as exc:  # noqa: BLE001
        log.warning("holders fetch failed pool=%s method=%s err=%s", pool_key, effective, exc)
        return {"ok": False, "error": f"holders fetch failed: {exc!s}"}

    resp = {
        "ok": True,
        "pool_key": pool_key,
        "method": effective,
        "cached_at": cached_at,
        "top": top,
    }
    if effective == "net_flow":
        resp["hours"] = hours
    return resp


# ---------- /api/pools/{pool_key}/alerts ----------
@app.get("/api/pools/{pool_key}/alerts")
async def api_pool_alerts(
    pool_key: str,
    limit: int = Query(50, ge=1, le=500),
) -> dict:
    st = _require_state()
    rows = st.sink.get_alerts_for_pool(pool_key, limit=limit)
    return {
        "ok": True,
        "pool_key": pool_key,
        "alerts": [_alert_row_to_obj(r, include_pool_info=False) for r in rows],
    }


# ---------- /api/permissions ----------
@app.get("/api/permissions")
async def api_permissions(
    protocol: str = Query(...),
    chain: str = Query(...),
    hours: float = Query(24.0, ge=0.1, le=24 * 7),
) -> dict:
    st = _require_state()
    if protocol != "aave_v3":
        return {"ok": False, "error": f"protocol {protocol} not supported"}
    rpc_pool = st.rpc_pools.get(chain)
    if rpc_pool is None:
        return {"ok": False, "error": f"no RpcPool for chain={chain}"}

    async def _fetch():
        deployment = await st.resolver.resolve(chain)
        return await fetch_permission_events(
            rpc_pool=rpc_pool,
            deployment=deployment,
            hours=hours,
        )

    key = (protocol, chain, round(hours, 3))
    try:
        events, cached_at = await st.permissions_cache.get_or_fetch(key, _fetch)
    except Exception as exc:  # noqa: BLE001
        log.warning("permissions fetch failed chain=%s err=%s", chain, exc)
        return {"ok": False, "error": f"permissions fetch failed: {exc!s}"}

    return {
        "ok": True,
        "protocol": protocol,
        "chain": chain,
        "hours": hours,
        "cached_at": cached_at,
        "events": events,
    }


# ---------- /api/ws/stream ----------
@app.websocket("/api/ws/stream")
async def ws_stream(ws: WebSocket) -> None:
    """Server-push stream.

    Clients receive three message types:
      {"type": "alert",  "data": {<alert fields>}}
      {"type": "event",  "data": {<event fields>}}
      {"type": "status", "data": {<status fields>}, "ts": <float>}

    On connect, server sends one immediate "status" + one "recent_alerts"
    snapshot so the UI does not need to make initial REST calls.
    """
    st = _require_state()
    await ws.accept()
    await st.ws_hub.register(ws)

    # initial snapshot
    try:
        await ws.send_json({"type": "status", "data": _status_payload(), "ts": time.time()})
        recent = st.sink.recent_alerts(limit=20)
        await ws.send_json({
            "type": "recent_alerts",
            "data": [_alert_row_to_obj(r, include_pool_info=True) for r in recent],
        })
    except Exception as exc:  # noqa: BLE001
        log.debug("ws initial snapshot failed: %s", exc)

    try:
        # keep the connection open; we only need it to detect disconnect.
        # Clients don't need to send anything, but ping messages are fine.
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_json({"type": "pong", "ts": time.time()})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("ws recv loop ended: %s", exc)
    finally:
        await st.ws_hub.unregister(ws)


# ---------- /api/alerts/recent ----------
@app.get("/api/alerts/recent")
async def api_alerts_recent(
    limit: int = Query(20, ge=1, le=500),
) -> dict:
    st = _require_state()
    rows = st.sink.recent_alerts(limit=limit)
    return {
        "ok": True,
        "alerts": [_alert_row_to_obj(r, include_pool_info=True) for r in rows],
    }


# ---------- /api/alerts/summary ----------
@app.get("/api/alerts/summary")
async def api_alerts_summary(
    hours: float = Query(1.0, ge=0.01, le=24.0 * 7),
) -> dict:
    """按 pool_key 汇总最近 N 小时未屏蔽告警数,供侧栏徽章 / 总览页用。

    - 已屏蔽的 (pool_key, rule) 命中时不计入 by_pool 计数
    - 整池屏蔽则该池 by_pool 计数全部为 0
    - 返回 total / critical_total / muted_total 便于顶部展示
    """
    st = _require_state()
    now = time.time()
    since = now - max(0.01, hours) * 3600.0
    rows = st.sink.get_alerts_summary_rows(since_ts=since)

    # reload mutes 以反映最新
    active_mutes = st.mute_store.list_active()

    def _is_muted(pool_key: str, rule: str) -> bool:
        for m in active_mutes:
            if m.pool_key != pool_key:
                continue
            if m.rule is None:
                return True  # 整池屏蔽
            if m.rule == rule:
                return True
        return False

    by_pool: dict[str, int] = {}
    by_pool_muted: dict[str, int] = {}
    by_level: dict[str, int] = {"info": 0, "warning": 0, "alert": 0, "critical": 0}
    last_ts_by_pool: dict[str, float] = {}
    total = 0
    muted_total = 0

    for r in rows:
        pk = r.get("pool_key")
        rule = r.get("rule") or ""
        cnt = int(r.get("cnt") or 0)
        level = r.get("level") or "info"
        last_ts = float(r.get("last_ts") or 0.0)
        if not pk:
            continue
        if _is_muted(pk, rule):
            by_pool_muted[pk] = by_pool_muted.get(pk, 0) + cnt
            muted_total += cnt
            continue
        by_pool[pk] = by_pool.get(pk, 0) + cnt
        by_level[level] = by_level.get(level, 0) + cnt
        total += cnt
        if last_ts > last_ts_by_pool.get(pk, 0.0):
            last_ts_by_pool[pk] = last_ts

    return {
        "ok": True,
        "hours": hours,
        "since_ts": since,
        "now_ts": now,
        "total": total,
        "muted_total": muted_total,
        "by_pool": by_pool,
        "by_pool_muted": by_pool_muted,
        "by_level": by_level,
        "last_ts_by_pool": last_ts_by_pool,
    }


# ---------- /api/mutes ----------
@app.get("/api/mutes")
async def api_mutes_list() -> dict:
    st = _require_state()
    mutes = st.mute_store.list_active()
    return {"ok": True, "mutes": [_mute_to_obj(m) for m in mutes]}


class MuteCreateBody(BaseModel):
    pool_key: str
    rule: str | None = None
    duration: str | None = None
    reason: str | None = ""


@app.post("/api/mutes")
async def api_mutes_add(body: MuteCreateBody) -> dict:
    st = _require_state()
    duration_sec = parse_duration(body.duration) if body.duration else None
    mute = st.mute_store.add(
        pool_key=body.pool_key,
        rule=body.rule,
        duration_sec=duration_sec,
        reason=body.reason or "",
    )
    return {"ok": True, "mute": _mute_to_obj(mute)}


@app.delete("/api/mutes")
async def api_mutes_remove(
    pool_key: str = Query(...),
    rule: str | None = Query(None),
) -> dict:
    st = _require_state()
    n = st.mute_store.remove(pool_key, rule)
    return {"ok": True, "removed": int(n)}


# ---------- entry ----------
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("WEB_PORT", "8787"))
    uvicorn.run(
        "src.web.api:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )

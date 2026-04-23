"""WebSocket hub: tracks active clients, fans out messages.

Design: the monitor process (`./w3risk start`) writes rows to SQLite.
The web process (`./w3risk web`) runs a single background task that polls
SQLite every 2s for new rows and broadcasts them to all connected clients.
One DB read per interval, N fanout — not N per client.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from ..logger import log
from ..lark_notifier import CHAIN_ZH, LEVEL_ZH, PROTOCOL_ZH
from ..sqlite_sink import SqliteSink


class WsHub:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, ws: WebSocket) -> None:
        async with self._lock:
            self.connections.add(ws)
        log.info("ws client connected (total=%d)", len(self.connections))

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            self.connections.discard(ws)
        log.info("ws client disconnected (total=%d)", len(self.connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        if not self.connections:
            return
        dead: list[WebSocket] = []
        for ws in list(self.connections):
            if ws.client_state != WebSocketState.CONNECTED:
                dead.append(ws)
                continue
            try:
                await ws.send_json(message)
            except Exception as exc:  # noqa: BLE001
                log.debug("ws send failed, dropping: %s", exc)
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self.connections.discard(ws)

    def client_count(self) -> int:
        return len(self.connections)


def _serialize_alert(row: dict) -> dict:
    return {
        "ts": float(row["ts"]),
        "level": row["level"],
        "level_zh": LEVEL_ZH.get(row["level"], row["level"]),
        "rule": row["rule"],
        "message": row.get("message") or "",
        "pool_key": row.get("pool_key"),
        "chain": row.get("chain"),
        "protocol": row.get("protocol"),
        "symbol": row.get("symbol"),
        "metrics": row.get("metrics") or {},
    }


def _serialize_event(row: dict) -> dict:
    chain = row.get("chain") or ""
    return {
        "ts": float(row["ts"]),
        "chain": chain,
        "chain_zh": CHAIN_ZH.get(chain, chain),
        "contract": row["contract"],
        "contract_role": row.get("contract_role"),
        "event": row["event"],
        "level": row.get("level"),
        "level_zh": LEVEL_ZH.get(row.get("level") or "", row.get("level") or ""),
        "block_number": int(row["block_number"]),
        "tx_hash": row["tx_hash"],
        "log_index": int(row["log_index"]),
        "old_value": row.get("old_value"),
        "new_value": row.get("new_value"),
        "extra": row.get("extra") or {},
    }


async def sqlite_watcher(
    hub: WsHub,
    sink: SqliteSink,
    get_status_payload,
    *,
    poll_interval_sec: float = 2.0,
    status_every_n_ticks: int = 5,  # ~10s status refresh
) -> None:
    """Background task: polls SQLite for new alerts + events, broadcasts."""
    # start from the current tip so we never re-broadcast pre-existing rows
    last_alert_id = sink.max_alert_id()
    last_event_id = sink.max_event_id()
    tick = 0
    log.info(
        "ws sqlite watcher started (alerts_from=%d events_from=%d interval=%.1fs)",
        last_alert_id, last_event_id, poll_interval_sec,
    )
    while True:
        try:
            await asyncio.sleep(poll_interval_sec)
            tick += 1

            # skip DB work when nobody is listening
            if hub.client_count() == 0:
                # still advance cursors so a client connecting later
                # doesn't get a flood of "missed" rows
                last_alert_id = sink.max_alert_id()
                last_event_id = sink.max_event_id()
                continue

            new_alerts = sink.alerts_after(last_alert_id)
            if new_alerts:
                last_alert_id = int(new_alerts[-1]["id"])
                for r in new_alerts:
                    await hub.broadcast({
                        "type": "alert",
                        "data": _serialize_alert(r),
                    })

            new_events = sink.events_after(last_event_id)
            if new_events:
                last_event_id = int(new_events[-1]["id"])
                for r in new_events:
                    await hub.broadcast({
                        "type": "event",
                        "data": _serialize_event(r),
                    })

            if tick % status_every_n_ticks == 0:
                try:
                    payload = get_status_payload()
                    await hub.broadcast({
                        "type": "status",
                        "data": payload,
                        "ts": time.time(),
                    })
                except Exception as exc:  # noqa: BLE001
                    log.debug("ws status payload failed: %s", exc)

        except asyncio.CancelledError:
            log.info("ws sqlite watcher cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("ws sqlite watcher tick failed: %s", exc)

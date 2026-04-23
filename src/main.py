"""Main entry: runs one collector per enabled chain and loops every N seconds."""
import argparse
import asyncio
import os
import signal
import time

from .aave_v3_collector import AaveV3Collector
from .config import AppConfig, level_ge, load_config
from .event_tracker import EventTracker
from .hidden_pools import HiddenPoolStore
from .lark_notifier import LarkNotifier
from .logger import log
from .mute_store import MuteStore, parse_duration
from .rpc_pool import RpcPool
from .rule_engine import Alert, RuleEngine
from .snapshot_store import SnapshotStore
from .sqlite_sink import SqliteSink


class ChainWorker:
    def __init__(
        self,
        chain: str,
        cfg: AppConfig,
        notifier: LarkNotifier,
        sink: SqliteSink | None,
        mute_store: MuteStore | None = None,
        hidden_pools: HiddenPoolStore | None = None,
    ):
        self.chain = chain
        self.cfg = cfg
        self.notifier = notifier
        self.sink = sink
        self.mute_store = mute_store
        self.hidden_pools = hidden_pools
        self.rpc_pool = RpcPool(chain, cfg.chains[chain], cfg.defaults)
        self.store = SnapshotStore(retention_sec=3600, sink=sink)
        self.rule_engine = RuleEngine(cfg.rules)
        self.collectors: list = []
        self.event_tracker: EventTracker | None = None

    async def init(self) -> None:
        pap: str | None = None
        pool_addr: str | None = None
        if "aave_v3" in self.cfg.enabled_protocols:
            pap = (self.cfg.protocols.get("aave_v3") or {}).get(self.chain, {}).get(
                "pool_addresses_provider"
            )
            if not pap:
                log.warning("aave_v3 not configured for chain=%s, skip", self.chain)
            else:
                watchlist = (self.cfg.watchlist.get("aave_v3") or {}).get(self.chain)
                c = AaveV3Collector(
                    chain=self.chain,
                    pool_addresses_provider=pap,
                    rpc_pool=self.rpc_pool,
                    watchlist_symbols=watchlist,
                )
                await c.init()
                self.collectors.append(c)
                if c.deployment is not None:
                    pool_addr = c.deployment.pool

        if self.sink is not None and (pap or pool_addr):
            self.event_tracker = EventTracker(
                chain=self.chain,
                rpc_pool=self.rpc_pool,
                sink=self.sink,
                pap_addr=pap,
                pool_addr=pool_addr,
                event_rules=(self.cfg.rules or {}).get("events") or {},
            )

    async def tick(self) -> None:
        started = time.time()
        all_snaps = []
        for c in self.collectors:
            try:
                snaps = await c.collect()
                all_snaps.extend(snaps)
            except Exception as exc:  # noqa: BLE001
                log.exception("collector %s failed: %s", type(c).__name__, exc)

        if not all_snaps:
            log.warning("chain=%s no snapshots collected this tick", self.chain)
            return

        # Drop hidden pools: no store, no rule eval, no alerts, no DB writes.
        # Reload from YAML so CLI / UI changes take effect without restart.
        if self.hidden_pools is not None:
            self.hidden_pools.reload_if_changed()
            hidden = self.hidden_pools.hidden_set()
            if hidden:
                before = len(all_snaps)
                all_snaps = [s for s in all_snaps if s.pool_key not in hidden]
                dropped = before - len(all_snaps)
                if dropped:
                    log.debug(
                        "chain=%s dropped %d hidden pool snapshots",
                        self.chain, dropped,
                    )
            if not all_snaps:
                return

        self.store.add(all_snaps)

        # Log a quick summary line per tick
        total_supply = sum(s.supply_usd for s in all_snaps)
        total_borrow = sum(s.borrow_usd for s in all_snaps)
        log.info(
            "chain=%s reserves=%d total_supply=$%s total_borrow=$%s elapsed=%.2fs",
            self.chain,
            len(all_snaps),
            f"{total_supply:,.0f}",
            f"{total_borrow:,.0f}",
            time.time() - started,
        )

        alerts: list[Alert] = self.rule_engine.evaluate(all_snaps, self.store)
        for a in alerts:
            # 屏蔽检查:命中的告警直接丢弃(不推送、不入库)
            if self.mute_store is not None:
                mute = self.mute_store.find(a.pool_key, a.rule)
                if mute is not None:
                    log.info(
                        "alert muted rule=%s pool=%s reason=%r",
                        a.rule, a.pool_key, mute.reason,
                    )
                    continue

            if self.sink is not None:
                try:
                    self.sink.write_alert(a)
                except Exception as exc:  # noqa: BLE001
                    log.warning("sqlite alert write failed: %s", exc)
            if not level_ge(a.level, self.cfg.alert_min_level):
                continue
            ok = await self.notifier.send_alert(a)
            log.info(
                "alert level=%s rule=%s pool=%s sent=%s",
                a.level, a.rule, a.pool_key, ok,
            )

        # Track B: chain events (permissions / proxy upgrades / pause)
        if self.event_tracker is not None:
            try:
                new_events = await self.event_tracker.tick()
            except Exception as exc:  # noqa: BLE001
                log.exception("event tracker tick failed chain=%s: %s", self.chain, exc)
                new_events = []
            for ev in new_events:
                if not level_ge(ev.get("level") or "info", self.cfg.alert_min_level):
                    continue
                ok = await self.notifier.send_event_alert(ev)
                log.info(
                    "event alert chain=%s contract=%s event=%s level=%s sent=%s",
                    ev["chain"], ev["contract"], ev["event"], ev.get("level"), ok,
                )


async def run_loop(cfg: AppConfig) -> None:
    notifier = LarkNotifier(cfg.lark_webhook_url)
    sink = SqliteSink(db_path="data/snapshots.db", retention_days=7)
    stats = sink.stats()
    log.info(
        "sqlite sink: path=%s snapshots=%d alerts=%d",
        stats["path"], stats["snapshots"], stats["alerts"],
    )
    mute_store = MuteStore()
    active_mutes = mute_store.list_active()
    if active_mutes:
        log.info("已加载 %d 条屏蔽规则", len(active_mutes))
        for m in active_mutes:
            log.info(
                "  muted pool=%s rule=%s until=%s reason=%r",
                m.pool_key, m.rule or "*", m.human_until(), m.reason,
            )

    hidden_pools = HiddenPoolStore()
    if hidden_pools.list_all():
        log.info("已加载 %d 个已删除池子(不采集不告警)", len(hidden_pools.list_all()))

    workers: list[ChainWorker] = []
    for chain in cfg.enabled_chains:
        if chain not in cfg.chains:
            log.warning("chain=%s not in rpc config, skip", chain)
            continue
        w = ChainWorker(chain, cfg, notifier, sink, mute_store=mute_store, hidden_pools=hidden_pools)
        try:
            await w.init()
        except Exception as exc:  # noqa: BLE001
            log.exception("worker init failed chain=%s: %s", chain, exc)
            continue
        workers.append(w)

    if not workers:
        log.error("no workers initialized, exiting")
        await notifier.close()
        sink.close()
        return

    # Startup heartbeat
    try:
        from .lark_notifier import CHAIN_ZH, LEVEL_ZH
        chain_list = "、".join(CHAIN_ZH.get(w.chain, w.chain) for w in workers)
        level_zh = LEVEL_ZH.get(cfg.alert_min_level, cfg.alert_min_level)
        await notifier.send_text(
            f"🚀 w3_risk_dashboard 已启动\n"
            f"• 监控链:{chain_list}\n"
            f"• 采集间隔:{cfg.collect_interval_sec} 秒\n"
            f"• 最低推送级别:{level_zh}({cfg.alert_min_level})"
        )
    except Exception:  # noqa: BLE001
        pass

    stop_event = asyncio.Event()

    def _signal_handler():
        log.info("shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    tick = 0
    while not stop_event.is_set():
        tick += 1
        await asyncio.gather(*[w.tick() for w in workers], return_exceptions=True)

        # Every 10 ticks send a heartbeat (only if explicitly enabled)
        if cfg.lark_push_info and tick % 10 == 0:
            for w in workers:
                latest = [w.store.latest(k) for k in w.store.all_keys()]
                latest = [s for s in latest if s is not None]
                total_supply = sum(s.supply_usd for s in latest)
                total_borrow = sum(s.borrow_usd for s in latest)
                await w.notifier.send_heartbeat(
                    chain=w.chain,
                    reserves_count=len(latest),
                    total_supply_usd=total_supply,
                    total_borrow_usd=total_borrow,
                    rpc_health=w.rpc_pool.snapshot(),
                )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=cfg.collect_interval_sec)
        except asyncio.TimeoutError:
            pass

    # Shutdown: close notifier, rpc pools, sink
    await notifier.close()
    for w in workers:
        await w.rpc_pool.close()
    sink.close()
    log.info("exited cleanly")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="w3_risk_dashboard")
    sub = p.add_subparsers(dest="cmd", required=False)
    sub.add_parser("run", help="Start the monitoring loop")
    sub.add_parser("probe", help="Probe all RPC providers and exit")
    sub.add_parser("snapshot", help="Take one collection cycle and print results")
    sub.add_parser("test-lark", help="Send a test Lark message and exit")

    m_add = sub.add_parser("mute", help="Mute alerts for a pool (or a specific rule)")
    m_add.add_argument("pool_key", help="e.g. ethereum:aave_v3:USDT")
    m_add.add_argument("--rule", default=None, help="specific rule name; omit to mute all")
    m_add.add_argument("--duration", default=None, help="e.g. 30m/2h/24h/7d; omit = forever")
    m_add.add_argument("--reason", default="", help="reason (shown in logs)")

    m_rm = sub.add_parser("unmute", help="Remove a mute")
    m_rm.add_argument("pool_key")
    m_rm.add_argument("--rule", default=None, help="specific rule; omit = remove all mutes on pool")

    sub.add_parser("mutes", help="List active mutes")

    web_p = sub.add_parser("web", help="Start the web dashboard (FastAPI + static)")
    web_p.add_argument("--port", type=int, default=None, help="override WEB_PORT env")
    web_p.add_argument("--host", default="0.0.0.0", help="bind address")

    return p.parse_args()


async def cmd_probe(cfg: AppConfig) -> None:
    pools: list[RpcPool] = []
    try:
        for chain in cfg.enabled_chains:
            if chain not in cfg.chains:
                continue
            pool = RpcPool(chain, cfg.chains[chain], cfg.defaults)
            pools.append(pool)
            print(f"\n=== chain: {chain} ===")
            results = await pool.probe_all()
            for r in results:
                marker = "✓" if r.get("ok") else "✗"
                print(
                    f"  {marker} [T{r['tier']}] {r['url']:<50} "
                    f"{'block=' + str(r['block']) if r.get('ok') else 'err=' + r.get('error','')} "
                    f"latency={r['latency_ms']}ms health={r['health']} state={r['state']}"
                )
    finally:
        for p in pools:
            await p.close()


async def cmd_snapshot(cfg: AppConfig) -> None:
    notifier = LarkNotifier(None)  # no push
    workers: list[ChainWorker] = []
    try:
        for chain in cfg.enabled_chains:
            if chain not in cfg.chains:
                continue
            w = ChainWorker(chain, cfg, notifier, sink=None)
            workers.append(w)
            await w.init()
            await w.tick()
            print(f"\n=== chain: {chain} ===")
            for key in w.store.all_keys():
                s = w.store.latest(key)
                if not s:
                    continue
                print(
                    f"  {s.symbol:<8} supply=${s.supply_usd:>14,.0f} "
                    f"borrow=${s.borrow_usd:>14,.0f} "
                    f"util={s.utilization_pct:>5.2f}% "
                    f"price=${s.price_usd:>10,.4f} block={s.block_number}"
                )
    finally:
        for w in workers:
            await w.rpc_pool.close()
        await notifier.close()


async def cmd_test_lark(cfg: AppConfig) -> None:
    if not cfg.lark_webhook_url:
        print("LARK_WEBHOOK_URL 未设置,请检查 .env")
        return
    notifier = LarkNotifier(cfg.lark_webhook_url)
    ok = await notifier.send_text(
        "✅ w3_risk_dashboard Lark webhook 自检通过"
    )
    # 发一条样例告警卡片,方便看中文排版
    from .rule_engine import Alert
    demo = Alert(
        level="critical",
        rule="utilization_99pct",
        pool_key="ethereum:aave_v3:USDT",
        chain="ethereum",
        protocol="aave_v3",
        symbol="USDT",
        message="demo",
        metrics={
            "utilization_pct": 99.92,
            "supply_usd": 1970855668.12,
            "borrow_usd": 1969307778.34,
            "available_liquidity_usd": 1547889.78,
        },
    )
    ok2 = await notifier.send_alert(demo)
    await notifier.close()
    print(f"Lark 文本: {'ok' if ok else 'failed'}   Lark 卡片: {'ok' if ok2 else 'failed'}")


def cmd_mute(args) -> None:
    store = MuteStore()
    duration_sec = parse_duration(args.duration)
    mute = store.add(args.pool_key, args.rule, duration_sec, args.reason)
    print(
        f"✅ 已屏蔽:pool={mute.pool_key} rule={mute.rule or '*'} "
        f"有效期={mute.human_until()} 原因={mute.reason!r}"
    )


def cmd_unmute(args) -> None:
    store = MuteStore()
    n = store.remove(args.pool_key, args.rule)
    if n:
        print(f"✅ 已取消 {n} 条屏蔽 (pool={args.pool_key} rule={args.rule or '*'})")
    else:
        print(f"ℹ️  没有匹配的屏蔽条目 (pool={args.pool_key} rule={args.rule or '*'})")


def cmd_web(args) -> None:
    try:
        import uvicorn  # type: ignore
    except ImportError:
        print("❌ 需要安装 fastapi + uvicorn: pip install -r requirements.txt")
        return
    port = args.port or int(os.environ.get("WEB_PORT", "8787"))
    host = args.host
    print(f"🌐 启动 Web Dashboard: http://{host}:{port}")
    print(f"   数据源:data/snapshots.db(只读),RPC 为 on-demand 查询")
    print(f"   停止:Ctrl+C")
    uvicorn.run(
        "src.web.api:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


def cmd_mutes() -> None:
    store = MuteStore()
    active = store.list_active()
    if not active:
        print("当前无屏蔽规则")
        return
    print(f"当前 {len(active)} 条屏蔽规则:\n")
    for m in active:
        print(f"  • pool={m.pool_key}")
        print(f"    rule={m.rule or '* (全部)'}")
        print(f"    有效期={m.human_until()}")
        print(f"    原因={m.reason or '(无)'}")
        print()


def main() -> None:
    args = _parse_args()
    cfg = load_config()
    cmd = args.cmd or "run"
    if cmd == "run":
        asyncio.run(run_loop(cfg))
    elif cmd == "probe":
        asyncio.run(cmd_probe(cfg))
    elif cmd == "snapshot":
        asyncio.run(cmd_snapshot(cfg))
    elif cmd == "test-lark":
        asyncio.run(cmd_test_lark(cfg))
    elif cmd == "mute":
        cmd_mute(args)
    elif cmd == "unmute":
        cmd_unmute(args)
    elif cmd == "mutes":
        cmd_mutes()
    elif cmd == "web":
        cmd_web(args)


if __name__ == "__main__":
    main()

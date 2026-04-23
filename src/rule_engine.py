"""Rule engine: evaluates current snapshot vs historical baselines."""
import time
from dataclasses import dataclass, field

from .aave_v3_collector import ReserveSnapshot
from .logger import log
from .snapshot_store import SnapshotStore


LEVEL_COLORS = {
    "info": ("blue", "🔵"),
    "warning": ("yellow", "🟡"),
    "alert": ("orange", "🟠"),
    "critical": ("red", "🔴"),
}


@dataclass
class Alert:
    level: str  # info|warning|alert|critical
    rule: str
    pool_key: str
    chain: str
    protocol: str
    symbol: str
    message: str
    metrics: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class RuleEngine:
    def __init__(self, rules_cfg: dict):
        self.cfg = rules_cfg
        self.dedup_window = (rules_cfg.get("dedup") or {}).get("window_sec", 300)
        self._last_fired: dict[tuple[str, str], float] = {}

    def evaluate(self, current: list[ReserveSnapshot], store: SnapshotStore) -> list[Alert]:
        alerts: list[Alert] = []
        now = time.time()
        for snap in current:
            alerts.extend(self._eval_utilization(snap, now))
            alerts.extend(self._eval_tvl_drop(snap, store, now))
            alerts.extend(self._eval_borrow_surge(snap, store, now))
            alerts.extend(self._eval_liquidity_drain(snap, store, now))
        return [a for a in alerts if self._dedup(a)]

    # --------- primitives ---------
    def _dedup(self, alert: Alert) -> bool:
        key = (alert.pool_key, alert.rule)
        last = self._last_fired.get(key, 0)
        if alert.timestamp - last < self.dedup_window:
            log.debug("dedup suppressed %s", alert.rule)
            return False
        self._last_fired[key] = alert.timestamp
        return True

    def _pct_change(self, new: float, old: float) -> float:
        if old <= 0:
            return 0.0
        return (new - old) / old * 100.0

    def _get_baseline(self, store: SnapshotStore, pool_key: str, window: int, now: float):
        target_ts = now - window
        pt = store.point_at_or_before(pool_key, target_ts)
        if pt is None:
            oldest = store.oldest(pool_key)
            if oldest is None:
                return None
            # If history is too young to span the window, skip to avoid false positives
            if now - oldest.ts < window * 0.5:
                return None
            pt = oldest
        return pt

    # --------- rules ---------
    def _eval_utilization(self, snap: ReserveSnapshot, now: float) -> list[Alert]:
        out: list[Alert] = []
        for r in self.cfg.get("utilization", []) or []:
            if snap.utilization_pct >= r["threshold_pct"]:
                out.append(
                    Alert(
                        level=r["level"],
                        rule=r["name"],
                        pool_key=snap.pool_key,
                        chain=snap.chain,
                        protocol=snap.protocol,
                        symbol=snap.symbol,
                        message=(
                            f"{snap.chain}/{snap.protocol} **{snap.symbol}** "
                            f"utilization {snap.utilization_pct:.2f}% "
                            f"≥ {r['threshold_pct']:.0f}% threshold"
                        ),
                        metrics={
                            "utilization_pct": round(snap.utilization_pct, 2),
                            "supply_usd": round(snap.supply_usd, 2),
                            "borrow_usd": round(snap.borrow_usd, 2),
                            "available_liquidity_usd": round(
                                snap.available_liquidity_usd, 2
                            ),
                        },
                        timestamp=now,
                    )
                )
        return out

    def _eval_tvl_drop(self, snap: ReserveSnapshot, store: SnapshotStore, now: float) -> list[Alert]:
        out: list[Alert] = []
        for r in self.cfg.get("tvl_drop", []) or []:
            baseline = self._get_baseline(store, snap.pool_key, r["time_window_sec"], now)
            if baseline is None:
                continue
            change = self._pct_change(snap.supply_usd, baseline.supply_usd)
            if change <= -r["threshold_pct"]:
                out.append(
                    Alert(
                        level=r["level"],
                        rule=r["name"],
                        pool_key=snap.pool_key,
                        chain=snap.chain,
                        protocol=snap.protocol,
                        symbol=snap.symbol,
                        message=(
                            f"{snap.chain}/{snap.protocol} **{snap.symbol}** TVL "
                            f"dropped {abs(change):.2f}% in {r['time_window_sec']}s "
                            f"(supply ${baseline.supply_usd:,.0f} → ${snap.supply_usd:,.0f})"
                        ),
                        metrics={
                            "drop_pct": round(change, 2),
                            "baseline_supply_usd": round(baseline.supply_usd, 2),
                            "current_supply_usd": round(snap.supply_usd, 2),
                            "window_sec": r["time_window_sec"],
                        },
                        timestamp=now,
                    )
                )
        return out

    def _eval_borrow_surge(
        self, snap: ReserveSnapshot, store: SnapshotStore, now: float
    ) -> list[Alert]:
        out: list[Alert] = []
        for r in self.cfg.get("borrow_surge", []) or []:
            baseline = self._get_baseline(store, snap.pool_key, r["time_window_sec"], now)
            if baseline is None:
                continue
            change = self._pct_change(snap.borrow_usd, baseline.borrow_usd)
            if change >= r["threshold_pct"]:
                out.append(
                    Alert(
                        level=r["level"],
                        rule=r["name"],
                        pool_key=snap.pool_key,
                        chain=snap.chain,
                        protocol=snap.protocol,
                        symbol=snap.symbol,
                        message=(
                            f"{snap.chain}/{snap.protocol} **{snap.symbol}** borrow "
                            f"surged {change:.2f}% in {r['time_window_sec']}s "
                            f"(${baseline.borrow_usd:,.0f} → ${snap.borrow_usd:,.0f})"
                        ),
                        metrics={
                            "surge_pct": round(change, 2),
                            "baseline_borrow_usd": round(baseline.borrow_usd, 2),
                            "current_borrow_usd": round(snap.borrow_usd, 2),
                            "window_sec": r["time_window_sec"],
                        },
                        timestamp=now,
                    )
                )
        return out

    def _eval_liquidity_drain(
        self, snap: ReserveSnapshot, store: SnapshotStore, now: float
    ) -> list[Alert]:
        out: list[Alert] = []
        for r in self.cfg.get("liquidity_drain", []) or []:
            baseline = self._get_baseline(store, snap.pool_key, r["time_window_sec"], now)
            if baseline is None:
                continue
            if baseline.available_liquidity_usd <= 0:
                continue
            change = self._pct_change(
                snap.available_liquidity_usd, baseline.available_liquidity_usd
            )
            if change <= -r["threshold_pct"]:
                out.append(
                    Alert(
                        level=r["level"],
                        rule=r["name"],
                        pool_key=snap.pool_key,
                        chain=snap.chain,
                        protocol=snap.protocol,
                        symbol=snap.symbol,
                        message=(
                            f"{snap.chain}/{snap.protocol} **{snap.symbol}** available "
                            f"liquidity drained {abs(change):.2f}% in {r['time_window_sec']}s "
                            f"(${baseline.available_liquidity_usd:,.0f} → "
                            f"${snap.available_liquidity_usd:,.0f})"
                        ),
                        metrics={
                            "drain_pct": round(change, 2),
                            "baseline_liquidity_usd": round(
                                baseline.available_liquidity_usd, 2
                            ),
                            "current_liquidity_usd": round(
                                snap.available_liquidity_usd, 2
                            ),
                            "window_sec": r["time_window_sec"],
                        },
                        timestamp=now,
                    )
                )
        return out

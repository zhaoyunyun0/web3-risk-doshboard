"""In-memory rolling snapshot store keyed by pool_key.

Keeps N seconds of history per pool. Optionally mirrors writes to a SQLite
sink and can bootstrap history from disk on startup.
"""
import time
from collections import deque
from dataclasses import dataclass

from .aave_v3_collector import ReserveSnapshot
from .logger import log


@dataclass
class HistoryPoint:
    ts: float
    supply_usd: float
    borrow_usd: float
    available_liquidity_usd: float
    utilization_pct: float
    price_usd: float


class SnapshotStore:
    def __init__(self, retention_sec: int = 3600, sink=None):
        self.retention_sec = retention_sec
        self._history: dict[str, deque[HistoryPoint]] = {}
        self._latest: dict[str, ReserveSnapshot] = {}
        self.sink = sink
        if self.sink is not None:
            self._bootstrap_from_sink()

    def _bootstrap_from_sink(self) -> None:
        try:
            rows = self.sink.load_recent_snapshots(seconds=self.retention_sec)
        except Exception as exc:  # noqa: BLE001
            log.warning("sqlite bootstrap failed: %s", exc)
            return
        count = 0
        for row in rows:
            dq = self._history.setdefault(row["pool_key"], deque())
            dq.append(
                HistoryPoint(
                    ts=row["ts"],
                    supply_usd=row.get("supply_usd") or 0.0,
                    borrow_usd=row.get("borrow_usd") or 0.0,
                    available_liquidity_usd=row.get("available_liquidity_usd") or 0.0,
                    utilization_pct=row.get("utilization_pct") or 0.0,
                    price_usd=row.get("price_usd") or 0.0,
                )
            )
            count += 1
        if count:
            log.info(
                "sqlite bootstrap: loaded %d history points across %d pools",
                count, len(self._history),
            )

    def add(self, snaps: list[ReserveSnapshot]) -> None:
        now = time.time()
        for s in snaps:
            self._latest[s.pool_key] = s
            dq = self._history.setdefault(s.pool_key, deque())
            dq.append(
                HistoryPoint(
                    ts=s.timestamp,
                    supply_usd=s.supply_usd,
                    borrow_usd=s.borrow_usd,
                    available_liquidity_usd=s.available_liquidity_usd,
                    utilization_pct=s.utilization_pct,
                    price_usd=s.price_usd,
                )
            )
            cutoff = now - self.retention_sec
            while dq and dq[0].ts < cutoff:
                dq.popleft()
        if self.sink is not None:
            try:
                self.sink.write_snapshots(snaps)
            except Exception as exc:  # noqa: BLE001
                log.warning("sqlite write failed: %s", exc)

    def latest(self, pool_key: str) -> ReserveSnapshot | None:
        return self._latest.get(pool_key)

    def point_at_or_before(self, pool_key: str, target_ts: float) -> HistoryPoint | None:
        """Return the most recent point whose ts <= target_ts.

        Used to find the "baseline" snapshot N seconds ago for delta comparison.
        """
        dq = self._history.get(pool_key)
        if not dq:
            return None
        candidate: HistoryPoint | None = None
        for pt in dq:
            if pt.ts <= target_ts:
                candidate = pt
            else:
                break
        return candidate

    def oldest(self, pool_key: str) -> HistoryPoint | None:
        dq = self._history.get(pool_key)
        return dq[0] if dq else None

    def all_keys(self) -> list[str]:
        return list(self._latest.keys())

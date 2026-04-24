"""SQLite persistence for snapshots and alerts.

Writes are synchronous — for the demo's low write volume (a few dozen rows/min)
this is fine. Upgrade to aiosqlite + loop.run_in_executor if we outgrow it.
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

from .aave_v3_collector import ReserveSnapshot
from .logger import log
from .rule_engine import Alert


SCHEMA = """
CREATE TABLE IF NOT EXISTS reserve_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    pool_key TEXT NOT NULL,
    chain TEXT NOT NULL,
    protocol TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset TEXT NOT NULL,
    block_number INTEGER NOT NULL,
    supply_usd REAL,
    borrow_usd REAL,
    available_liquidity_usd REAL,
    utilization_pct REAL,
    price_usd REAL
);
CREATE INDEX IF NOT EXISTS idx_rs_pool_ts ON reserve_snapshots(pool_key, ts);
CREATE INDEX IF NOT EXISTS idx_rs_ts ON reserve_snapshots(ts);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    level TEXT NOT NULL,
    rule TEXT NOT NULL,
    pool_key TEXT NOT NULL,
    chain TEXT,
    protocol TEXT,
    symbol TEXT,
    message TEXT,
    metrics TEXT
);
CREATE INDEX IF NOT EXISTS idx_al_ts ON alerts(ts);
CREATE INDEX IF NOT EXISTS idx_al_pool_ts ON alerts(pool_key, ts);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    chain TEXT NOT NULL,
    contract TEXT NOT NULL,
    contract_role TEXT,
    event TEXT NOT NULL,
    level TEXT,
    block_number INTEGER NOT NULL,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    old_value TEXT,
    new_value TEXT,
    extra TEXT,
    UNIQUE(chain, tx_hash, log_index)
);
CREATE INDEX IF NOT EXISTS idx_ev_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_ev_chain_contract_ts ON events(chain, contract, ts);

CREATE TABLE IF NOT EXISTS event_cursors (
    chain TEXT NOT NULL,
    contract TEXT NOT NULL,
    last_block INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(chain, contract)
);
"""


class SqliteSink:
    def __init__(self, db_path: str = "data/snapshots.db", retention_days: int = 7):
        self.db_path = db_path
        self.retention_days = retention_days
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()
        self._prune_old()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    def _prune_old(self) -> None:
        cutoff = time.time() - self.retention_days * 86400
        with self._lock, self._conn:
            r1 = self._conn.execute(
                "DELETE FROM reserve_snapshots WHERE ts < ?", (cutoff,)
            )
            r2 = self._conn.execute("DELETE FROM alerts WHERE ts < ?", (cutoff,))
            r3 = self._conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            if r1.rowcount or r2.rowcount or r3.rowcount:
                log.info(
                    "pruned %d snapshots, %d alerts, %d events older than %dd",
                    r1.rowcount, r2.rowcount, r3.rowcount, self.retention_days,
                )

    def write_snapshots(self, snaps: list[ReserveSnapshot]) -> None:
        if not snaps:
            return
        rows = [
            (
                s.timestamp, s.pool_key, s.chain, s.protocol, s.symbol, s.asset,
                s.block_number, s.supply_usd, s.borrow_usd,
                s.available_liquidity_usd, s.utilization_pct, s.price_usd,
            )
            for s in snaps
        ]
        with self._lock, self._conn:
            self._conn.executemany(
                """INSERT INTO reserve_snapshots (
                    ts, pool_key, chain, protocol, symbol, asset, block_number,
                    supply_usd, borrow_usd, available_liquidity_usd,
                    utilization_pct, price_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )

    def write_alert(self, a: Alert) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO alerts (
                    ts, level, rule, pool_key, chain, protocol, symbol,
                    message, metrics
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    a.timestamp, a.level, a.rule, a.pool_key, a.chain,
                    a.protocol, a.symbol, a.message, json.dumps(a.metrics),
                ),
            )

    def load_recent_snapshots(self, seconds: int = 3600) -> list[dict]:
        """Load recent rows for bootstrapping in-memory history."""
        since = time.time() - seconds
        with self._lock, self._conn:
            cur = self._conn.execute(
                """SELECT pool_key, ts, chain, protocol, symbol, supply_usd,
                          borrow_usd, available_liquidity_usd,
                          utilization_pct, price_usd
                   FROM reserve_snapshots
                   WHERE ts >= ?
                   ORDER BY ts ASC""",
                (since,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def stats(self) -> dict:
        with self._lock, self._conn:
            snap_cnt = self._conn.execute(
                "SELECT COUNT(*) FROM reserve_snapshots"
            ).fetchone()[0]
            alert_cnt = self._conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
            return {"snapshots": snap_cnt, "alerts": alert_cnt, "path": self.db_path}

    # --------- read APIs (used by web backend) ---------
    def list_pools_latest(self) -> list[dict]:
        """Return the newest reserve_snapshots row per pool_key."""
        sql = """
            SELECT rs.*
            FROM reserve_snapshots rs
            JOIN (
                SELECT pool_key, MAX(ts) AS max_ts
                FROM reserve_snapshots
                GROUP BY pool_key
            ) m ON rs.pool_key = m.pool_key AND rs.ts = m.max_ts
            ORDER BY rs.pool_key ASC
        """
        with self._lock, self._conn:
            cur = self._conn.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        # de-dup in case two snapshots tied on ts — keep first
        seen: set[str] = set()
        out: list[dict] = []
        for r in rows:
            if r["pool_key"] in seen:
                continue
            seen.add(r["pool_key"])
            out.append(r)
        return out

    def get_pool_latest(self, pool_key: str) -> dict | None:
        sql = """
            SELECT * FROM reserve_snapshots
            WHERE pool_key = ?
            ORDER BY ts DESC
            LIMIT 1
        """
        with self._lock, self._conn:
            cur = self._conn.execute(sql, (pool_key,))
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            return dict(zip(cols, row)) if row else None

    def get_history(
        self,
        pool_key: str,
        hours: float = 24.0,
        max_points: int = 500,
    ) -> list[dict]:
        """Return time-ordered history points for a pool, down-sampled
        to at most `max_points` entries (simple stride decimation)."""
        since = time.time() - max(0.1, hours) * 3600.0
        sql = """
            SELECT ts, supply_usd, borrow_usd, available_liquidity_usd,
                   utilization_pct, price_usd
            FROM reserve_snapshots
            WHERE pool_key = ? AND ts >= ?
            ORDER BY ts ASC
        """
        with self._lock, self._conn:
            cur = self._conn.execute(sql, (pool_key, since))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        if not rows:
            return []
        if len(rows) <= max_points:
            return rows
        # stride decimation — always keep first and last
        stride = max(1, len(rows) // max_points)
        sampled = rows[::stride]
        if sampled[-1] is not rows[-1]:
            sampled.append(rows[-1])
        return sampled

    def get_alerts_for_pool(self, pool_key: str, limit: int = 50) -> list[dict]:
        sql = """
            SELECT ts, level, rule, pool_key, chain, protocol, symbol,
                   message, metrics
            FROM alerts
            WHERE pool_key = ?
            ORDER BY ts DESC
            LIMIT ?
        """
        with self._lock, self._conn:
            cur = self._conn.execute(sql, (pool_key, int(limit)))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            r["metrics"] = _parse_metrics(r.get("metrics"))
        return rows

    def recent_alerts(self, limit: int = 20) -> list[dict]:
        sql = """
            SELECT ts, level, rule, pool_key, chain, protocol, symbol,
                   message, metrics
            FROM alerts
            ORDER BY ts DESC
            LIMIT ?
        """
        with self._lock, self._conn:
            cur = self._conn.execute(sql, (int(limit),))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            r["metrics"] = _parse_metrics(r.get("metrics"))
        return rows

    def get_alerts_count_by_pool(
        self,
        since_ts: float,
        until_ts: float | None = None,
    ) -> dict[str, int]:
        """按 pool_key 聚合告警数量,用于侧栏徽章 / 全局总览。

        since_ts / until_ts 为 Unix 秒。until_ts 默认 None = 到现在。
        返回 dict: {pool_key: count}。不做屏蔽过滤 —— 调用方结合 MuteStore
        自行过滤,保持这里的纯 DB 语义。
        """
        sql = "SELECT pool_key, COUNT(*) FROM alerts WHERE ts >= ?"
        params: list = [float(since_ts)]
        if until_ts is not None:
            sql += " AND ts <= ?"
            params.append(float(until_ts))
        sql += " GROUP BY pool_key"
        with self._lock, self._conn:
            cur = self._conn.execute(sql, tuple(params))
            return {row[0]: int(row[1]) for row in cur.fetchall()}

    def get_alerts_summary_rows(
        self,
        since_ts: float,
        until_ts: float | None = None,
    ) -> list[dict]:
        """返回 since 以来每个 (pool_key, rule) 的告警条数 + 最近时间,
        给调用方按 rule 做屏蔽过滤后再汇总用。
        """
        sql = """
            SELECT pool_key, rule, level, COUNT(*) as cnt, MAX(ts) as last_ts
            FROM alerts WHERE ts >= ?
        """
        params: list = [float(since_ts)]
        if until_ts is not None:
            sql += " AND ts <= ?"
            params.append(float(until_ts))
        sql += " GROUP BY pool_key, rule, level"
        with self._lock, self._conn:
            cur = self._conn.execute(sql, tuple(params))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def max_alert_id(self) -> int:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT COALESCE(MAX(id), 0) FROM alerts").fetchone()
        return int(row[0]) if row else 0

    def alerts_after(self, after_id: int, limit: int = 100) -> list[dict]:
        """Return alerts with id > after_id, ordered by id ASC (so caller
        can advance its cursor to the last row's id)."""
        sql = """
            SELECT id, ts, level, rule, pool_key, chain, protocol, symbol,
                   message, metrics
            FROM alerts
            WHERE id > ?
            ORDER BY id ASC
            LIMIT ?
        """
        with self._lock, self._conn:
            cur = self._conn.execute(sql, (int(after_id), int(limit)))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            r["metrics"] = _parse_metrics(r.get("metrics"))
        return rows

    def max_event_id(self) -> int:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
        return int(row[0]) if row else 0

    def events_after(self, after_id: int, limit: int = 100) -> list[dict]:
        sql = """
            SELECT id, ts, chain, contract, contract_role, event, level,
                   block_number, tx_hash, log_index, old_value, new_value, extra
            FROM events
            WHERE id > ?
            ORDER BY id ASC
            LIMIT ?
        """
        with self._lock, self._conn:
            cur = self._conn.execute(sql, (int(after_id), int(limit)))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            r["extra"] = _parse_metrics(r.get("extra"))
        return rows

    # --------- Track B: events & cursors ---------
    def write_events(self, rows: list[dict]) -> list[dict]:
        """Insert event rows (dedup via UNIQUE (chain, tx_hash, log_index)).

        Returns the subset that was newly inserted (i.e. not already in DB).
        Caller can use the returned list to decide what to push to Lark.
        """
        if not rows:
            return []
        inserted: list[dict] = []
        with self._lock, self._conn:
            for r in rows:
                dup = self._conn.execute(
                    "SELECT 1 FROM events WHERE chain=? AND tx_hash=? AND log_index=?",
                    (r["chain"], r["tx_hash"], int(r["log_index"])),
                ).fetchone()
                if dup:
                    continue
                self._conn.execute(
                    """INSERT INTO events (
                        ts, chain, contract, contract_role, event, level,
                        block_number, tx_hash, log_index,
                        old_value, new_value, extra
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        float(r["ts"]),
                        r["chain"],
                        r["contract"],
                        r.get("contract_role"),
                        r["event"],
                        r.get("level"),
                        int(r["block_number"]),
                        r["tx_hash"],
                        int(r["log_index"]),
                        r.get("old_value"),
                        r.get("new_value"),
                        json.dumps(r.get("extra") or {}),
                    ),
                )
                inserted.append(r)
        return inserted

    def get_event_cursor(self, chain: str, contract: str) -> int | None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT last_block FROM event_cursors WHERE chain=? AND contract=?",
                (chain, contract),
            ).fetchone()
        return int(row[0]) if row else None

    def set_event_cursor(self, chain: str, contract: str, last_block: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO event_cursors (chain, contract, last_block, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(chain, contract) DO UPDATE SET
                       last_block = excluded.last_block,
                       updated_at = excluded.updated_at""",
                (chain, contract, int(last_block), time.time()),
            )

    def recent_events(
        self,
        chain: str | None = None,
        contract: str | None = None,
        hours: float = 24.0,
        limit: int = 100,
    ) -> list[dict]:
        since = time.time() - max(0.1, hours) * 3600.0
        clauses = ["ts >= ?"]
        params: list = [since]
        if chain:
            clauses.append("chain = ?")
            params.append(chain)
        if contract:
            clauses.append("contract = ?")
            params.append(contract)
        params.append(int(limit))
        sql = f"""
            SELECT ts, chain, contract, contract_role, event, level,
                   block_number, tx_hash, log_index, old_value, new_value, extra
            FROM events
            WHERE {' AND '.join(clauses)}
            ORDER BY ts DESC
            LIMIT ?
        """
        with self._lock, self._conn:
            cur = self._conn.execute(sql, tuple(params))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            r["extra"] = _parse_metrics(r.get("extra"))
        return rows

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _parse_metrics(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}

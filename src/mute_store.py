"""Pool / rule mute store.

File-based (`data/mutes.yaml`) so mutes survive restarts and can be edited by hand.
Supports per-pool or per-(pool, rule) muting with optional expiration.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .logger import log


@dataclass
class Mute:
    pool_key: str
    rule: str | None = None          # None = match all rules for this pool
    until: float | None = None       # unix ts; None = forever
    reason: str = ""
    muted_at: float = field(default_factory=time.time)

    def is_active(self, now: float | None = None) -> bool:
        if self.until is None:
            return True
        return self.until > (now or time.time())

    def matches(self, pool_key: str, rule: str) -> bool:
        if self.pool_key != pool_key:
            return False
        if self.rule is not None and self.rule != rule:
            return False
        return True

    def human_until(self) -> str:
        if self.until is None:
            return "永久"
        delta = self.until - time.time()
        if delta <= 0:
            return "已过期"
        if delta >= 86400:
            return f"{delta / 86400:.1f} 天后"
        if delta >= 3600:
            return f"{delta / 3600:.1f} 小时后"
        return f"{delta / 60:.0f} 分钟后"


def parse_duration(s: str | None) -> int | None:
    """'30s', '5m', '2h', '7d' → seconds. None → None (forever)."""
    if not s:
        return None
    s = s.strip().lower()
    if s.endswith("d"):
        return int(float(s[:-1]) * 86400)
    if s.endswith("h"):
        return int(float(s[:-1]) * 3600)
    if s.endswith("m"):
        return int(float(s[:-1]) * 60)
    if s.endswith("s"):
        return int(float(s[:-1]))
    return int(s)


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse_iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


class MuteStore:
    def __init__(self, path: str = "data/mutes.yaml"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.mutes: list[Mute] = []
        self.load()

    # --------- persistence ---------
    def load(self) -> None:
        if not self.path.exists():
            self.mutes = []
            return
        try:
            with open(self.path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("mutes.yaml 解析失败,忽略: %s", exc)
            self.mutes = []
            return
        raw = data.get("muted", []) or []
        self.mutes = []
        for entry in raw:
            if not isinstance(entry, dict) or "pool_key" not in entry:
                continue
            self.mutes.append(
                Mute(
                    pool_key=entry["pool_key"],
                    rule=entry.get("rule"),
                    until=_parse_iso(entry.get("until")),
                    reason=entry.get("reason", "") or "",
                    muted_at=_parse_iso(entry.get("muted_at")) or time.time(),
                )
            )

    def save(self) -> None:
        data = {"muted": []}
        for m in self.mutes:
            entry: dict = {"pool_key": m.pool_key}
            if m.rule:
                entry["rule"] = m.rule
            if m.until:
                entry["until"] = _iso(m.until)
            if m.reason:
                entry["reason"] = m.reason
            entry["muted_at"] = _iso(m.muted_at)
            data["muted"].append(entry)
        with open(self.path, "w") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

    # --------- mutation ---------
    def prune_expired(self) -> int:
        before = len(self.mutes)
        now = time.time()
        self.mutes = [m for m in self.mutes if m.is_active(now)]
        removed = before - len(self.mutes)
        if removed:
            self.save()
            log.info("已清理 %d 条过期 mute", removed)
        return removed

    def add(
        self,
        pool_key: str,
        rule: str | None = None,
        duration_sec: int | None = None,
        reason: str = "",
    ) -> Mute:
        # 同 (pool, rule) 已存在则覆盖
        self.mutes = [
            m for m in self.mutes
            if not (m.pool_key == pool_key and m.rule == rule)
        ]
        until = time.time() + duration_sec if duration_sec else None
        mute = Mute(
            pool_key=pool_key, rule=rule, until=until, reason=reason,
            muted_at=time.time(),
        )
        self.mutes.append(mute)
        self.save()
        return mute

    def remove(self, pool_key: str, rule: str | None = None) -> int:
        """Remove mute(s). If rule is None, remove all mutes on pool."""
        before = len(self.mutes)
        if rule is None:
            self.mutes = [m for m in self.mutes if m.pool_key != pool_key]
        else:
            self.mutes = [
                m for m in self.mutes
                if not (m.pool_key == pool_key and m.rule == rule)
            ]
        removed = before - len(self.mutes)
        if removed:
            self.save()
        return removed

    # --------- query ---------
    def find(self, pool_key: str, rule: str) -> Mute | None:
        self.prune_expired()
        for m in self.mutes:
            if m.matches(pool_key, rule):
                return m
        return None

    def list_active(self) -> list[Mute]:
        self.prune_expired()
        return list(self.mutes)

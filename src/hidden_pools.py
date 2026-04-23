"""Hidden pool store — pools the user explicitly removed from the UI.

File-based at `data/hidden_pools.yaml`. A hidden pool:
  - is skipped by ChainWorker.tick (no new snapshots written, no rule eval,
    no alerts pushed)
  - is filtered out of /api/pools in the web backend (doesn't show in the
    sidebar tree)
  - existing historical rows in SQLite are preserved — "restore" un-hides
    it and data resumes.

Separate from MuteStore: mute silences Lark only; hide stops updates
entirely and takes it off the UI. Deleting a pool in the UI writes here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .logger import log


@dataclass
class HiddenPool:
    pool_key: str
    reason: str = ""
    hidden_at: float = field(default_factory=time.time)


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse_iso(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:  # noqa: BLE001
        return None


class HiddenPoolStore:
    def __init__(self, path: str = "data/hidden_pools.yaml"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.hidden: list[HiddenPool] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.hidden = []
            return
        try:
            with open(self.path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("hidden_pools.yaml 解析失败,忽略: %s", exc)
            self.hidden = []
            return
        raw = data.get("hidden", []) or []
        self.hidden = []
        for entry in raw:
            if not isinstance(entry, dict) or "pool_key" not in entry:
                continue
            self.hidden.append(
                HiddenPool(
                    pool_key=entry["pool_key"],
                    reason=entry.get("reason", "") or "",
                    hidden_at=_parse_iso(entry.get("hidden_at")) or time.time(),
                )
            )

    def save(self) -> None:
        data = {"hidden": []}
        for h in self.hidden:
            entry: dict = {"pool_key": h.pool_key}
            if h.reason:
                entry["reason"] = h.reason
            entry["hidden_at"] = _iso(h.hidden_at)
            data["hidden"].append(entry)
        with open(self.path, "w") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

    # --------- mutation ---------
    def add(self, pool_key: str, reason: str = "") -> HiddenPool:
        # overwrite existing entry
        self.hidden = [h for h in self.hidden if h.pool_key != pool_key]
        entry = HiddenPool(pool_key=pool_key, reason=reason, hidden_at=time.time())
        self.hidden.append(entry)
        self.save()
        return entry

    def remove(self, pool_key: str) -> bool:
        before = len(self.hidden)
        self.hidden = [h for h in self.hidden if h.pool_key != pool_key]
        changed = len(self.hidden) < before
        if changed:
            self.save()
        return changed

    # --------- query ---------
    def is_hidden(self, pool_key: str) -> bool:
        # lightweight, no I/O
        return any(h.pool_key == pool_key for h in self.hidden)

    def reload_if_changed(self) -> None:
        """Cheap reload — call before checks from a long-running loop so
        updates from another process (e.g. CLI or a different API call)
        take effect without restart."""
        self.load()

    def hidden_set(self) -> set[str]:
        return {h.pool_key for h in self.hidden}

    def list_all(self) -> list[HiddenPool]:
        return list(self.hidden)

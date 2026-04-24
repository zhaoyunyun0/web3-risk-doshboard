"""关注地址清单(Address Watchlist)。

持久化 `data/address_watchlist.yaml`,记录被盯梢的链上地址 + 人类可读标签 +
告警模式。前端活动/持仓/告警页把地址显示为标签名(如 `Binance Hot #3`),
LargeTransferScanner 按 watch_mode 对匹配地址产出额外的"watched_address"告警。

支持三种 watch_mode:
  - tag_only:          仅标签显示,不产告警(默认)
  - alert_any:         该地址任何 Supply/Withdraw/Borrow/Repay 都告警
  - alert_threshold:   金额 >= threshold_usd 才告警

和 MuteStore 一样,文件 mtime 变化会被 reload(跨进程同步:Web 写,监控读)。
地址统一以 lowercase 存储和查询,避免 checksum 大小写问题。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .logger import log


WATCH_MODES = ("tag_only", "alert_any", "alert_threshold")


@dataclass
class AddressEntry:
    address: str                    # lowercase 0x...
    label: str                      # 简短名(必填)
    tags: list[str] = field(default_factory=list)
    note: str = ""
    watch_mode: str = "tag_only"
    threshold_usd: float = 0.0
    added_at: float = field(default_factory=time.time)

    def to_yaml_entry(self) -> dict:
        entry: dict = {
            "address": self.address,
            "label": self.label,
        }
        if self.tags:
            entry["tags"] = list(self.tags)
        if self.note:
            entry["note"] = self.note
        if self.watch_mode != "tag_only":
            entry["watch_mode"] = self.watch_mode
        if self.threshold_usd > 0:
            entry["threshold_usd"] = self.threshold_usd
        entry["added_at"] = _iso(self.added_at)
        return entry

    def to_dict(self) -> dict:
        """给 HTTP API 用(human 友好)。"""
        return {
            "address": self.address,
            "label": self.label,
            "tags": list(self.tags),
            "note": self.note,
            "watch_mode": self.watch_mode,
            "threshold_usd": float(self.threshold_usd),
            "added_at": self.added_at,
            "added_at_iso": _iso(self.added_at),
        }


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        return None


def _parse_iso(s) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)).timestamp()
    except Exception:  # noqa: BLE001
        return None


def _normalize(addr: str | None) -> str | None:
    if not addr:
        return None
    s = str(addr).strip().lower()
    if not s.startswith("0x"):
        s = "0x" + s
    # 基本 format 校验(以 0x 开头 + 40 hex)
    if len(s) != 42:
        return None
    return s


class AddressWatchlist:
    def __init__(self, path: str = "data/address_watchlist.yaml") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: list[AddressEntry] = []
        self._last_mtime: float = 0.0
        self.load()

    # --------- persistence ---------
    def load(self) -> None:
        if not self.path.exists():
            self.entries = []
            self._last_mtime = 0.0
            return
        try:
            self._last_mtime = self.path.stat().st_mtime
            with open(self.path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("address_watchlist.yaml 解析失败,忽略: %s", exc)
            self.entries = []
            return
        raw = data.get("addresses", []) or []
        self.entries = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            addr = _normalize(entry.get("address"))
            if not addr:
                continue
            mode = str(entry.get("watch_mode") or "tag_only")
            if mode not in WATCH_MODES:
                mode = "tag_only"
            self.entries.append(
                AddressEntry(
                    address=addr,
                    label=str(entry.get("label") or "")[:64] or addr[:10],
                    tags=[str(x) for x in (entry.get("tags") or [])],
                    note=str(entry.get("note") or "")[:500],
                    watch_mode=mode,
                    threshold_usd=float(entry.get("threshold_usd") or 0),
                    added_at=_parse_iso(entry.get("added_at")) or time.time(),
                )
            )

    def save(self) -> None:
        data = {"addresses": [e.to_yaml_entry() for e in self.entries]}
        with open(self.path, "w") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        try:
            self._last_mtime = self.path.stat().st_mtime
        except OSError:
            pass

    def reload_if_changed(self) -> bool:
        if not self.path.exists():
            if self._last_mtime != 0.0:
                self.entries = []
                self._last_mtime = 0.0
                return True
            return False
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return False
        if mtime == self._last_mtime:
            return False
        old_count = len(self.entries)
        self.load()
        log.info(
            "address_watchlist.yaml 检测到外部改动,已重新加载 (%d → %d 条)",
            old_count, len(self.entries),
        )
        return True

    # --------- CRUD ---------
    def add(
        self,
        address: str,
        label: str,
        *,
        tags: list[str] | None = None,
        note: str = "",
        watch_mode: str = "tag_only",
        threshold_usd: float = 0.0,
    ) -> AddressEntry | None:
        addr = _normalize(address)
        if not addr:
            return None
        if watch_mode not in WATCH_MODES:
            watch_mode = "tag_only"
        # 已存在就覆盖(按地址唯一)
        self.entries = [e for e in self.entries if e.address != addr]
        entry = AddressEntry(
            address=addr,
            label=(label or addr[:10]).strip()[:64],
            tags=[t.strip() for t in (tags or []) if t.strip()],
            note=(note or "").strip()[:500],
            watch_mode=watch_mode,
            threshold_usd=max(0.0, float(threshold_usd or 0)),
            added_at=time.time(),
        )
        self.entries.append(entry)
        self.save()
        return entry

    def update(
        self,
        address: str,
        *,
        label: str | None = None,
        tags: list[str] | None = None,
        note: str | None = None,
        watch_mode: str | None = None,
        threshold_usd: float | None = None,
    ) -> AddressEntry | None:
        addr = _normalize(address)
        if not addr:
            return None
        for e in self.entries:
            if e.address == addr:
                if label is not None:
                    e.label = label.strip()[:64] or e.label
                if tags is not None:
                    e.tags = [t.strip() for t in tags if t.strip()]
                if note is not None:
                    e.note = note.strip()[:500]
                if watch_mode is not None and watch_mode in WATCH_MODES:
                    e.watch_mode = watch_mode
                if threshold_usd is not None:
                    e.threshold_usd = max(0.0, float(threshold_usd))
                self.save()
                return e
        return None

    def remove(self, address: str) -> int:
        addr = _normalize(address)
        if not addr:
            return 0
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.address != addr]
        removed = before - len(self.entries)
        if removed:
            self.save()
        return removed

    # --------- query ---------
    def find(self, address: str) -> AddressEntry | None:
        self.reload_if_changed()
        addr = _normalize(address)
        if not addr:
            return None
        for e in self.entries:
            if e.address == addr:
                return e
        return None

    def list_all(self) -> list[AddressEntry]:
        self.reload_if_changed()
        return list(self.entries)

    def label_map(self) -> dict[str, str]:
        """地址(lower) -> label,前端批量反查用。"""
        self.reload_if_changed()
        return {e.address: e.label for e in self.entries}

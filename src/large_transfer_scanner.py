"""大额单笔转账告警扫描器(Track C)。

与 Track A(快照规则)/ Track B(权限事件)并行:每 tick 扫 Pool 合约的
Supply/Withdraw/Borrow/Repay 事件,对每笔按 USD 金额和占池 supply 百分比
两个维度检查阈值;超阈值产出 Alert,通过 main.py 既有 alert 路径推 Lark
+ 入 SQLite。

默认只对"流出类"(Withdraw/Borrow)告警 — 资金跑出池子才是风险入口。

游标复用 sqlite_sink.event_cursors(contract 名用 `{pool_addr}#activity`
和现有 PoolProxy 游标区分)。首次启动回看 `lookback_blocks` 避免刷屏。

同笔 tx 去重:内存保留 (tx_hash, log_index, rule_name) 最近 24h。
"""
from __future__ import annotations

import time
from typing import Any

from web3 import AsyncWeb3
from web3.types import LogReceipt

from .aave_v3_collector import ReserveSnapshot
from .events import AAVE_POOL_EVENTS_ABI, POOL_EVENT_TOPIC0
from .logger import log
from .rpc_pool import RpcPool
from .rule_engine import Alert
from .sqlite_sink import SqliteSink
from .web.on_demand import _decode_log_with_abi, get_logs_paginated


# 活动事件里 reserve 字段的名字因事件不同:
#   Supply/Withdraw/Borrow/Repay: "reserve"
#   LiquidationCall: collateralAsset / debtAsset(本 scanner 不处理)
_RESERVE_FIELD = "reserve"


class LargeTransferScanner:
    """扫大额单笔转账。"""

    def __init__(
        self,
        chain: str,
        rpc_pool: RpcPool,
        sink: SqliteSink | None,
        pool_addr: str | None,
        rules: dict | None,
        protocol: str = "aave_v3",
        lookback_blocks: int = 300,
    ) -> None:
        self.chain = chain
        self.protocol = protocol
        self.rpc_pool = rpc_pool
        self.sink = sink
        self.pool_addr = AsyncWeb3.to_checksum_address(pool_addr) if pool_addr else None
        self.rules = rules or {}
        self.lookback_blocks = lookback_blocks
        # dedup 记录:(tx_hash, log_index, rule_name) → 首次告警 ts
        self._fired: dict[tuple[str, int, str], float] = {}
        # 24h 滚动窗口清理(避免内存泄漏)
        self._dedup_window = 24 * 3600
        # 游标 key:与 event_tracker 的 PoolProxy cursor 用不同后缀区分
        self._cursor_key: str | None = (
            f"{self.pool_addr}#activity" if self.pool_addr else None
        )

    def _prune_dedup(self, now: float) -> None:
        expired = [k for k, ts in self._fired.items() if now - ts > self._dedup_window]
        for k in expired:
            self._fired.pop(k, None)

    async def tick(self, reserves: list[ReserveSnapshot]) -> list[Alert]:
        """扫增量活动事件,返回超阈值的 Alert 列表。

        reserves: 当前 tick 的 Pool 快照(含各 reserve 的 asset 地址/symbol/
        price_usd/supply_usd/decimals),用于反查 address → 元数据,并算
        amount_usd 和占池百分比。
        """
        if not self.pool_addr or not self.rules or self.sink is None:
            return []

        event_types: list[str] = list(self.rules.get("event_types") or [])
        if not event_types:
            return []

        # 只扫 rules 里声明的 event types 对应的 topic0,节省 RPC 带宽
        topic0_list = [POOL_EVENT_TOPIC0[n] for n in event_types if n in POOL_EVENT_TOPIC0]
        if not topic0_list:
            log.warning(
                "large_transfer: no valid event_types in rules for chain=%s (%s)",
                self.chain, event_types,
            )
            return []

        # 拿最新 block
        async def _bn(w3: AsyncWeb3) -> int:
            return await w3.eth.block_number
        try:
            latest = int(await self.rpc_pool.execute(_bn, method_label="block_number.large_tx"))
        except Exception as exc:  # noqa: BLE001
            log.warning("large_transfer block_number failed chain=%s: %s", self.chain, exc)
            return []

        cursor = self.sink.get_event_cursor(self.chain, self._cursor_key)
        first_run = cursor is None
        if first_run:
            cursor = max(0, latest - self.lookback_blocks)
            log.info(
                "large_transfer first run chain=%s pool=%s from_block=%d latest=%d",
                self.chain, self.pool_addr, cursor + 1, latest,
            )
        if cursor >= latest:
            return []

        # reserve address(lowercase) → meta (symbol/price_usd/supply_usd/decimals)
        meta_by_addr: dict[str, dict[str, Any]] = {}
        for s in reserves:
            if not s.asset:
                continue
            meta_by_addr[s.asset.lower()] = {
                "symbol": s.symbol,
                "decimals": s.decimals,
                "price_usd": s.price_usd,
                "supply_usd": s.supply_usd,
            }

        base_params = {"address": self.pool_addr, "topics": [topic0_list]}
        try:
            logs = await get_logs_paginated(
                self.rpc_pool, base_params, cursor + 1, latest,
                method_label="eth_getLogs.large_tx",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("large_transfer get_logs failed chain=%s: %s", self.chain, exc)
            return []

        # 推进游标(即便没命中阈值也要推,避免下次重扫)
        self.sink.set_event_cursor(self.chain, self._cursor_key, latest)

        if not logs:
            return []

        now = time.time()
        self._prune_dedup(now)
        alerts: list[Alert] = []

        usd_thresholds = self.rules.get("usd_thresholds") or []
        pct_cfg = self.rules.get("pct_thresholds") or {}
        pct_enable_below = float(pct_cfg.get("enable_if_supply_below_usd") or 0)
        pct_rules = pct_cfg.get("rules") or []

        for l in logs:
            name, args = _decode_log_with_abi(AAVE_POOL_EVENTS_ABI, l)
            if name is None or name not in event_types:
                continue
            reserve_addr = args.get(_RESERVE_FIELD)
            if not reserve_addr:
                continue
            meta = meta_by_addr.get(str(reserve_addr).lower())
            if meta is None:
                # 事件里的 reserve 不在 watchlist 里(未监控该 reserve),跳过
                continue

            amount_raw = int(args.get("amount") or 0)
            if amount_raw <= 0:
                continue
            decimals = int(meta["decimals"])
            price_usd = float(meta["price_usd"] or 0.0)
            amount_token = amount_raw / (10 ** decimals)
            amount_usd = amount_token * price_usd
            if amount_usd <= 0:
                continue

            pool_supply_usd = float(meta["supply_usd"] or 0.0)
            pct_of_pool = (
                (amount_usd / pool_supply_usd * 100.0) if pool_supply_usd > 0 else 0.0
            )

            tx_hash_raw = l["transactionHash"]
            tx_hash = (
                tx_hash_raw.hex() if hasattr(tx_hash_raw, "hex") else str(tx_hash_raw)
            )
            if not tx_hash.startswith("0x"):
                tx_hash = "0x" + tx_hash
            log_index = int(l.get("logIndex") or 0)
            block_number = int(l.get("blockNumber") or 0)
            user_addr = args.get("user") or args.get("onBehalfOf") or args.get("to") or ""

            # 收集命中的规则 — 按 USD 和 pct 两维分别跑
            hits: list[tuple[str, str]] = []  # (rule_name, level)
            for r in usd_thresholds:
                threshold_usd = float(r.get("usd") or 0)
                if threshold_usd > 0 and amount_usd >= threshold_usd:
                    hits.append((str(r["name"]), str(r.get("level") or "warning")))
            if pct_rules and (pct_enable_below <= 0 or pool_supply_usd < pct_enable_below):
                for r in pct_rules:
                    threshold_pct = float(r.get("pct") or 0)
                    if threshold_pct > 0 and pct_of_pool >= threshold_pct:
                        hits.append((str(r["name"]), str(r.get("level") or "warning")))

            # 对多个命中规则,只发最高级别的那条(避免 1M/5M/10M 同时发 3 条)
            if not hits:
                continue
            _LEVEL_RANK = {"info": 0, "warning": 1, "alert": 2, "critical": 3}
            hits.sort(key=lambda x: _LEVEL_RANK.get(x[1], 0), reverse=True)
            rule_name, rule_level = hits[0]

            dedup_key = (tx_hash, log_index, rule_name)
            if dedup_key in self._fired:
                continue
            self._fired[dedup_key] = now

            pool_key = f"{self.chain}:{self.protocol}:{meta['symbol']}"
            msg = (
                f"{self.chain}/{self.protocol} **{meta['symbol']}** 单笔 "
                f"**{name}** ${amount_usd:,.0f} (占池 {pct_of_pool:.2f}%) "
                f"from `{user_addr[:10]}...{user_addr[-6:]}` tx {tx_hash[:10]}...{tx_hash[-6:]}"
                if len(str(user_addr)) >= 16 else
                f"{self.chain}/{self.protocol} **{meta['symbol']}** 单笔 "
                f"**{name}** ${amount_usd:,.0f} (占池 {pct_of_pool:.2f}%)"
            )
            metrics = {
                "event": name,
                "amount_usd": amount_usd,
                "amount_token": amount_token,
                "pct_of_pool_supply": pct_of_pool,
                "pool_supply_usd": pool_supply_usd,
                "user": user_addr,
                "tx_hash": tx_hash,
                "block": block_number,
                "log_index": log_index,
            }
            alerts.append(
                Alert(
                    level=rule_level,
                    rule=rule_name,
                    pool_key=pool_key,
                    chain=self.chain,
                    protocol=self.protocol,
                    symbol=meta["symbol"],
                    message=msg,
                    metrics=metrics,
                    timestamp=now,
                )
            )

        if alerts:
            log.info(
                "large_transfer chain=%s new_alerts=%d (scanned %d logs)",
                self.chain, len(alerts), len(logs),
            )
        return alerts

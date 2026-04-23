"""Track B: periodic event-log polling for Aave permission + proxy events.

One EventTracker per chain. Each tick:
  1. Read cursor from sqlite (or default to latest - lookback on first run).
  2. eth_getLogs in (cursor, latest] with topic0 union for the contract.
  3. Decode logs, write to `events` table (UNIQUE dedupe).
  4. Advance cursor to `latest`.
  5. Return the newly-inserted rows so the caller can push Lark alerts.
"""
from __future__ import annotations

import asyncio
import time

from web3 import AsyncWeb3

from .events import (
    PERMISSION_EVENT_TOPIC0,
    PERMISSION_EVENT_ZH,
    POOL_ADDRESSES_PROVIDER_EVENTS_ABI,
    PROXY_EVENT_TOPIC0,
    PROXY_EVENTS_ABI,
    TOPIC0_TO_PERMISSION_EVENT,
    TOPIC0_TO_PROXY_EVENT,
)
from .logger import log
from .rpc_pool import RpcPool
from .sqlite_sink import SqliteSink
from .web.on_demand import _decode_log_with_abi, get_logs_paginated


# First-run backfill window. Permission events are rare, so we cast a wide
# net (~1 week on Ethereum). L2 block times are 10-50× faster, so a full
# 1-week lookback would be millions of blocks (thousands of paginated
# eth_getLogs calls) and hammer public RPCs on startup — we cap those at
# ~50k blocks to keep first-run under ~100 pages per chain. Backfilled
# events are stored but NOT pushed to Lark (avoids restart spam).
DEFAULT_LOOKBACK_BLOCKS = {
    "ethereum": 50_400,   # 12s → 7 days
    "arbitrum": 50_000,   # ~0.25s → ~3.5h (capped for startup latency)
    "optimism": 50_000,   # 2s → ~28h (capped)
    "base": 50_000,       # 2s → ~28h (capped)
    "polygon": 50_000,    # ~2.2s → ~31h (capped)
    "bnb": 50_000,        # 3s → ~42h (capped)
}

# Defaults if rules.yaml doesn't override them (PRD FR-06 defaults).
DEFAULT_EVENT_LEVELS: dict[str, str] = {
    # PoolAddressesProvider-level permission events
    "OwnershipTransferred":    "alert",
    "PoolUpdated":             "alert",
    "PoolConfiguratorUpdated": "alert",
    "PriceOracleUpdated":      "alert",
    "ACLManagerUpdated":       "alert",
    "ACLAdminUpdated":         "alert",
    "PoolDataProviderUpdated": "warning",
    "AddressSet":              "warning",
    "AddressSetAsProxy":       "alert",
    "ProxyCreated":            "warning",
    # Pool proxy-level (ERC1967 + Pausable)
    "AdminChanged":            "alert",
    "Upgraded":                "alert",
    "BeaconUpgraded":          "alert",
    "Paused":                  "warning",
    "Unpaused":                "warning",
}


class EventTracker:
    def __init__(
        self,
        chain: str,
        rpc_pool: RpcPool,
        sink: SqliteSink,
        pap_addr: str | None,
        pool_addr: str | None,
        event_rules: dict | None = None,
        lookback_blocks: int | None = None,
    ):
        self.chain = chain
        self.rpc_pool = rpc_pool
        self.sink = sink
        self.pap_addr = AsyncWeb3.to_checksum_address(pap_addr) if pap_addr else None
        self.pool_addr = AsyncWeb3.to_checksum_address(pool_addr) if pool_addr else None

        event_rules = event_rules or {}
        self.event_levels: dict[str, str] = {
            **DEFAULT_EVENT_LEVELS,
            **(event_rules.get("levels") or {}),
        }
        self.lookback_blocks = (
            lookback_blocks
            or DEFAULT_LOOKBACK_BLOCKS.get(chain, 1800)
        )

    async def tick(self) -> list[dict]:
        """Poll permission + proxy events once. Returns newly-seen rows."""
        if not self.pap_addr and not self.pool_addr:
            return []

        async def _bn(w3: AsyncWeb3):
            return await w3.eth.block_number

        try:
            latest = int(await self.rpc_pool.execute(_bn, method_label="block_number.events"))
        except Exception as exc:  # noqa: BLE001
            log.warning("event tracker block_number failed chain=%s: %s", self.chain, exc)
            return []

        new_rows: list[dict] = []

        if self.pap_addr:
            new_rows.extend(
                await self._poll_contract(
                    contract=self.pap_addr,
                    role="PoolAddressesProvider",
                    topic0_map=PERMISSION_EVENT_TOPIC0,
                    abi=POOL_ADDRESSES_PROVIDER_EVENTS_ABI,
                    latest=latest,
                )
            )
        if self.pool_addr:
            new_rows.extend(
                await self._poll_contract(
                    contract=self.pool_addr,
                    role="PoolProxy",
                    topic0_map=PROXY_EVENT_TOPIC0,
                    abi=PROXY_EVENTS_ABI,
                    latest=latest,
                )
            )
        return new_rows

    async def _poll_contract(
        self,
        contract: str,
        role: str,
        topic0_map: dict[str, str],
        abi: list[dict],
        latest: int,
    ) -> list[dict]:
        cursor = self.sink.get_event_cursor(self.chain, contract)
        first_run = cursor is None
        if first_run:
            cursor = max(0, latest - self.lookback_blocks)
            log.info(
                "event tracker first run chain=%s role=%s from_block=%d latest=%d",
                self.chain, role, cursor + 1, latest,
            )
        if cursor >= latest:
            return []

        topic0_list = list(topic0_map.values())
        base_params = {"address": contract, "topics": [topic0_list]}

        try:
            logs = await get_logs_paginated(
                self.rpc_pool, base_params, cursor + 1, latest,
                method_label=f"eth_getLogs.track_b.{role}",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "event tracker getLogs failed chain=%s role=%s range=%d-%d: %s",
                self.chain, role, cursor + 1, latest, exc,
            )
            return []

        if not logs:
            self.sink.set_event_cursor(self.chain, contract, latest)
            return []

        block_set = {int(l["blockNumber"]) for l in logs}
        ts_map = await self._block_timestamps(block_set)

        rows: list[dict] = []
        for l in logs:
            name, args = _decode_log_with_abi(abi, l)
            if name is None:
                continue
            bn = int(l["blockNumber"])
            ts = ts_map.get(bn, time.time())
            txh = l["transactionHash"]
            txh = txh.hex() if hasattr(txh, "hex") else str(txh)
            if not txh.startswith("0x"):
                txh = "0x" + txh
            idx = int(l["logIndex"])

            old_val = (
                args.get("oldAddress")
                or args.get("oldImplementationAddress")
                or args.get("previousOwner")
                or args.get("previousAdmin")
            )
            new_val = (
                args.get("newAddress")
                or args.get("newImplementationAddress")
                or args.get("newOwner")
                or args.get("newAdmin")
                or args.get("proxyAddress")
                or args.get("implementation")
                or args.get("beacon")
                or args.get("account")
            )
            extra: dict = {}
            raw_id = args.get("id")
            if raw_id is not None:
                if isinstance(raw_id, (bytes, bytearray)):
                    try:
                        extra["id_str"] = raw_id.rstrip(b"\x00").decode("utf-8", errors="replace")
                    except Exception:  # noqa: BLE001
                        extra["id_str"] = None
                    extra["id_hex"] = "0x" + raw_id.hex()
                else:
                    extra["id_hex"] = str(raw_id)

            rows.append({
                "ts": ts,
                "chain": self.chain,
                "contract": contract,
                "contract_role": role,
                "event": name,
                "event_zh": PERMISSION_EVENT_ZH.get(name, name),
                "level": self.event_levels.get(name, "info"),
                "block_number": bn,
                "tx_hash": txh,
                "log_index": idx,
                "old_value": str(old_val) if old_val else None,
                "new_value": str(new_val) if new_val else None,
                "extra": extra,
            })

        newly_inserted = self.sink.write_events(rows)
        self.sink.set_event_cursor(self.chain, contract, latest)
        log.info(
            "event tracker chain=%s role=%s logs=%d new=%d cursor→%d",
            self.chain, role, len(logs), len(newly_inserted), latest,
        )
        # Suppress Lark spam on first run (we backfilled up to 1h of history —
        # those are cold events, not "just happened"). Store them, skip alerts.
        if first_run:
            return []
        return newly_inserted

    async def _block_timestamps(self, block_numbers: set[int]) -> dict[int, float]:
        if not block_numbers:
            return {}
        sem = asyncio.Semaphore(6)

        async def _one(bn: int) -> tuple[int, float | None]:
            async with sem:
                async def _call(w3: AsyncWeb3):
                    return await w3.eth.get_block(bn)
                try:
                    blk = await self.rpc_pool.execute(_call, method_label="eth_getBlockByNumber.events")
                    return bn, float(blk["timestamp"])
                except Exception as exc:  # noqa: BLE001
                    log.debug("get_block %d failed: %s", bn, exc)
                    return bn, None

        results = await asyncio.gather(*[_one(bn) for bn in block_numbers])
        return {bn: ts for bn, ts in results if ts is not None}

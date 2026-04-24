"""On-demand RPC queries for events (activity / holders / permissions).

All functions use the shared RpcPool (retry / rotation / circuit breaker).
eth_getLogs is paginated over ~1000 blocks because most public RPCs will
reject wider ranges.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from web3 import AsyncWeb3
from web3.types import LogReceipt

from ..events import (
    AAVE_POOL_EVENTS_ABI,
    EVENT_EN,
    EVENT_ZH,
    PERMISSION_EVENT_EN,
    PERMISSION_EVENT_TOPIC0,
    PERMISSION_EVENT_ZH,
    POOL_ADDRESSES_PROVIDER_EVENTS_ABI,
    POOL_EVENT_TOPIC0,
    TOPIC0_TO_PERMISSION_EVENT,
    TOPIC0_TO_POOL_EVENT,
)
from ..logger import log
from ..rpc_pool import RpcPool
from .permission_abis import (
    ACL_MANAGER_EVENT_EN,
    ACL_MANAGER_EVENT_ZH,
    ACL_MANAGER_EVENTS_ABI,
    ACL_MANAGER_TOPIC0,
    POOL_CONFIGURATOR_EVENT_EN,
    POOL_CONFIGURATOR_EVENT_ZH,
    POOL_CONFIGURATOR_EVENTS_ABI,
    POOL_CONFIGURATOR_TOPIC0,
    POOL_PROXY_EVENT_EN,
    POOL_PROXY_EVENT_ZH,
    POOL_PROXY_EVENTS_ABI,
    POOL_PROXY_TOPIC0,
    format_event_display,
    format_role_hash,
)


# ---------- chain-wide assumptions ----------
# Most EVM chains are ~12s block time; public getLogs typically caps at
# ~1000 blocks. We use a safer 500 page size for get_logs which also works
# with Ankr / LlamaRPC / drpc.
DEFAULT_PAGE_SIZE = 500
# Very rough blocks-per-hour for different chains (used for initial range
# estimation; we always read latest block from RPC first).
BLOCKS_PER_HOUR = {
    "ethereum": 300,      # 12s
    "arbitrum": 14_400,   # ~0.25s
    "optimism": 1_800,    # 2s
    "base": 1_800,        # 2s
    "polygon": 1_600,     # ~2.2s
    "bnb": 1_200,         # 3s
    "avaxc": 1_800,       # 2s (Avalanche C-Chain)
}


def _blocks_for_hours(chain: str, hours: float) -> int:
    bph = BLOCKS_PER_HOUR.get(chain, 1800)
    return int(bph * max(0.1, hours))


def _short(addr: str | None) -> str | None:
    if not addr:
        return None
    a = addr.lower()
    if len(a) < 10:
        return a
    return f"0x{a[2:6].upper()}...{a[-2:].upper()}"


# ---------- get_logs pagination ----------
async def get_logs_paginated(
    rpc_pool: RpcPool,
    base_params: dict,
    from_block: int,
    to_block: int,
    page_size: int = DEFAULT_PAGE_SIZE,
    method_label: str = "eth_getLogs",
) -> list[LogReceipt]:
    """Run eth_getLogs over [from_block, to_block] in chunks of page_size.
    `base_params` should contain at least address and topics; fromBlock /
    toBlock are injected per page. Returns the concatenated list of logs."""
    if from_block > to_block:
        return []

    out: list[LogReceipt] = []
    cur_from = from_block
    while cur_from <= to_block:
        cur_to = min(to_block, cur_from + page_size - 1)
        params = dict(base_params)
        params["fromBlock"] = cur_from
        params["toBlock"] = cur_to

        async def _call(w3: AsyncWeb3, p=params):
            return await w3.eth.get_logs(p)

        try:
            logs = await rpc_pool.execute(_call, method_label=method_label)
        except Exception as exc:  # noqa: BLE001
            # If a single page fails (range too wide, etc.), narrow and retry once.
            if page_size > 100:
                log.warning(
                    "get_logs page failed chain=%s range=%d-%d err=%s — retry narrower",
                    rpc_pool.chain, cur_from, cur_to, exc,
                )
                smaller = max(100, page_size // 2)
                sub = await get_logs_paginated(
                    rpc_pool, base_params, cur_from, cur_to,
                    page_size=smaller, method_label=method_label,
                )
                out.extend(sub)
                cur_from = cur_to + 1
                continue
            raise

        out.extend(logs)
        cur_from = cur_to + 1
    return out


# ---------- block timestamp cache ----------
async def _block_timestamps(
    rpc_pool: RpcPool, block_numbers: set[int]
) -> dict[int, float]:
    """Fetch timestamps for a set of blocks. Runs a few in parallel."""
    if not block_numbers:
        return {}

    sem = asyncio.Semaphore(6)

    async def _one(bn: int) -> tuple[int, float | None]:
        async with sem:
            async def _call(w3: AsyncWeb3):
                return await w3.eth.get_block(bn)
            try:
                blk = await rpc_pool.execute(_call, method_label="eth_getBlockByNumber")
                return bn, float(blk["timestamp"])
            except Exception as exc:  # noqa: BLE001
                log.debug("get_block %d failed: %s", bn, exc)
                return bn, None

    results = await asyncio.gather(*[_one(bn) for bn in block_numbers])
    return {bn: ts for bn, ts in results if ts is not None}


def _decode_log_with_abi(event_abi_list: list[dict], log_entry: LogReceipt) -> tuple[str | None, dict[str, Any]]:
    """Try each event ABI via process_log, return (name, args) on first match.
    Uses a throwaway AsyncWeb3 contract to get the event object (no RPC)."""
    topic0 = log_entry["topics"][0]
    if hasattr(topic0, "hex"):
        topic0_hex = "0x" + topic0.hex().removeprefix("0x").lower()
    else:
        topic0_hex = str(topic0).lower()

    w3 = AsyncWeb3()  # no provider needed for ABI decoding
    # checksum address is required by web3.Contract
    addr = log_entry["address"]
    if isinstance(addr, str):
        addr_cs = AsyncWeb3.to_checksum_address(addr)
    else:
        addr_cs = AsyncWeb3.to_checksum_address("0x" + addr.hex())
    contract = w3.eth.contract(address=addr_cs, abi=event_abi_list)

    for entry in event_abi_list:
        if entry.get("type") != "event":
            continue
        name = entry["name"]
        sig = _sig_for_abi_entry(entry)
        sig_topic = "0x" + AsyncWeb3.keccak(text=sig).hex().removeprefix("0x").lower()
        if sig_topic != topic0_hex:
            continue
        try:
            ev = getattr(contract.events, name)()
            parsed = ev.process_log(log_entry)
            return name, dict(parsed["args"])
        except Exception as exc:  # noqa: BLE001
            log.debug("process_log(%s) failed: %s", name, exc)
            return name, {}
    return None, {}


def _sig_for_abi_entry(entry: dict) -> str:
    types = ",".join(i["type"] for i in entry.get("inputs", []))
    return f"{entry['name']}({types})"


# ---------- pool activity ----------
async def fetch_pool_activity(
    rpc_pool: RpcPool,
    pool_addr: str,
    reserve_addr: str,
    hours: float,
    min_usd: float,
    price_usd: float,
    decimals: int,
) -> list[dict]:
    """Fetch the 5 Aave v3 activity events for a given reserve on `pool_addr`,
    filter to USD-threshold, return sorted by ts desc."""

    chain = rpc_pool.chain
    # latest block
    async def _bn(w3: AsyncWeb3):
        return await w3.eth.block_number
    latest = int(await rpc_pool.execute(_bn, method_label="block_number"))
    span = _blocks_for_hours(chain, hours)
    from_block = max(0, latest - span)
    to_block = latest

    pool_addr_cs = AsyncWeb3.to_checksum_address(pool_addr)
    reserve_addr_cs = AsyncWeb3.to_checksum_address(reserve_addr)
    reserve_topic = "0x" + reserve_addr_cs[2:].lower().rjust(64, "0")

    # topics[0] = one-of 5 event hashes; topics[1] = reserve (indexed, first indexed param)
    topic0_list = [
        POOL_EVENT_TOPIC0["Supply"],
        POOL_EVENT_TOPIC0["Withdraw"],
        POOL_EVENT_TOPIC0["Borrow"],
        POOL_EVENT_TOPIC0["Repay"],
        POOL_EVENT_TOPIC0["LiquidationCall"],
    ]

    base_params_main: dict = {
        "address": pool_addr_cs,
        "topics": [topic0_list, reserve_topic],
    }
    # LiquidationCall's topics[1] is the collateralAsset, which is still a good
    # pre-filter — this is the user's collateral on that reserve.

    # For LiquidationCall the reserve asset could instead be in topics[2] (debtAsset),
    # so also query by debt side: topics=[LiquidationCall, *, reserve].
    # We merge both result sets for LiquidationCall.
    base_params_liq_debt: dict = {
        "address": pool_addr_cs,
        "topics": [[POOL_EVENT_TOPIC0["LiquidationCall"]], None, reserve_topic],
    }

    logs_main, logs_liq_debt = await asyncio.gather(
        get_logs_paginated(rpc_pool, base_params_main, from_block, to_block,
                           method_label="eth_getLogs.pool_activity"),
        get_logs_paginated(rpc_pool, base_params_liq_debt, from_block, to_block,
                           method_label="eth_getLogs.pool_liquidation_debt"),
    )

    # de-dup (tx_hash, log_index)
    seen: set[tuple[str, int]] = set()
    all_logs: list[LogReceipt] = []
    for lst in (logs_main, logs_liq_debt):
        for l in lst:
            txh = l["transactionHash"].hex() if hasattr(l["transactionHash"], "hex") else str(l["transactionHash"])
            idx = int(l["logIndex"])
            key = (txh, idx)
            if key in seen:
                continue
            seen.add(key)
            all_logs.append(l)

    if not all_logs:
        return []

    # fetch block timestamps in bulk
    block_set = {int(l["blockNumber"]) for l in all_logs}
    ts_map = await _block_timestamps(rpc_pool, block_set)

    scale = 10 ** decimals
    price = float(price_usd or 0.0)
    out: list[dict] = []
    for l in all_logs:
        name, args = _decode_log_with_abi(AAVE_POOL_EVENTS_ABI, l)
        if name is None:
            continue

        # normalize amount — different events carry different keys
        amt_token = 0.0
        amt_raw = None
        if name in ("Supply", "Withdraw", "Borrow", "Repay"):
            amt_raw = args.get("amount")
        elif name == "LiquidationCall":
            amt_raw = args.get("debtToCover")
        if amt_raw is not None:
            try:
                amt_token = float(int(amt_raw)) / scale
            except Exception:
                amt_token = 0.0
        amt_usd = amt_token * price

        if amt_usd < float(min_usd or 0):
            continue

        bn = int(l["blockNumber"])
        ts = ts_map.get(bn)
        if ts is None:
            continue

        txh = l["transactionHash"].hex() if hasattr(l["transactionHash"], "hex") else str(l["transactionHash"])
        if not txh.startswith("0x"):
            txh = "0x" + txh

        # canonical user / onBehalfOf fields
        user = None
        on_behalf_of = None
        if name == "Supply":
            user = args.get("user")
            on_behalf_of = args.get("onBehalfOf")
        elif name == "Withdraw":
            user = args.get("user")
            on_behalf_of = args.get("to")
        elif name == "Borrow":
            user = args.get("user")
            on_behalf_of = args.get("onBehalfOf")
        elif name == "Repay":
            user = args.get("repayer")
            on_behalf_of = args.get("user")
        elif name == "LiquidationCall":
            user = args.get("liquidator")
            on_behalf_of = args.get("user")

        out.append({
            "ts": ts,
            "block": bn,
            "tx_hash": txh,
            "event": name,
            "event_zh": EVENT_ZH.get(name, name),
            "event_en": EVENT_EN.get(name, name),
            "user": user,
            "on_behalf_of": on_behalf_of,
            "amount_token": amt_token,
            "amount_usd": amt_usd,
        })

    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


# ---------- top holders by net flow ----------
async def fetch_top_holders_by_netflow(
    rpc_pool: RpcPool,
    pool_addr: str,
    reserve_addr: str,
    hours: float,
    price_usd: float,
    decimals: int,
) -> list[dict]:
    """Aggregate Supply & Withdraw events by user; rank by |net|."""
    chain = rpc_pool.chain

    async def _bn(w3: AsyncWeb3):
        return await w3.eth.block_number
    latest = int(await rpc_pool.execute(_bn, method_label="block_number"))
    span = _blocks_for_hours(chain, hours)
    from_block = max(0, latest - span)
    to_block = latest

    pool_addr_cs = AsyncWeb3.to_checksum_address(pool_addr)
    reserve_addr_cs = AsyncWeb3.to_checksum_address(reserve_addr)
    reserve_topic = "0x" + reserve_addr_cs[2:].lower().rjust(64, "0")

    topics = [
        [POOL_EVENT_TOPIC0["Supply"], POOL_EVENT_TOPIC0["Withdraw"]],
        reserve_topic,
    ]
    base_params = {"address": pool_addr_cs, "topics": topics}

    logs = await get_logs_paginated(
        rpc_pool, base_params, from_block, to_block,
        method_label="eth_getLogs.holders",
    )
    if not logs:
        return []

    scale = 10 ** decimals
    price = float(price_usd or 0.0)
    # user -> stats
    agg: dict[str, dict[str, Any]] = {}

    for l in logs:
        name, args = _decode_log_with_abi(AAVE_POOL_EVENTS_ABI, l)
        if name not in ("Supply", "Withdraw"):
            continue

        # canonical "user" attribution:
        #  Supply: onBehalfOf (the account whose position grows)
        #  Withdraw: user (the account whose position shrinks)
        if name == "Supply":
            addr = args.get("onBehalfOf") or args.get("user")
        else:
            addr = args.get("user")
        if not addr:
            continue
        addr = addr.lower()

        try:
            amt_token = float(int(args.get("amount") or 0)) / scale
        except Exception:
            amt_token = 0.0
        amt_usd = amt_token * price

        txh = l["transactionHash"].hex() if hasattr(l["transactionHash"], "hex") else str(l["transactionHash"])
        if not txh.startswith("0x"):
            txh = "0x" + txh

        entry = agg.setdefault(addr, {
            "supply_usd": 0.0,
            "withdraw_usd": 0.0,
            "txs": set(),
        })
        if name == "Supply":
            entry["supply_usd"] += amt_usd
        else:
            entry["withdraw_usd"] += amt_usd
        entry["txs"].add(txh)

    rows: list[dict] = []
    for addr, s in agg.items():
        net = s["supply_usd"] - s["withdraw_usd"]
        rows.append({
            "address": addr,
            "address_short": _short(addr),
            "net_usd": net,
            "supply_usd": s["supply_usd"],
            "withdraw_usd": s["withdraw_usd"],
            "tx_count": len(s["txs"]),
            "tag": None,
        })
    rows.sort(key=lambda r: abs(r["net_usd"]), reverse=True)
    top = rows[:20]
    for i, r in enumerate(top, start=1):
        r["rank"] = i
    return top


# ---------- permission events ----------
# 把每个合约的(地址, 角色名, ABI, 中文描述 map, topic0 map)打包到一张表里,
# 统一驱动扫描逻辑。这样加合约只要往 _permission_targets 加一行。
def _permission_targets(deployment: dict) -> list[tuple[str, str, list[dict], dict, dict, dict]]:
    """返回 [(addr_checksum, role, abi, zh_map, en_map, topic0_map), ...]。
    地址为 None 的合约会被过滤掉(resolver 没拿到时降级)。"""
    targets: list[tuple[str, str, list[dict], dict, dict, dict]] = []

    pap = deployment.get("pool_addresses_provider")
    if pap:
        targets.append((
            AsyncWeb3.to_checksum_address(pap),
            "PoolAddressesProvider",
            POOL_ADDRESSES_PROVIDER_EVENTS_ABI,
            PERMISSION_EVENT_ZH,
            PERMISSION_EVENT_EN,
            PERMISSION_EVENT_TOPIC0,
        ))

    pool_cfg = deployment.get("pool_configurator")
    if pool_cfg:
        targets.append((
            AsyncWeb3.to_checksum_address(pool_cfg),
            "PoolConfigurator",
            POOL_CONFIGURATOR_EVENTS_ABI,
            POOL_CONFIGURATOR_EVENT_ZH,
            POOL_CONFIGURATOR_EVENT_EN,
            POOL_CONFIGURATOR_TOPIC0,
        ))

    acl = deployment.get("acl_manager")
    if acl:
        targets.append((
            AsyncWeb3.to_checksum_address(acl),
            "ACLManager",
            ACL_MANAGER_EVENTS_ABI,
            ACL_MANAGER_EVENT_ZH,
            ACL_MANAGER_EVENT_EN,
            ACL_MANAGER_TOPIC0,
        ))

    pool = deployment.get("pool")
    if pool:
        targets.append((
            AsyncWeb3.to_checksum_address(pool),
            "Pool",
            POOL_PROXY_EVENTS_ABI,
            POOL_PROXY_EVENT_ZH,
            POOL_PROXY_EVENT_EN,
            POOL_PROXY_TOPIC0,
        ))

    return targets


def _normalize_bytes32(raw) -> str | None:
    """bytes32 -> 0x-prefix lowercase hex string."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        return "0x" + raw.hex()
    s = str(raw).lower()
    if not s.startswith("0x"):
        s = "0x" + s
    return s


def _normalize_addr(val) -> str | None:
    if val is None:
        return None
    try:
        return AsyncWeb3.to_checksum_address(val)
    except Exception:  # noqa: BLE001
        return None


def _build_event_record(
    *, log_entry: LogReceipt, role: str, name: str, args: dict,
    zh_map: dict, en_map: dict, ts: float, bn: int, txh: str,
) -> dict:
    """把单条解码后事件转成前端 dict。按事件类型归一化
    old/new/asset/extra。返回 description_zh + description_en 双语描述,
    old_display_zh/_en 和 new_display_zh/_en 双语 display(前端按 state.lang
    选择)。"""
    contract = log_entry["address"]
    if isinstance(contract, (bytes, bytearray)):
        contract = "0x" + contract.hex()
    contract_cs = AsyncWeb3.to_checksum_address(contract)

    old_val: Any = None
    new_val: Any = None
    asset: str | None = None
    extra: dict[str, Any] = {}

    # ---------- 按 role 归一化 ----------
    if role == "PoolAddressesProvider":
        old_val = _normalize_addr(
            args.get("oldAddress")
            or args.get("oldImplementationAddress")
            or args.get("previousOwner")
        )
        new_val = _normalize_addr(
            args.get("newAddress")
            or args.get("newImplementationAddress")
            or args.get("newOwner")
            or args.get("proxyAddress")
        )
        if "id" in args and args["id"] is not None:
            raw = args["id"]
            if isinstance(raw, (bytes, bytearray)):
                try:
                    extra["id_str"] = raw.rstrip(b"\x00").decode("utf-8", errors="replace")
                except Exception:
                    extra["id_str"] = None
                extra["id_hex"] = "0x" + raw.hex()
            else:
                extra["id_hex"] = str(raw)
                extra["id_str"] = None

    elif role == "PoolConfigurator":
        asset = _normalize_addr(args.get("asset"))
        # old/new 两种形态: (a) 明确 oldX/newX 数值; (b) 单一 bool(enabled/frozen/...)
        # 按事件名分支处理,简单粗暴但好读。
        if name == "SupplyCapChanged":
            old_val = str(args.get("oldSupplyCap"))
            new_val = str(args.get("newSupplyCap"))
        elif name == "BorrowCapChanged":
            old_val = str(args.get("oldBorrowCap"))
            new_val = str(args.get("newBorrowCap"))
        elif name == "ReserveFactorChanged":
            old_val = str(args.get("oldReserveFactor"))
            new_val = str(args.get("newReserveFactor"))
        elif name == "LiquidationProtocolFeeChanged":
            old_val = str(args.get("oldFee"))
            new_val = str(args.get("newFee"))
        elif name == "DebtCeilingChanged":
            old_val = str(args.get("oldDebtCeiling"))
            new_val = str(args.get("newDebtCeiling"))
        elif name == "UnbackedMintCapChanged":
            old_val = str(args.get("oldUnbackedMintCap"))
            new_val = str(args.get("newUnbackedMintCap"))
        elif name == "ReserveInterestRateStrategyChanged":
            old_val = _normalize_addr(args.get("oldStrategy"))
            new_val = _normalize_addr(args.get("newStrategy"))
        elif name == "EModeAssetCategoryChanged":
            old_val = str(args.get("oldCategoryId"))
            new_val = str(args.get("newCategoryId"))
        elif name == "SiloedBorrowingChanged":
            old_val = str(args.get("oldState"))
            new_val = str(args.get("newState"))
        elif name in ("ReserveFrozen", "ReservePaused", "ReserveActive"):
            key = {"ReserveFrozen": "frozen", "ReservePaused": "paused", "ReserveActive": "active"}[name]
            new_val = str(args.get(key))
        elif name in ("ReserveBorrowing", "ReserveFlashLoaning"):
            new_val = str(args.get("enabled"))
        elif name == "BorrowableInIsolationChanged":
            new_val = str(args.get("borrowable"))
        elif name == "CollateralConfigurationChanged":
            extra["ltv"] = str(args.get("ltv"))
            extra["liquidationThreshold"] = str(args.get("liquidationThreshold"))
            extra["liquidationBonus"] = str(args.get("liquidationBonus"))
        elif name == "EModeCategoryAdded":
            asset = None
            extra["categoryId"] = str(args.get("categoryId"))
            extra["ltv"] = str(args.get("ltv"))
            extra["liquidationThreshold"] = str(args.get("liquidationThreshold"))
            extra["liquidationBonus"] = str(args.get("liquidationBonus"))
            extra["oracle"] = _normalize_addr(args.get("oracle"))
            extra["label"] = args.get("label")
        elif name == "ReserveInitialized":
            extra["aToken"] = _normalize_addr(args.get("aToken"))
            extra["stableDebtToken"] = _normalize_addr(args.get("stableDebtToken"))
            extra["variableDebtToken"] = _normalize_addr(args.get("variableDebtToken"))
            extra["interestRateStrategyAddress"] = _normalize_addr(args.get("interestRateStrategyAddress"))
        # ReserveDropped 只有 asset,无 old/new。

    elif role == "ACLManager":
        # RoleGranted/RoleRevoked: role(bytes32), account(addr), sender(addr)
        # RoleAdminChanged: role, previousAdminRole, newAdminRole (3 个 bytes32)
        extra["role"] = _normalize_bytes32(args.get("role"))
        if name in ("RoleGranted", "RoleRevoked"):
            extra["account"] = _normalize_addr(args.get("account"))
            extra["sender"] = _normalize_addr(args.get("sender"))
            # new_value 放 account,让老 UI 也能显示目标地址
            new_val = extra["account"]
        elif name == "RoleAdminChanged":
            old_val = _normalize_bytes32(args.get("previousAdminRole"))
            new_val = _normalize_bytes32(args.get("newAdminRole"))

    elif role == "Pool":
        if name == "Upgraded":
            new_val = _normalize_addr(args.get("implementation"))
        elif name == "AdminChanged":
            old_val = _normalize_addr(args.get("previousAdmin"))
            new_val = _normalize_addr(args.get("newAdmin"))

    out = {
        "ts": ts,
        "block": bn,
        "tx_hash": txh,
        "contract": contract_cs,
        "contract_role": role,
        "event": name,
        "description_zh": zh_map.get(name, name),
        "description_en": en_map.get(name, name),
        "asset": asset,
        "old_value": old_val,
        "new_value": new_val,
    }
    if extra:
        out["extra"] = extra

    # --- 人类可读 display 字段(前端按 state.lang 选 _zh 或 _en) ---
    od_zh, nd_zh, extra_display_zh = format_event_display(name, old_val, new_val, extra, lang="zh")
    od_en, nd_en, extra_display_en = format_event_display(name, old_val, new_val, extra, lang="en")
    if od_zh is not None:
        out["old_display_zh"] = od_zh
    if od_en is not None:
        out["old_display_en"] = od_en
    if nd_zh is not None:
        out["new_display_zh"] = nd_zh
    if nd_en is not None:
        out["new_display_en"] = nd_en
    if extra_display_zh:
        out["extra_display_zh"] = extra_display_zh
    if extra_display_en:
        out["extra_display_en"] = extra_display_en

    # 向后兼容旧字段名(指向 zh 版);前端应优先用 _zh/_en
    if od_zh is not None:
        out["old_display"] = od_zh
    if nd_zh is not None:
        out["new_display"] = nd_zh
    if extra_display_zh:
        out["extra_display"] = extra_display_zh

    # --- ACL: role bytes32 → 可读名(英文,无需翻) ---
    if role == "ACLManager" and extra and extra.get("role"):
        role_name = format_role_hash(extra.get("role"))
        if role_name:
            out.setdefault("extra_display_zh", {})["role_name"] = role_name
            out.setdefault("extra_display_en", {})["role_name"] = role_name
            out.setdefault("extra_display", {})["role_name"] = role_name

    return out


async def fetch_permission_events(
    rpc_pool: RpcPool,
    deployment: dict,
    hours: float,
) -> list[dict]:
    """扫 4 个合约的权限/参数变更事件并统一返回。

    deployment 至少要包含 `pool_addresses_provider`;其它可选:
    `pool_configurator` / `acl_manager` / `pool`。缺失的合约自动跳过。

    返回 list[dict],按时间倒序,统一字段见 _build_event_record。
    """
    chain = rpc_pool.chain

    async def _bn(w3: AsyncWeb3):
        return await w3.eth.block_number
    latest = int(await rpc_pool.execute(_bn, method_label="block_number"))
    span = _blocks_for_hours(chain, hours)
    from_block = max(0, latest - span)
    to_block = latest

    targets = _permission_targets(deployment)
    if not targets:
        return []

    # 所有合约的 topic0 合并成一张大 OR — 然后 eth_getLogs 用 address 数组
    # 一次拉下来,而不是 4 个串行请求。web3.py 的 AsyncEth.get_logs 支持
    # address: [addr1, addr2, ...]。
    addr_list = [t[0] for t in targets]
    all_topic0: list[str] = []
    for _addr, _role, _abi, _zh, _en, topic_map in targets:
        all_topic0.extend(topic_map.values())
    # 去重,避免同一 hash 出现多次(理论上每个事件签名 topic0 唯一,但保险)
    all_topic0 = list(dict.fromkeys(all_topic0))

    base_params = {"address": addr_list, "topics": [all_topic0]}

    logs = await get_logs_paginated(
        rpc_pool, base_params, from_block, to_block,
        method_label="eth_getLogs.permissions",
    )
    if not logs:
        return []

    # 建立 地址 -> (role, abi, zh_map, en_map) 的查表
    addr_to_target: dict[str, tuple[str, list[dict], dict, dict]] = {
        t[0].lower(): (t[1], t[2], t[3], t[4]) for t in targets
    }

    block_set = {int(l["blockNumber"]) for l in logs}
    ts_map = await _block_timestamps(rpc_pool, block_set)

    out: list[dict] = []
    for l in logs:
        raw_addr = l["address"]
        if isinstance(raw_addr, (bytes, bytearray)):
            addr_key = ("0x" + raw_addr.hex()).lower()
        else:
            addr_key = str(raw_addr).lower()
        found = addr_to_target.get(addr_key)
        if found is None:
            # 不该发生,保险跳过
            continue
        role, abi, zh_map, en_map = found

        name, args = _decode_log_with_abi(abi, l)
        if name is None:
            # 事件 topic0 命中但该合约 ABI 解不出来 — 跳过(不在我们关注列表)
            continue

        bn = int(l["blockNumber"])
        ts = ts_map.get(bn)
        if ts is None:
            continue

        txh = l["transactionHash"].hex() if hasattr(l["transactionHash"], "hex") else str(l["transactionHash"])
        if not txh.startswith("0x"):
            txh = "0x" + txh

        rec = _build_event_record(
            log_entry=l, role=role, name=name, args=args,
            zh_map=zh_map, en_map=en_map, ts=ts, bn=bn, txh=txh,
        )
        out.append(rec)

    out.sort(key=lambda x: x["ts"], reverse=True)
    return out

"""权限相关事件的 ABI + topic0 + 中文描述。

本模块为 Phase 2 新增 — 原本 `fetch_permission_events` 只扫
PoolAddressesProvider (PAP) 的事件,漏了 PoolConfigurator 里真正常动的
reserve 参数调整。这里把 3 个新合约的事件集中定义:

  PoolConfigurator   — 调 SupplyCap / BorrowCap / LTV / Frozen / 等
  ACLManager         — OZ AccessControl 三大事件 (RoleGranted/Revoked/AdminChanged)
  Pool (proxy)       — InitializableImmutableAdminUpgradeabilityProxy 升级事件

Topic0 在本文件导入时动态计算(Web3.keccak),避免手写 32 字节哈希出错。
"""
from __future__ import annotations

from web3 import Web3


# ---------- PoolConfigurator 事件 ABI ----------
# 参考 aave-v3-core/contracts/protocol/pool/PoolConfigurator.sol
# 只收录我们关心的"治理 / 参数变更"类事件。rebalance/mintedToTreasury 这类
# 运营类的不纳入(那是账本事件,不是权限变更)。
POOL_CONFIGURATOR_EVENTS_ABI = [
    {
        "anonymous": False, "type": "event", "name": "CollateralConfigurationChanged",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",                "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "ltv",                  "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "liquidationThreshold", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "liquidationBonus",     "type": "uint256"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "SupplyCapChanged",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",         "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "oldSupplyCap",  "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "newSupplyCap",  "type": "uint256"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "BorrowCapChanged",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",         "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "oldBorrowCap",  "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "newBorrowCap",  "type": "uint256"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "ReserveInterestRateStrategyChanged",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",       "type": "address"},
            {"indexed": False, "internalType": "address", "name": "oldStrategy", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "newStrategy", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "ReserveFactorChanged",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",            "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "oldReserveFactor", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "newReserveFactor", "type": "uint256"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "ReserveFrozen",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",  "type": "address"},
            {"indexed": False, "internalType": "bool",    "name": "frozen", "type": "bool"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "ReservePaused",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",  "type": "address"},
            {"indexed": False, "internalType": "bool",    "name": "paused", "type": "bool"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "ReserveActive",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",  "type": "address"},
            {"indexed": False, "internalType": "bool",    "name": "active", "type": "bool"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "ReserveBorrowing",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",   "type": "address"},
            {"indexed": False, "internalType": "bool",    "name": "enabled", "type": "bool"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "ReserveFlashLoaning",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",   "type": "address"},
            {"indexed": False, "internalType": "bool",    "name": "enabled", "type": "bool"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "LiquidationProtocolFeeChanged",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",  "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "oldFee", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "newFee", "type": "uint256"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "DebtCeilingChanged",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",           "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "oldDebtCeiling",  "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "newDebtCeiling",  "type": "uint256"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "BorrowableInIsolationChanged",
        # 注意: asset 参数在 v3.0.x 不是 indexed(官方 ABI 里 asset 没 indexed 标志)
        "inputs": [
            {"indexed": False, "internalType": "address", "name": "asset",      "type": "address"},
            {"indexed": False, "internalType": "bool",    "name": "borrowable", "type": "bool"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "EModeAssetCategoryChanged",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",          "type": "address"},
            {"indexed": False, "internalType": "uint8",   "name": "oldCategoryId",  "type": "uint8"},
            {"indexed": False, "internalType": "uint8",   "name": "newCategoryId",  "type": "uint8"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "EModeCategoryAdded",
        "inputs": [
            {"indexed": True,  "internalType": "uint8",   "name": "categoryId",            "type": "uint8"},
            {"indexed": False, "internalType": "uint256", "name": "ltv",                   "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "liquidationThreshold",  "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "liquidationBonus",      "type": "uint256"},
            {"indexed": False, "internalType": "address", "name": "oracle",                "type": "address"},
            {"indexed": False, "internalType": "string",  "name": "label",                 "type": "string"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "ReserveDropped",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "ReserveInitialized",
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "asset",                    "type": "address"},
            {"indexed": True, "internalType": "address", "name": "aToken",                   "type": "address"},
            {"indexed": True, "internalType": "address", "name": "stableDebtToken",          "type": "address"},
            {"indexed": False,"internalType": "address", "name": "variableDebtToken",        "type": "address"},
            {"indexed": False,"internalType": "address", "name": "interestRateStrategyAddress","type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "SiloedBorrowingChanged",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",    "type": "address"},
            {"indexed": False, "internalType": "bool",    "name": "oldState", "type": "bool"},
            {"indexed": False, "internalType": "bool",    "name": "newState", "type": "bool"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "UnbackedMintCapChanged",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "asset",              "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "oldUnbackedMintCap", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "newUnbackedMintCap", "type": "uint256"},
        ],
    },
]


# ---------- ACLManager 事件 ABI (OpenZeppelin AccessControl 标准) ----------
ACL_MANAGER_EVENTS_ABI = [
    {
        "anonymous": False, "type": "event", "name": "RoleGranted",
        "inputs": [
            {"indexed": True, "internalType": "bytes32", "name": "role",    "type": "bytes32"},
            {"indexed": True, "internalType": "address", "name": "account", "type": "address"},
            {"indexed": True, "internalType": "address", "name": "sender",  "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "RoleRevoked",
        "inputs": [
            {"indexed": True, "internalType": "bytes32", "name": "role",    "type": "bytes32"},
            {"indexed": True, "internalType": "address", "name": "account", "type": "address"},
            {"indexed": True, "internalType": "address", "name": "sender",  "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "RoleAdminChanged",
        "inputs": [
            {"indexed": True, "internalType": "bytes32", "name": "role",              "type": "bytes32"},
            {"indexed": True, "internalType": "bytes32", "name": "previousAdminRole", "type": "bytes32"},
            {"indexed": True, "internalType": "bytes32", "name": "newAdminRole",      "type": "bytes32"},
        ],
    },
]


# ---------- Pool Proxy (InitializableImmutableAdminUpgradeabilityProxy) ----------
# Pool 是个代理合约,PAP.setPoolImpl() 会调 proxy.upgradeToAndCall() 进而发
# Upgraded 事件。这是 pool 实现被替换的"核心信号",必扫。
POOL_PROXY_EVENTS_ABI = [
    {
        "anonymous": False, "type": "event", "name": "Upgraded",
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "implementation", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "AdminChanged",
        "inputs": [
            {"indexed": False, "internalType": "address", "name": "previousAdmin", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "newAdmin",      "type": "address"},
        ],
    },
]


# ---------- 签名字符串(用于 keccak 算 topic0) ----------
def _sig_for(entry: dict) -> str:
    types = ",".join(i["type"] for i in entry.get("inputs", []))
    return f"{entry['name']}({types})"


def _topic0(entry: dict) -> str:
    return "0x" + Web3.keccak(text=_sig_for(entry)).hex().removeprefix("0x").lower()


POOL_CONFIGURATOR_TOPIC0: dict[str, str] = {
    e["name"]: _topic0(e) for e in POOL_CONFIGURATOR_EVENTS_ABI
}
ACL_MANAGER_TOPIC0: dict[str, str] = {
    e["name"]: _topic0(e) for e in ACL_MANAGER_EVENTS_ABI
}
POOL_PROXY_TOPIC0: dict[str, str] = {
    e["name"]: _topic0(e) for e in POOL_PROXY_EVENTS_ABI
}


# ---------- 中文描述 (给前端"含义"列用) ----------
# 注意: PoolConfigurator 的 ReserveInitialized / CollateralConfigurationChanged /
# EModeCategoryAdded 是多值事件,单一 old→new 放不下,前端/后端按 extra 展示。
POOL_CONFIGURATOR_EVENT_ZH: dict[str, str] = {
    "CollateralConfigurationChanged": "调整抵押参数 (LTV / 清算门槛 / 清算奖金)",
    "SupplyCapChanged":                "调整供应上限 (Supply Cap)",
    "BorrowCapChanged":                "调整借款上限 (Borrow Cap)",
    "ReserveInterestRateStrategyChanged": "替换利率策略合约",
    "ReserveFactorChanged":            "调整储备因子 (Reserve Factor)",
    "ReserveFrozen":                   "冻结/解冻资产",
    "ReservePaused":                   "暂停/恢复资产",
    "ReserveActive":                   "启用/停用资产",
    "ReserveBorrowing":                "开启/关闭借款",
    "ReserveFlashLoaning":             "开启/关闭闪电贷",
    "LiquidationProtocolFeeChanged":   "调整清算协议费",
    "DebtCeilingChanged":              "调整隔离模式债务上限",
    "BorrowableInIsolationChanged":    "隔离模式下是否可借",
    "EModeAssetCategoryChanged":       "调整资产的 eMode 分类",
    "EModeCategoryAdded":              "新增 eMode 分类",
    "ReserveDropped":                  "移除资产",
    "ReserveInitialized":              "新增资产 (初始化 reserve)",
    "SiloedBorrowingChanged":          "独立借款模式切换",
    "UnbackedMintCapChanged":          "调整无担保铸造上限",
}

ACL_MANAGER_EVENT_ZH: dict[str, str] = {
    "RoleGranted":      "授予角色",
    "RoleRevoked":      "撤销角色",
    "RoleAdminChanged": "修改角色的管理员角色",
}

POOL_PROXY_EVENT_ZH: dict[str, str] = {
    "Upgraded":     "Pool 合约升级 (实现替换)",
    "AdminChanged": "Pool 代理管理员变更",
}

# ---------- 英文版(与上面 _ZH 一一对应) ----------
POOL_CONFIGURATOR_EVENT_EN: dict[str, str] = {
    "CollateralConfigurationChanged":      "Collateral config changed (LTV / liq. threshold / bonus)",
    "SupplyCapChanged":                    "Supply cap changed",
    "BorrowCapChanged":                    "Borrow cap changed",
    "ReserveInterestRateStrategyChanged":  "Interest rate strategy replaced",
    "ReserveFactorChanged":                "Reserve factor changed",
    "ReserveFrozen":                       "Reserve frozen/unfrozen",
    "ReservePaused":                       "Reserve paused/unpaused",
    "ReserveActive":                       "Reserve activated/deactivated",
    "ReserveBorrowing":                    "Borrowing enabled/disabled",
    "ReserveFlashLoaning":                 "Flash loan enabled/disabled",
    "LiquidationProtocolFeeChanged":       "Liquidation protocol fee changed",
    "DebtCeilingChanged":                  "Debt ceiling (isolation mode) changed",
    "BorrowableInIsolationChanged":        "Borrowable in isolation changed",
    "EModeAssetCategoryChanged":           "Asset eMode category changed",
    "EModeCategoryAdded":                  "eMode category added",
    "ReserveDropped":                      "Reserve dropped",
    "ReserveInitialized":                  "Reserve initialized",
    "SiloedBorrowingChanged":              "Siloed borrowing toggled",
    "UnbackedMintCapChanged":              "Unbacked mint cap changed",
}

ACL_MANAGER_EVENT_EN: dict[str, str] = {
    "RoleGranted":      "Role granted",
    "RoleRevoked":      "Role revoked",
    "RoleAdminChanged": "Role admin changed",
}

POOL_PROXY_EVENT_EN: dict[str, str] = {
    "Upgraded":     "Pool upgraded (new implementation)",
    "AdminChanged": "Pool proxy admin changed",
}


# ---------- 一些常用 ACLManager role hash (OZ AccessControl 用 keccak256("NAME")) ----------
# DEFAULT_ADMIN_ROLE 是全 0 的 bytes32,其它角色是 keccak256(text=roleName).
# 前端可以用这张表把 role bytes32 转为可读名称。
def _role_hash(name: str) -> str:
    return "0x" + Web3.keccak(text=name).hex().removeprefix("0x").lower()


ACL_ROLE_NAMES: dict[str, str] = {
    "0x" + "00" * 32:                  "DEFAULT_ADMIN_ROLE",
    _role_hash("POOL_ADMIN"):          "POOL_ADMIN",
    _role_hash("EMERGENCY_ADMIN"):     "EMERGENCY_ADMIN",
    _role_hash("RISK_ADMIN"):          "RISK_ADMIN",
    _role_hash("FLASH_BORROWER"):      "FLASH_BORROWER",
    _role_hash("BRIDGE"):              "BRIDGE",
    _role_hash("ASSET_LISTING_ADMIN"): "ASSET_LISTING_ADMIN",
}


# ---------- 人类可读 display 格式化 ----------
# 为前端展示单独生成的字符串字段(old_display / new_display)。
# 原则:
#   - 数字类(Cap/Fee/LTV 等):按单位换算 + 带千分位(或百分比);
#   - Boolean:是/否;
#   - Address/bytes32:保留原值 — 前端仍走 fmtAddr 截断。

def _fmt_thousands(n: int) -> str:
    return f"{n:,}"


def _fmt_usd_compact(n: int | float) -> str:
    """$1.5B / $23.4M / $1,234 这类紧凑 USD 显示。"""
    v = float(n)
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{sign}${a/1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a/1e6:.2f}M"
    if a >= 1e3:
        return f"{sign}${a/1e3:.2f}K"
    return f"{sign}${a:,.2f}"


def _fmt_bps_pct(raw: str | int | None) -> str | None:
    """bps(万分比) → 百分比。8300 → '83.00%'。"""
    if raw is None:
        return None
    try:
        v = int(str(raw))
    except Exception:
        return None
    return f"{v/100:.2f}%"


def _fmt_token_amount(raw: str | int | None) -> str | None:
    """Cap 类数字: PoolConfigurator 的 SupplyCap/BorrowCap 事件值是
    **token 数量**(不含 decimals 倍数),直接加千分位。
    150000000 → '150,000,000'。"""
    if raw is None or raw == "":
        return None
    try:
        v = int(str(raw))
    except Exception:
        return str(raw)
    return _fmt_thousands(v)


def _fmt_debt_ceiling(raw: str | int | None) -> str | None:
    """DebtCeiling 单位是 USD,2 decimals(aave-v3 用 10^2)。"""
    if raw is None or raw == "":
        return None
    try:
        v = int(str(raw))
    except Exception:
        return str(raw)
    return _fmt_usd_compact(v / 100)


def _fmt_bool_i18n(raw: str | bool | None, *, true_txt: str, false_txt: str) -> str | None:
    if raw is None:
        return None
    s = str(raw).lower()
    if s in ("true", "1"):
        return true_txt
    if s in ("false", "0"):
        return false_txt
    return str(raw)


# 向后兼容别名(旧调用方仍可用)
def _fmt_bool_zh(raw: str | bool | None, *, true_zh: str = "是", false_zh: str = "否") -> str | None:
    return _fmt_bool_i18n(raw, true_txt=true_zh, false_txt=false_zh)


# 中英文 bool 标签映射,按事件类型查
_BOOL_LABELS = {
    "ReservePaused":              {"zh": ("已暂停", "已恢复"),   "en": ("Paused", "Unpaused")},
    "ReserveFrozen":              {"zh": ("已冻结", "已解冻"),   "en": ("Frozen", "Unfrozen")},
    "ReserveActive":              {"zh": ("已启用", "已停用"),   "en": ("Active", "Inactive")},
    "_default":                   {"zh": ("是", "否"),          "en": ("Yes", "No")},
}


def format_event_display(
    event_name: str,
    old_value,
    new_value,
    extra: dict | None,
    lang: str = "zh",
) -> tuple[str | None, str | None, dict | None]:
    """按事件类型把 old/new 原始字符串值转成人类可读字符串。

    lang: 'zh' 中文, 'en' 英文。bps/数字/金额格式不随语言变,只有文字类
    (bool 标签、多值事件的组合描述)需要翻。

    Returns: (old_display, new_display, extra_display)
      - old_display/new_display: 人类可读,None 时前端 fallback 到 fmtAddr
      - extra_display: 多值事件(如 CollateralConfigurationChanged)的详细字段
    """
    od: str | None = None
    nd: str | None = None
    extra_display: dict | None = None
    is_en = lang == "en"

    # --- Cap 类:token 数量(带千分位) — 与语言无关 ---
    if event_name in ("SupplyCapChanged", "BorrowCapChanged", "UnbackedMintCapChanged"):
        od = _fmt_token_amount(old_value)
        nd = _fmt_token_amount(new_value)

    # --- DebtCeiling: USD with 2 decimals — 与语言无关 ---
    elif event_name == "DebtCeilingChanged":
        od = _fmt_debt_ceiling(old_value)
        nd = _fmt_debt_ceiling(new_value)

    # --- bps(万分比) → 百分比 — 与语言无关 ---
    elif event_name in ("ReserveFactorChanged", "LiquidationProtocolFeeChanged"):
        od = _fmt_bps_pct(old_value)
        nd = _fmt_bps_pct(new_value)

    # --- Boolean 事件(需多语言) ---
    elif event_name in ("ReservePaused", "ReserveFrozen", "ReserveActive"):
        labels = _BOOL_LABELS[event_name]["en" if is_en else "zh"]
        nd = _fmt_bool_i18n(new_value, true_txt=labels[0], false_txt=labels[1])
    elif event_name in ("ReserveBorrowing", "ReserveFlashLoaning",
                        "BorrowableInIsolationChanged", "SiloedBorrowingChanged"):
        labels = _BOOL_LABELS["_default"]["en" if is_en else "zh"]
        od = _fmt_bool_i18n(old_value, true_txt=labels[0], false_txt=labels[1])
        nd = _fmt_bool_i18n(new_value, true_txt=labels[0], false_txt=labels[1])

    # --- eMode 分类号 — 与语言无关 ---
    elif event_name == "EModeAssetCategoryChanged":
        od = f"#{old_value}" if old_value not in (None, "None", "") else None
        nd = f"#{new_value}" if new_value not in (None, "None", "") else None

    # --- CollateralConfigurationChanged / EModeCategoryAdded: 多值组合 ---
    elif event_name in ("CollateralConfigurationChanged", "EModeCategoryAdded"):
        if extra:
            extra_display = {
                "ltv": _fmt_bps_pct(extra.get("ltv")),
                "liquidationThreshold": _fmt_bps_pct(extra.get("liquidationThreshold")),
                "liquidationBonus": _fmt_bps_pct(extra.get("liquidationBonus")),
            }
            parts = []
            if extra_display.get("ltv"):
                parts.append(f"LTV {extra_display['ltv']}")
            if extra_display.get("liquidationThreshold"):
                label = "Liq. Threshold" if is_en else "清算门槛"
                parts.append(f"{label} {extra_display['liquidationThreshold']}")
            if extra_display.get("liquidationBonus"):
                label = "Liq. Bonus" if is_en else "清算奖金"
                parts.append(f"{label} {extra_display['liquidationBonus']}")
            if parts:
                nd = " · ".join(parts)

    # --- ACL RoleGranted/Revoked: new_value 是 account 地址,保留原值 ---
    elif event_name in ("RoleGranted", "RoleRevoked"):
        pass
    elif event_name == "RoleAdminChanged":
        # role bytes32 → 可读名(英文角色名本来就是英文,语言不变)
        if old_value:
            od = ACL_ROLE_NAMES.get(str(old_value).lower(), str(old_value))
        if new_value:
            nd = ACL_ROLE_NAMES.get(str(new_value).lower(), str(new_value))

    # --- 地址类:不生成 display,前端走 fmtAddr ---
    else:
        pass

    return od, nd, extra_display


def format_role_hash(role_hash: str | None) -> str | None:
    """给前端用:把 ACL role 的 bytes32 转为可读名(找不到返回 None)。"""
    if not role_hash:
        return None
    return ACL_ROLE_NAMES.get(str(role_hash).lower())

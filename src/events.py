"""Event ABIs + topic0 helpers for on-demand log queries.

Contains:
  - Aave v3 Pool activity events (Supply, Withdraw, Borrow, Repay, LiquidationCall)
  - PoolAddressesProvider permission events
  - Generic OwnershipTransferred
  - Chinese display strings (EVENT_ZH, PERMISSION_EVENT_ZH)
  - topic0 hash helpers (keccak("EventName(types)")) for eth_getLogs filtering.

Notes on Aave v3 event signatures — indexed modifiers do NOT affect the
signature string; only the types do.
  Supply(address,address,address,uint256,uint16)
    indexed: reserve, onBehalfOf, referralCode
  Withdraw(address,address,address,uint256)
    indexed: reserve, user, to
  Borrow(address,address,address,uint256,uint8,uint256,uint16)
    indexed: reserve, onBehalfOf, referralCode
  Repay(address,address,address,uint256,bool)
    indexed: reserve, user, repayer
  LiquidationCall(address,address,address,uint256,uint256,address,bool)
    indexed: collateralAsset, debtAsset, user
"""
from web3 import Web3


# ---------- Aave v3 Pool events ----------
AAVE_POOL_EVENTS_ABI = [
    # Supply
    {
        "anonymous": False,
        "type": "event",
        "name": "Supply",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "reserve",      "type": "address"},
            {"indexed": False, "internalType": "address", "name": "user",         "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "onBehalfOf",   "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount",       "type": "uint256"},
            {"indexed": True,  "internalType": "uint16",  "name": "referralCode", "type": "uint16"},
        ],
    },
    # Withdraw
    {
        "anonymous": False,
        "type": "event",
        "name": "Withdraw",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "reserve", "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "user",    "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "to",      "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount",  "type": "uint256"},
        ],
    },
    # Borrow
    {
        "anonymous": False,
        "type": "event",
        "name": "Borrow",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "reserve",             "type": "address"},
            {"indexed": False, "internalType": "address", "name": "user",                "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "onBehalfOf",          "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount",              "type": "uint256"},
            {"indexed": False, "internalType": "uint8",   "name": "interestRateMode",    "type": "uint8"},
            {"indexed": False, "internalType": "uint256", "name": "borrowRate",          "type": "uint256"},
            {"indexed": True,  "internalType": "uint16",  "name": "referralCode",        "type": "uint16"},
        ],
    },
    # Repay
    {
        "anonymous": False,
        "type": "event",
        "name": "Repay",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "reserve",   "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "user",      "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "repayer",   "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount",    "type": "uint256"},
            {"indexed": False, "internalType": "bool",    "name": "useATokens","type": "bool"},
        ],
    },
    # LiquidationCall
    {
        "anonymous": False,
        "type": "event",
        "name": "LiquidationCall",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "collateralAsset",          "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "debtAsset",                "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "user",                     "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "debtToCover",              "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "liquidatedCollateralAmount","type": "uint256"},
            {"indexed": False, "internalType": "address", "name": "liquidator",               "type": "address"},
            {"indexed": False, "internalType": "bool",    "name": "receiveAToken",            "type": "bool"},
        ],
    },
]


# ---------- PoolAddressesProvider permission events ----------
POOL_ADDRESSES_PROVIDER_EVENTS_ABI = [
    {
        "anonymous": False, "type": "event", "name": "PoolUpdated",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "oldAddress", "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "newAddress", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "PoolConfiguratorUpdated",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "oldAddress", "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "newAddress", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "PriceOracleUpdated",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "oldAddress", "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "newAddress", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "ACLManagerUpdated",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "oldAddress", "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "newAddress", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "ACLAdminUpdated",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "oldAddress", "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "newAddress", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "PoolDataProviderUpdated",
        "inputs": [
            {"indexed": True,  "internalType": "address", "name": "oldAddress", "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "newAddress", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "ProxyCreated",
        "inputs": [
            {"indexed": True,  "internalType": "bytes32", "name": "id",             "type": "bytes32"},
            {"indexed": True,  "internalType": "address", "name": "proxyAddress",   "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "implementationAddress", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "AddressSet",
        "inputs": [
            {"indexed": True,  "internalType": "bytes32", "name": "id",         "type": "bytes32"},
            {"indexed": True,  "internalType": "address", "name": "oldAddress", "type": "address"},
            {"indexed": True,  "internalType": "address", "name": "newAddress", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "AddressSetAsProxy",
        "inputs": [
            {"indexed": True,  "internalType": "bytes32", "name": "id",                    "type": "bytes32"},
            {"indexed": True,  "internalType": "address", "name": "proxyAddress",          "type": "address"},
            {"indexed": False, "internalType": "address", "name": "oldImplementationAddress","type": "address"},
            {"indexed": True,  "internalType": "address", "name": "newImplementationAddress","type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "OwnershipTransferred",
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "previousOwner", "type": "address"},
            {"indexed": True, "internalType": "address", "name": "newOwner",      "type": "address"},
        ],
    },
]


# ---------- signature strings (for topic0 hashing) ----------
# NOTE: indexed keywords do NOT appear in the signature string.
EVENT_SIGNATURES: dict[str, str] = {
    # Pool events
    "Supply":          "Supply(address,address,address,uint256,uint16)",
    "Withdraw":        "Withdraw(address,address,address,uint256)",
    "Borrow":          "Borrow(address,address,address,uint256,uint8,uint256,uint16)",
    "Repay":           "Repay(address,address,address,uint256,bool)",
    "LiquidationCall": "LiquidationCall(address,address,address,uint256,uint256,address,bool)",
    # PoolAddressesProvider events
    "PoolUpdated":              "PoolUpdated(address,address)",
    "PoolConfiguratorUpdated":  "PoolConfiguratorUpdated(address,address)",
    "PriceOracleUpdated":       "PriceOracleUpdated(address,address)",
    "ACLManagerUpdated":        "ACLManagerUpdated(address,address)",
    "ACLAdminUpdated":          "ACLAdminUpdated(address,address)",
    "PoolDataProviderUpdated":  "PoolDataProviderUpdated(address,address)",
    "ProxyCreated":             "ProxyCreated(bytes32,address,address)",
    "AddressSet":               "AddressSet(bytes32,address,address)",
    "AddressSetAsProxy":        "AddressSetAsProxy(bytes32,address,address,address)",
    "OwnershipTransferred":     "OwnershipTransferred(address,address)",
}


def topic0_for(event_name: str) -> str:
    """Return 0x-prefixed hex topic0 (keccak256 of the canonical signature)."""
    sig = EVENT_SIGNATURES[event_name]
    h = Web3.keccak(text=sig)
    # web3.py returns HexBytes; ensure 0x-prefix lowercase string
    return "0x" + h.hex().removeprefix("0x").lower()


# Pre-computed lookup tables for fast filtering
POOL_EVENT_NAMES = ["Supply", "Withdraw", "Borrow", "Repay", "LiquidationCall"]
PERMISSION_EVENT_NAMES = [
    "PoolUpdated",
    "PoolConfiguratorUpdated",
    "PriceOracleUpdated",
    "ACLManagerUpdated",
    "ACLAdminUpdated",
    "PoolDataProviderUpdated",
    "ProxyCreated",
    "AddressSet",
    "AddressSetAsProxy",
    "OwnershipTransferred",
]

POOL_EVENT_TOPIC0: dict[str, str] = {n: topic0_for(n) for n in POOL_EVENT_NAMES}
PERMISSION_EVENT_TOPIC0: dict[str, str] = {n: topic0_for(n) for n in PERMISSION_EVENT_NAMES}

# Reverse map: topic0 -> event name (both lowercase hex)
TOPIC0_TO_POOL_EVENT: dict[str, str] = {v.lower(): k for k, v in POOL_EVENT_TOPIC0.items()}
TOPIC0_TO_PERMISSION_EVENT: dict[str, str] = {v.lower(): k for k, v in PERMISSION_EVENT_TOPIC0.items()}


# ---------- ERC1967 Proxy + Pausable events (Track B on Pool proxy) ----------
PROXY_EVENTS_ABI = [
    {
        "anonymous": False, "type": "event", "name": "AdminChanged",
        "inputs": [
            {"indexed": False, "internalType": "address", "name": "previousAdmin", "type": "address"},
            {"indexed": False, "internalType": "address", "name": "newAdmin",      "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "Upgraded",
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "implementation", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "BeaconUpgraded",
        "inputs": [
            {"indexed": True, "internalType": "address", "name": "beacon", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "Paused",
        "inputs": [
            {"indexed": False, "internalType": "address", "name": "account", "type": "address"},
        ],
    },
    {
        "anonymous": False, "type": "event", "name": "Unpaused",
        "inputs": [
            {"indexed": False, "internalType": "address", "name": "account", "type": "address"},
        ],
    },
]

EVENT_SIGNATURES.update({
    "AdminChanged":   "AdminChanged(address,address)",
    "Upgraded":       "Upgraded(address)",
    "BeaconUpgraded": "BeaconUpgraded(address)",
    "Paused":         "Paused(address)",
    "Unpaused":       "Unpaused(address)",
})

PROXY_EVENT_NAMES = ["AdminChanged", "Upgraded", "BeaconUpgraded", "Paused", "Unpaused"]
PROXY_EVENT_TOPIC0: dict[str, str] = {n: topic0_for(n) for n in PROXY_EVENT_NAMES}
TOPIC0_TO_PROXY_EVENT: dict[str, str] = {v.lower(): k for k, v in PROXY_EVENT_TOPIC0.items()}


# ---------- i18n ----------
EVENT_ZH: dict[str, str] = {
    "Supply": "存款",
    "Withdraw": "提款",
    "Borrow": "借款",
    "Repay": "还款",
    "LiquidationCall": "清算",
}

EVENT_EN: dict[str, str] = {
    "Supply": "Supply",
    "Withdraw": "Withdraw",
    "Borrow": "Borrow",
    "Repay": "Repay",
    "LiquidationCall": "Liquidation",
}

PERMISSION_EVENT_ZH: dict[str, str] = {
    "PoolUpdated":             "Pool 合约地址被更新",
    "PoolConfiguratorUpdated": "PoolConfigurator 合约被更新",
    "PriceOracleUpdated":      "价格预言机被更新",
    "ACLManagerUpdated":       "ACLManager 合约被更新",
    "ACLAdminUpdated":         "ACL 管理员被更新",
    "PoolDataProviderUpdated": "PoolDataProvider 合约被更新",
    "ProxyCreated":            "代理合约被创建",
    "AddressSet":              "地址条目被设置",
    "AddressSetAsProxy":       "代理地址被设置(指向新实现)",
    "OwnershipTransferred":    "所有权已转移",
    "AdminChanged":            "代理合约管理员变更",
    "Upgraded":                "代理合约已升级(实现替换)",
    "BeaconUpgraded":          "Beacon 代理已升级",
    "Paused":                  "合约已暂停",
    "Unpaused":                "合约已恢复",
}

PERMISSION_EVENT_EN: dict[str, str] = {
    "PoolUpdated":             "Pool contract address updated",
    "PoolConfiguratorUpdated": "PoolConfigurator contract updated",
    "PriceOracleUpdated":      "Price oracle updated",
    "ACLManagerUpdated":       "ACLManager contract updated",
    "ACLAdminUpdated":         "ACL admin updated",
    "PoolDataProviderUpdated": "PoolDataProvider contract updated",
    "ProxyCreated":            "Proxy contract created",
    "AddressSet":              "Address entry set",
    "AddressSetAsProxy":       "Address set as proxy (new impl)",
    "OwnershipTransferred":    "Ownership transferred",
    "AdminChanged":            "Proxy admin changed",
    "Upgraded":                "Proxy upgraded (new implementation)",
    "BeaconUpgraded":          "Beacon proxy upgraded",
    "Paused":                  "Contract paused",
    "Unpaused":                "Contract unpaused",
}

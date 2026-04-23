"""Config loader: reads YAML files and env vars."""
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


@dataclass
class RpcProviderCfg:
    url: str
    tier: int = 2
    weight: int = 10
    rate_limit_qps: int = 5
    timeout_sec: float = 10.0


@dataclass
class RpcDefaults:
    max_retries: int = 3
    backoff_base_ms: int = 500
    backoff_max_ms: int = 8000
    total_timeout_sec: float = 30.0
    circuit: dict = field(default_factory=dict)
    health: dict = field(default_factory=dict)


@dataclass
class ChainCfg:
    chain_id: int
    providers: list[RpcProviderCfg]


@dataclass
class AppConfig:
    chains: dict[str, ChainCfg]
    defaults: RpcDefaults
    protocols: dict
    watchlist: dict
    rules: dict
    enabled_chains: list[str]
    enabled_protocols: list[str]
    collect_interval_sec: int
    lark_webhook_url: str | None
    lark_push_info: bool
    alert_min_level: str


_LEVEL_ORDER = {"info": 0, "warning": 1, "alert": 2, "critical": 3}


def level_ge(a: str, b: str) -> bool:
    return _LEVEL_ORDER.get(a, 0) >= _LEVEL_ORDER.get(b, 0)


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _csv_env(key: str) -> list[str]:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def load_config() -> AppConfig:
    rpc_raw = _load_yaml(CONFIG_DIR / "rpc.yaml")
    protocols_raw = _load_yaml(CONFIG_DIR / "protocols.yaml")
    rules_raw = _load_yaml(CONFIG_DIR / "rules.yaml")

    chains: dict[str, ChainCfg] = {}
    for name, entry in (rpc_raw.get("chains") or {}).items():
        providers = [
            RpcProviderCfg(
                url=p["url"],
                tier=p.get("tier", 2),
                weight=p.get("weight", 10),
                rate_limit_qps=p.get("rate_limit_qps", 5),
                timeout_sec=p.get("timeout_sec", 10.0),
            )
            for p in (entry.get("providers") or [])
        ]
        chains[name] = ChainCfg(chain_id=entry["chain_id"], providers=providers)

    defaults_raw = rpc_raw.get("defaults") or {}
    defaults = RpcDefaults(
        max_retries=defaults_raw.get("max_retries", 3),
        backoff_base_ms=defaults_raw.get("backoff_base_ms", 500),
        backoff_max_ms=defaults_raw.get("backoff_max_ms", 8000),
        total_timeout_sec=defaults_raw.get("total_timeout_sec", 30.0),
        circuit=defaults_raw.get("circuit") or {},
        health=defaults_raw.get("health") or {},
    )

    protocols = {k: v for k, v in protocols_raw.items() if not k.endswith("_watchlist")}
    watchlist = {
        k.removesuffix("_watchlist"): v
        for k, v in protocols_raw.items()
        if k.endswith("_watchlist")
    }

    enabled_chains = _csv_env("ENABLED_CHAINS") or list(chains.keys())
    enabled_protocols = _csv_env("ENABLED_PROTOCOLS") or list(protocols.keys())

    return AppConfig(
        chains=chains,
        defaults=defaults,
        protocols=protocols,
        watchlist=watchlist,
        rules=rules_raw,
        enabled_chains=enabled_chains,
        enabled_protocols=enabled_protocols,
        collect_interval_sec=int(os.environ.get("COLLECT_INTERVAL_SEC", "60")),
        lark_webhook_url=os.environ.get("LARK_WEBHOOK_URL"),
        lark_push_info=os.environ.get("LARK_PUSH_INFO", "false").lower() == "true",
        alert_min_level=os.environ.get("ALERT_MIN_LEVEL", "warning").lower(),
    )

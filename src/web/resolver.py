"""Lazy resolver for Aave v3 on-chain deployment addresses.

For each chain we only hold the PoolAddressesProvider in config. The actual
Pool / DataProvider / Oracle are resolved on first request and cached.
Mirrors the logic in `src/aave_v3_collector.py::_call_pap` but is designed
to be shared by the HTTP layer without a collector instance.
"""
import asyncio

from web3 import AsyncWeb3

from ..abis import POOL_ADDRESSES_PROVIDER_ABI
from ..config import AppConfig
from ..logger import log
from ..rpc_pool import RpcPool


class AaveDeploymentResolver:
    def __init__(self, rpc_pools: dict[str, RpcPool], cfg: AppConfig):
        self.rpc_pools = rpc_pools
        self.cfg = cfg
        self._cache: dict[str, dict[str, str]] = {}
        self._lock = asyncio.Lock()

    def _pap_address(self, chain: str) -> str | None:
        return (
            (self.cfg.protocols.get("aave_v3") or {})
            .get(chain, {})
            .get("pool_addresses_provider")
        )

    async def resolve(self, chain: str) -> dict[str, str]:
        """Return {pool, pool_addresses_provider, data_provider, oracle,
        pool_configurator, acl_manager}.

        Raises ValueError if chain is not configured; RpcPoolExhausted if
        no provider can answer. pool_configurator / acl_manager 在权限事件
        扫描中要用,如果链上 PAP 变种不带这俩方法(理论上不会,但异常稳健),
        对应字段为 None,fetch_permission_events 会跳过该合约。"""
        cached = self._cache.get(chain)
        if cached is not None:
            return cached

        async with self._lock:
            cached = self._cache.get(chain)
            if cached is not None:
                return cached

            pap = self._pap_address(chain)
            if not pap:
                raise ValueError(f"aave_v3 not configured for chain={chain}")
            pool_pool = self.rpc_pools.get(chain)
            if pool_pool is None:
                raise ValueError(f"no RpcPool for chain={chain}")

            pap_addr = AsyncWeb3.to_checksum_address(pap)

            async def _call(method: str, *, required: bool = True) -> str | None:
                async def _f(w3: AsyncWeb3):
                    c = w3.eth.contract(address=pap_addr, abi=POOL_ADDRESSES_PROVIDER_ABI)
                    return await getattr(c.functions, method)().call()
                try:
                    addr = await pool_pool.execute(_f, method_label=f"pap.{method}")
                except Exception as exc:  # noqa: BLE001
                    if required:
                        raise
                    log.warning(
                        "resolver[%s]: %s() not available on PAP — err=%s",
                        chain, method, exc,
                    )
                    return None
                return AsyncWeb3.to_checksum_address(addr)

            # 必需的 4 个地址(老版本已有),新增 2 个标记为非必需 — 某些 fork 变种
            # 可能没实现 getPoolConfigurator/getACLManager,失败时降级到 None。
            (
                pool_addr,
                dp_addr,
                oracle_addr,
                pool_cfg_addr,
                acl_mgr_addr,
            ) = await asyncio.gather(
                _call("getPool"),
                _call("getPoolDataProvider"),
                _call("getPriceOracle"),
                _call("getPoolConfigurator", required=False),
                _call("getACLManager", required=False),
            )

            deployment: dict[str, str] = {
                "pool": pool_addr,
                "pool_addresses_provider": pap_addr,
                "data_provider": dp_addr,
                "oracle": oracle_addr,
            }
            if pool_cfg_addr:
                deployment["pool_configurator"] = pool_cfg_addr
            if acl_mgr_addr:
                deployment["acl_manager"] = acl_mgr_addr
            self._cache[chain] = deployment
            log.info(
                "resolver[%s]: pool=%s pap=%s dp=%s oracle=%s pool_cfg=%s acl=%s",
                chain, pool_addr, pap_addr, dp_addr, oracle_addr,
                pool_cfg_addr, acl_mgr_addr,
            )
            return deployment

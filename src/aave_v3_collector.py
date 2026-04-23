"""Aave v3 data collector.

Collects per-reserve TVL, borrow, utilization, price in USD.
Static metadata (symbol/decimals/token addresses) is cached at init.
"""
import asyncio
import time
from dataclasses import dataclass, field

from web3 import AsyncWeb3
from web3.contract import AsyncContract

from .abis import (
    AAVE_DATA_PROVIDER_ABI,
    AAVE_ORACLE_ABI,
    AAVE_POOL_ABI,
    ERC20_ABI,
    ERC20_BYTES32_ABI,
    POOL_ADDRESSES_PROVIDER_ABI,
)
from .logger import log
from .rpc_pool import RpcError, RpcPool


@dataclass
class ReserveMeta:
    asset: str
    symbol: str
    decimals: int


@dataclass
class ReserveSnapshot:
    chain: str
    protocol: str
    asset: str
    symbol: str
    decimals: int

    total_supply_raw: int          # atoken totalSupply in raw units
    total_stable_debt_raw: int
    total_variable_debt_raw: int
    price_usd: float               # price quoted by Aave oracle (USD)

    liquidity_rate_ray: int        # ray = 1e27 ; APR for suppliers
    variable_borrow_rate_ray: int
    stable_borrow_rate_ray: int

    block_number: int
    timestamp: float = field(default_factory=time.time)

    # ----- derived -----
    @property
    def total_supply(self) -> float:
        return self.total_supply_raw / (10 ** self.decimals)

    @property
    def total_debt(self) -> float:
        return (self.total_stable_debt_raw + self.total_variable_debt_raw) / (
            10 ** self.decimals
        )

    @property
    def available_liquidity(self) -> float:
        return max(0.0, self.total_supply - self.total_debt)

    @property
    def utilization_pct(self) -> float:
        if self.total_supply <= 0:
            return 0.0
        return min(100.0, self.total_debt / self.total_supply * 100.0)

    @property
    def supply_usd(self) -> float:
        return self.total_supply * self.price_usd

    @property
    def borrow_usd(self) -> float:
        return self.total_debt * self.price_usd

    @property
    def available_liquidity_usd(self) -> float:
        return self.available_liquidity * self.price_usd

    @property
    def pool_key(self) -> str:
        return f"{self.chain}:{self.protocol}:{self.symbol}"


@dataclass
class AaveDeployment:
    chain: str
    pool: str
    data_provider: str
    oracle: str
    oracle_base_unit: int
    reserves: list[ReserveMeta]


class AaveV3Collector:
    def __init__(
        self,
        chain: str,
        pool_addresses_provider: str,
        rpc_pool: RpcPool,
        watchlist_symbols: list[str] | None = None,
    ):
        self.chain = chain
        self.rpc_pool = rpc_pool
        self.pap_addr = AsyncWeb3.to_checksum_address(pool_addresses_provider)
        self.watchlist = {s.lower() for s in (watchlist_symbols or [])}
        self.deployment: AaveDeployment | None = None

    # --------- init / discovery ---------
    async def init(self) -> None:
        pool_addr, dp_addr, oracle_addr = await asyncio.gather(
            self._call_pap("getPool"),
            self._call_pap("getPoolDataProvider"),
            self._call_pap("getPriceOracle"),
        )

        async def _reserves_list(w3: AsyncWeb3):
            c = w3.eth.contract(address=pool_addr, abi=AAVE_POOL_ABI)
            return await c.functions.getReservesList().call()

        async def _base_unit(w3: AsyncWeb3):
            c = w3.eth.contract(address=oracle_addr, abi=AAVE_ORACLE_ABI)
            return await c.functions.BASE_CURRENCY_UNIT().call()

        reserves_addrs, base_unit = await asyncio.gather(
            self.rpc_pool.execute(_reserves_list, method_label="getReservesList"),
            self.rpc_pool.execute(_base_unit, method_label="BASE_CURRENCY_UNIT"),
        )

        metas = await self._fetch_reserve_metas([AsyncWeb3.to_checksum_address(a) for a in reserves_addrs])
        if self.watchlist:
            metas = [m for m in metas if m.symbol.lower() in self.watchlist]

        self.deployment = AaveDeployment(
            chain=self.chain,
            pool=pool_addr,
            data_provider=dp_addr,
            oracle=oracle_addr,
            oracle_base_unit=int(base_unit),
            reserves=metas,
        )
        log.info(
            "aave_v3[%s] initialized: pool=%s dp=%s oracle=%s reserves=%d (watchlist=%d)",
            self.chain,
            pool_addr,
            dp_addr,
            oracle_addr,
            len(metas),
            len(self.watchlist),
        )

    async def _call_pap(self, method: str) -> str:
        async def _f(w3: AsyncWeb3):
            c = w3.eth.contract(address=self.pap_addr, abi=POOL_ADDRESSES_PROVIDER_ABI)
            return await getattr(c.functions, method)().call()

        addr = await self.rpc_pool.execute(_f, method_label=f"pap.{method}")
        return AsyncWeb3.to_checksum_address(addr)

    async def _fetch_reserve_metas(self, assets: list[str]) -> list[ReserveMeta]:
        async def _fetch_symbol(asset: str) -> str:
            async def _string_sym(w3: AsyncWeb3):
                c = w3.eth.contract(address=asset, abi=ERC20_ABI)
                return await c.functions.symbol().call()

            async def _b32_sym(w3: AsyncWeb3):
                c = w3.eth.contract(address=asset, abi=ERC20_BYTES32_ABI)
                raw = await c.functions.symbol().call()
                if isinstance(raw, (bytes, bytearray)):
                    return raw.rstrip(b"\x00").decode("utf-8", errors="replace")
                return raw

            try:
                return await self.rpc_pool.execute(_string_sym, method_label="erc20.symbol")
            except RpcError:
                try:
                    return await self.rpc_pool.execute(_b32_sym, method_label="erc20.symbol.b32")
                except RpcError:
                    return f"0x{asset[2:10]}"  # fallback: address prefix

        async def _one(asset: str) -> ReserveMeta | None:
            try:
                async def _dec(w3: AsyncWeb3):
                    return await w3.eth.contract(address=asset, abi=ERC20_ABI).functions.decimals().call()

                symbol, decimals = await asyncio.gather(
                    _fetch_symbol(asset),
                    self.rpc_pool.execute(_dec, method_label="erc20.decimals"),
                )
                return ReserveMeta(asset=asset, symbol=symbol, decimals=int(decimals))
            except Exception as exc:  # noqa: BLE001
                log.warning("fetch meta failed for %s: %s", asset, exc)
                return None

        results = await asyncio.gather(*[_one(a) for a in assets])
        return [r for r in results if r is not None]

    # --------- collect ---------
    async def collect(self) -> list[ReserveSnapshot]:
        if self.deployment is None:
            raise RuntimeError("collector not initialized")
        dep = self.deployment

        async def _block_number(w3: AsyncWeb3):
            return await w3.eth.block_number

        bn = int(await self.rpc_pool.execute(_block_number, method_label="block_number"))

        async def _one(meta: ReserveMeta) -> ReserveSnapshot | None:
            asset = AsyncWeb3.to_checksum_address(meta.asset)

            async def _reserve_data(w3: AsyncWeb3):
                c = w3.eth.contract(address=dep.data_provider, abi=AAVE_DATA_PROVIDER_ABI)
                return await c.functions.getReserveData(asset).call()

            async def _price(w3: AsyncWeb3):
                c = w3.eth.contract(address=dep.oracle, abi=AAVE_ORACLE_ABI)
                return await c.functions.getAssetPrice(asset).call()

            try:
                reserve_data, price_raw = await asyncio.gather(
                    self.rpc_pool.execute(_reserve_data, method_label="dataProvider.getReserveData"),
                    self.rpc_pool.execute(_price, method_label="oracle.getAssetPrice"),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("collect failed for %s.%s: %s", self.chain, meta.symbol, exc)
                return None

            (
                _unbacked,
                _accrued,
                total_atoken,
                total_stable,
                total_variable,
                liquidity_rate,
                variable_borrow_rate,
                stable_borrow_rate,
                _avg_stable_rate,
                _liquidity_idx,
                _variable_idx,
                _last_update_ts,
            ) = reserve_data

            price_usd = int(price_raw) / dep.oracle_base_unit

            return ReserveSnapshot(
                chain=self.chain,
                protocol="aave_v3",
                asset=asset,
                symbol=meta.symbol,
                decimals=meta.decimals,
                total_supply_raw=int(total_atoken),
                total_stable_debt_raw=int(total_stable),
                total_variable_debt_raw=int(total_variable),
                price_usd=price_usd,
                liquidity_rate_ray=int(liquidity_rate),
                variable_borrow_rate_ray=int(variable_borrow_rate),
                stable_borrow_rate_ray=int(stable_borrow_rate),
                block_number=bn,
            )

        results = await asyncio.gather(*[_one(m) for m in dep.reserves])
        return [r for r in results if r is not None]

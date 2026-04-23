"""Multi-provider RPC pool with retry, rotation and circuit breaker."""
import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from web3 import AsyncHTTPProvider, AsyncWeb3

from .circuit_breaker import CircuitBreaker, CircuitState
from .config import ChainCfg, RpcDefaults, RpcProviderCfg
from .logger import log


class RpcError(Exception):
    pass


class RpcPoolExhausted(RpcError):
    pass


def classify_error(exc: BaseException) -> str:
    """Classify exception into: timeout | rate_limit | retryable | fatal."""
    text = f"{type(exc).__name__}: {exc!s}".lower()
    if isinstance(exc, asyncio.TimeoutError) or "timeout" in text:
        return "timeout"
    if "429" in text or "rate" in text or "too many" in text or "-32005" in text:
        return "rate_limit"
    if "-32015" in text or "execution reverted" in text or "-32602" in text:
        return "fatal"
    # ABI decode errors (e.g. legacy bytes32 symbol on MKR) — retrying hits same result
    if "could not decode" in text or "output_types" in text or "badfunctioncalloutput" in text:
        return "fatal"
    if "-32000" in text and "block" in text and ("not found" in text or "not exist" in text):
        return "retryable"
    if isinstance(exc, (ConnectionError, OSError)):
        return "retryable"
    if "500" in text or "502" in text or "503" in text or "504" in text:
        return "retryable"
    return "retryable"


@dataclass
class RpcProvider:
    cfg: RpcProviderCfg
    w3: AsyncWeb3
    breaker: CircuitBreaker
    last_request_at: float = 0.0

    @property
    def url(self) -> str:
        return self.cfg.url

    @property
    def tier(self) -> int:
        return self.cfg.tier


class RpcPool:
    """One pool per chain. Routes calls across providers with retry + CB."""

    def __init__(self, chain: str, chain_cfg: ChainCfg, defaults: RpcDefaults):
        self.chain = chain
        self.chain_cfg = chain_cfg
        self.defaults = defaults
        self.providers: list[RpcProvider] = []
        for p in chain_cfg.providers:
            w3 = AsyncWeb3(AsyncHTTPProvider(p.url, request_kwargs={"timeout": p.timeout_sec}))
            breaker = CircuitBreaker(
                name=p.url,
                circuit_cfg=dict(defaults.circuit) if defaults.circuit else {},
                health_cfg=dict(defaults.health) if defaults.health else {},
            )
            self.providers.append(RpcProvider(cfg=p, w3=w3, breaker=breaker))

    # --------- selection ---------
    def _eligible(self, max_tier: int) -> list[RpcProvider]:
        return [
            p
            for p in self.providers
            if p.cfg.tier <= max_tier and p.breaker.can_pass()
        ]

    def _pick(self, exclude: set[str], max_tier: int) -> RpcProvider | None:
        candidates = [p for p in self._eligible(max_tier) if p.url not in exclude]
        if not candidates:
            return None
        weights = [
            max(1, p.cfg.weight) * max(1, p.breaker.health_score) for p in candidates
        ]
        return random.choices(candidates, weights=weights, k=1)[0]

    async def _respect_rate_limit(self, provider: RpcProvider) -> None:
        if provider.cfg.rate_limit_qps <= 0:
            return
        min_gap = 1.0 / provider.cfg.rate_limit_qps
        gap = time.time() - provider.last_request_at
        if gap < min_gap:
            await asyncio.sleep(min_gap - gap)
        provider.last_request_at = time.time()

    # --------- core call ---------
    async def execute(
        self,
        fn: Callable[[AsyncWeb3], Awaitable[Any]],
        *,
        method_label: str = "call",
    ) -> Any:
        """Execute `fn(w3)` with rotation, retry and circuit-breaker awareness."""
        started = time.time()
        total_timeout = self.defaults.total_timeout_sec
        max_retries = self.defaults.max_retries
        base = self.defaults.backoff_base_ms / 1000.0
        max_back = self.defaults.backoff_max_ms / 1000.0

        excluded: set[str] = set()
        last_exc: BaseException | None = None
        attempt = 0
        max_tier = 2  # escalate to 3 only if T1/T2 exhausted

        while time.time() - started < total_timeout and attempt <= max_retries:
            provider = self._pick(excluded, max_tier)
            if provider is None:
                if max_tier < 3:
                    max_tier = 3
                    excluded.clear()
                    continue
                break

            try:
                await self._respect_rate_limit(provider)
                result = await asyncio.wait_for(
                    fn(provider.w3), timeout=provider.cfg.timeout_sec
                )
                provider.breaker.record_success()
                return result
            except BaseException as exc:  # noqa: BLE001
                last_exc = exc
                kind = classify_error(exc)
                log.debug(
                    "rpc error chain=%s provider=%s method=%s kind=%s err=%s",
                    self.chain,
                    provider.url,
                    method_label,
                    kind,
                    exc,
                )
                if kind == "fatal":
                    raise RpcError(f"fatal rpc error: {exc!s}") from exc
                provider.breaker.record_failure(
                    "timeout" if kind == "timeout" else
                    "rate_limit" if kind == "rate_limit" else
                    "error"
                )
                excluded.add(provider.url)
                attempt += 1
                if kind == "rate_limit":
                    # skip L1 (same-provider retry), go straight to rotate
                    continue
                if attempt == 1:
                    # L1: quick same-provider retry
                    excluded.discard(provider.url)
                    await asyncio.sleep(0.1)
                    continue
                # L3: exponential backoff
                backoff = min(max_back, base * (2 ** (attempt - 2)))
                await asyncio.sleep(backoff + random.uniform(0, backoff * 0.2))

        elapsed = time.time() - started
        raise RpcPoolExhausted(
            f"chain={self.chain} method={method_label} attempts={attempt} "
            f"elapsed={elapsed:.2f}s last_error={last_exc!s}"
        )

    # --------- health probe ---------
    async def probe_all(self) -> list[dict]:
        async def _probe(p: RpcProvider) -> dict:
            t0 = time.time()
            try:
                bn = await asyncio.wait_for(p.w3.eth.block_number, timeout=5.0)
                p.breaker.record_success()
                return {
                    "url": p.url,
                    "tier": p.tier,
                    "block": int(bn),
                    "latency_ms": int((time.time() - t0) * 1000),
                    "state": p.breaker.state.value,
                    "health": p.breaker.health_score,
                    "ok": True,
                }
            except BaseException as exc:  # noqa: BLE001
                kind = classify_error(exc)
                p.breaker.record_failure(
                    "timeout" if kind == "timeout" else "error"
                )
                return {
                    "url": p.url,
                    "tier": p.tier,
                    "error": f"{type(exc).__name__}: {exc!s}",
                    "latency_ms": int((time.time() - t0) * 1000),
                    "state": p.breaker.state.value,
                    "health": p.breaker.health_score,
                    "ok": False,
                }

        return await asyncio.gather(*[_probe(p) for p in self.providers])

    def snapshot(self) -> list[dict]:
        return [p.breaker.snapshot() | {"tier": p.tier} for p in self.providers]

    # --------- shutdown ---------
    async def close(self) -> None:
        """Close all underlying aiohttp sessions to avoid 'Unclosed client session'."""
        for p in self.providers:
            prov = p.w3.provider
            try:
                if hasattr(prov, "disconnect"):
                    await prov.disconnect()
                else:
                    # fallback: web3.py caches aiohttp sessions per URL internally
                    cache = getattr(prov, "_request_session_manager", None)
                    if cache and hasattr(cache, "cache_async_session_manager"):
                        mgr = cache.cache_async_session_manager
                        for sess in list(getattr(mgr, "_async_session_cache", {}).values()):
                            with _suppress():
                                await sess.close()
            except Exception as exc:  # noqa: BLE001
                log.debug("close provider %s failed: %s", p.url, exc)


class _suppress:
    """asyncio-friendly suppress used during shutdown."""
    def __enter__(self):
        return self
    def __exit__(self, et, ev, tb):
        return True

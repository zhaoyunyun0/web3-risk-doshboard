"""Top aToken holders via The Graph subgraph (M4).

We query Aave v3's official subgraph for the top 20 users by scaled aToken
balance on a given reserve, then convert scaled → real balance using the
reserve's `liquidityIndex`. This is the "static balance" ranking the PRD
asks for — replacing the activity-based "net flow" approximation.

Configuration: one URL per chain via env vars (see .env.example), e.g.
  THE_GRAPH_AAVE_V3_URL_ETHEREUM=https://gateway.thegraph.com/api/<KEY>/subgraphs/id/<ID>
If the env is unset for a chain, callers should fall back to the net-flow
implementation in src/web/on_demand.py.
"""
from __future__ import annotations

import httpx

from .logger import log


RAY = 10 ** 27


# Minimum aToken balance to include (skip dust rows that would clutter top 20
# rankings when a reserve is very fragmented).
MIN_ATOKEN_RAW = 1  # effectively no floor; callers can filter on USD instead


# GraphQL query: fetch top 20 users by scaled aToken balance for one reserve.
# We also pull reserve.liquidityIndex so we can compute real balances
# consistently with what the Pool reports on-chain.
_GQL_QUERY = """
query TopAHolders($underlying: Bytes!) {
  reserves(where: { underlyingAsset: $underlying }, first: 1) {
    id
    liquidityIndex
    decimals
    symbol
  }
  userReserves(
    where: { reserve_: { underlyingAsset: $underlying } }
    orderBy: scaledATokenBalance
    orderDirection: desc
    first: 20
  ) {
    user { id }
    scaledATokenBalance
    currentVariableDebt
    currentTotalDebt
  }
}
"""


def _short(addr: str | None) -> str | None:
    if not addr:
        return None
    a = addr.lower()
    if len(a) < 10:
        return a
    return f"0x{a[2:6].upper()}...{a[-2:].upper()}"


async def fetch_top_holders_subgraph(
    subgraph_url: str,
    reserve_addr: str,
    price_usd: float,
    decimals: int,
    *,
    timeout_sec: float = 20.0,
) -> list[dict]:
    """POST one GraphQL query, decode + return top-20 rows.

    Returned row shape matches the net-flow implementation so the frontend
    doesn't have to branch:
        {rank, address, address_short, balance_token, balance_usd,
         variable_debt_usd, scaled_atoken_raw, tag}
    """
    underlying = reserve_addr.lower()
    payload = {"query": _GQL_QUERY, "variables": {"underlying": underlying}}

    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        r = await client.post(subgraph_url, json=payload)
        r.raise_for_status()
        body = r.json()

    if body.get("errors"):
        # Keep the error message short — subgraph errors are long JSON blobs.
        first = body["errors"][0] if body["errors"] else {}
        raise RuntimeError(
            f"subgraph error: {first.get('message', 'unknown')}"
        )

    data = (body or {}).get("data") or {}
    reserves = data.get("reserves") or []
    rows = data.get("userReserves") or []

    if not reserves:
        log.warning("subgraph returned no reserve for underlying=%s", underlying)
        return []
    reserve = reserves[0]
    try:
        liquidity_index = int(reserve.get("liquidityIndex") or RAY)
    except (TypeError, ValueError):
        liquidity_index = RAY

    scale = 10 ** int(decimals)
    price = float(price_usd or 0.0)

    out: list[dict] = []
    for r in rows:
        user = (r.get("user") or {}).get("id") or ""
        try:
            scaled = int(r.get("scaledATokenBalance") or 0)
        except (TypeError, ValueError):
            scaled = 0
        if scaled < MIN_ATOKEN_RAW:
            continue
        # real balance = scaled * index / RAY
        real_raw = scaled * liquidity_index // RAY
        balance_token = real_raw / scale
        balance_usd = balance_token * price

        try:
            var_debt_raw = int(r.get("currentVariableDebt") or 0)
        except (TypeError, ValueError):
            var_debt_raw = 0
        variable_debt_usd = (var_debt_raw / scale) * price

        out.append({
            "address": user.lower(),
            "address_short": _short(user),
            "balance_token": balance_token,
            "balance_usd": balance_usd,
            "variable_debt_usd": variable_debt_usd,
            "scaled_atoken_raw": scaled,
            "tag": None,
        })

    out.sort(key=lambda x: x["balance_usd"], reverse=True)
    for i, row in enumerate(out[:20], start=1):
        row["rank"] = i
    return out[:20]

"""Lark webhook notifier with Chinese interactive card formatting."""
import time

import httpx

from .logger import log
from .rule_engine import Alert


# --------- i18n maps ---------
CARD_TEMPLATES = {
    "info": "blue",
    "warning": "yellow",
    "alert": "orange",
    "critical": "red",
}

LEVEL_EMOJI = {
    "info": "ℹ️",
    "warning": "🟡",
    "alert": "🟠",
    "critical": "🔴",
}

LEVEL_ZH = {
    "info": "信息",
    "warning": "预警",
    "alert": "告警",
    "critical": "严重",
}

CHAIN_ZH = {
    "ethereum": "以太坊",
    "arbitrum": "Arbitrum",
    "optimism": "Optimism",
    "base": "Base",
    "polygon": "Polygon",
    "bnb": "BNB Chain",
    "avalanche": "Avalanche",
    "gnosis": "Gnosis",
    "katana": "Katana",
}

PROTOCOL_ZH = {
    "aave_v3": "Aave v3",
    "compound_v3": "Compound v3",
    "morpho_blue": "Morpho Blue",
    "spark": "Spark",
    "venus": "Venus",
    "radiant": "Radiant",
}

# 规则名 → (标题短语, 度量模板选择键)
RULE_TITLES = {
    "utilization": "利用率异常",
    "tvl_drop": "TVL 骤降",
    "borrow_surge": "借款激增",
    "liquidity_drain": "流动性抽水",
}


# --------- formatters ---------
def fmt_usd(v) -> str:
    if v is None:
        return "N/A"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    sign = "-" if v < 0 else ""
    x = abs(v)
    if x >= 1e9:
        return f"{sign}${x / 1e9:.2f}B"
    if x >= 1e6:
        return f"{sign}${x / 1e6:.2f}M"
    if x >= 1e3:
        return f"{sign}${x / 1e3:.2f}K"
    return f"{sign}${x:,.2f}"


def fmt_pct(v) -> str:
    if v is None:
        return "N/A"
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return str(v)


def fmt_seconds(v) -> str:
    if v is None:
        return "N/A"
    try:
        v = int(v)
    except (TypeError, ValueError):
        return str(v)
    if v >= 3600:
        return f"{v / 3600:.1f} 小时"
    if v >= 60:
        return f"{v // 60} 分钟"
    return f"{v} 秒"


def rule_category(rule: str) -> str:
    """utilization_99pct → utilization; tvl_drop_5m_5pct → tvl_drop"""
    if rule.startswith("tvl_drop"):
        return "tvl_drop"
    if rule.startswith("borrow_surge"):
        return "borrow_surge"
    if rule.startswith("liquidity_drain"):
        return "liquidity_drain"
    if rule.startswith("utilization"):
        return "utilization"
    return rule.split("_")[0]


def format_metrics_block(alert: Alert) -> str:
    m = alert.metrics or {}
    cat = rule_category(alert.rule)

    if cat == "utilization":
        return (
            f"**当前利用率**:{fmt_pct(m.get('utilization_pct'))}\n"
            f"**总存款**:{fmt_usd(m.get('supply_usd'))}\n"
            f"**总借款**:{fmt_usd(m.get('borrow_usd'))}\n"
            f"**可用流动性**:{fmt_usd(m.get('available_liquidity_usd'))}"
        )
    if cat == "tvl_drop":
        drop = m.get("drop_pct", 0)
        return (
            f"**跌幅**:{fmt_pct(abs(drop))}(窗口 {fmt_seconds(m.get('window_sec'))})\n"
            f"**存款变化**:{fmt_usd(m.get('baseline_supply_usd'))} → "
            f"{fmt_usd(m.get('current_supply_usd'))}"
        )
    if cat == "borrow_surge":
        return (
            f"**增幅**:+{fmt_pct(m.get('surge_pct'))}"
            f"(窗口 {fmt_seconds(m.get('window_sec'))})\n"
            f"**借款变化**:{fmt_usd(m.get('baseline_borrow_usd'))} → "
            f"{fmt_usd(m.get('current_borrow_usd'))}"
        )
    if cat == "liquidity_drain":
        drain = m.get("drain_pct", 0)
        return (
            f"**跌幅**:{fmt_pct(abs(drain))}(窗口 {fmt_seconds(m.get('window_sec'))})\n"
            f"**可用流动性变化**:{fmt_usd(m.get('baseline_liquidity_usd'))} → "
            f"{fmt_usd(m.get('current_liquidity_usd'))}"
        )
    # fallback
    return "\n".join(f"**{k}**:{v}" for k, v in m.items())


# --------- notifier ---------
class LarkNotifier:
    def __init__(self, webhook_url: str | None):
        self.webhook_url = webhook_url
        self._client = httpx.AsyncClient(timeout=10.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def send_text(self, text: str) -> bool:
        if not self.webhook_url:
            log.warning("Lark webhook 未配置,跳过文本推送")
            return False
        return await self._post({"msg_type": "text", "content": {"text": text}})

    async def send_alert(self, alert: Alert) -> bool:
        if not self.webhook_url:
            log.warning("Lark webhook 未配置,跳过告警推送: %s", alert.rule)
            return False
        card = self._build_card(alert)
        return await self._post({"msg_type": "interactive", "card": card})

    async def send_heartbeat(
        self,
        chain: str,
        reserves_count: int,
        total_supply_usd: float,
        total_borrow_usd: float,
        rpc_health: list[dict],
    ) -> bool:
        if not self.webhook_url:
            return False
        healthy = sum(1 for p in rpc_health if p["state"] == "CLOSED")
        chain_zh = CHAIN_ZH.get(chain, chain)
        content = (
            f"💓 **系统心跳** · {chain_zh}\n"
            f"**监控池数**:{reserves_count}\n"
            f"**总存款**:{fmt_usd(total_supply_usd)}\n"
            f"**总借款**:{fmt_usd(total_borrow_usd)}\n"
            f"**RPC 节点健康度**:{healthy}/{len(rpc_health)}"
        )
        card = {
            "header": {
                "title": {"tag": "plain_text", "content": "🟢 w3_risk_dashboard 心跳"},
                "template": "green",
            },
            "elements": [{"tag": "markdown", "content": content}],
        }
        return await self._post({"msg_type": "interactive", "card": card})

    # --------- card builder ---------
    def _build_card(self, alert: Alert) -> dict:
        emoji = LEVEL_EMOJI.get(alert.level, "🔔")
        template = CARD_TEMPLATES.get(alert.level, "blue")
        level_zh = LEVEL_ZH.get(alert.level, alert.level)
        chain_zh = CHAIN_ZH.get(alert.chain, alert.chain)
        proto_zh = PROTOCOL_ZH.get(alert.protocol, alert.protocol)
        category_zh = RULE_TITLES.get(rule_category(alert.rule), "异常")

        title = f"{emoji} {level_zh} · {alert.symbol} {category_zh}"

        meta_line = (
            f"**资产**:`{alert.symbol}`   "
            f"**链/协议**:{chain_zh} / {proto_zh}   "
            f"**级别**:{level_zh}"
        )
        body = format_metrics_block(alert)
        footer = (
            f"🕒 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(alert.timestamp))}"
            f"   📌 `{alert.rule}`"
        )

        return {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": template,
            },
            "elements": [
                {"tag": "markdown", "content": meta_line},
                {"tag": "hr"},
                {"tag": "markdown", "content": body},
                {"tag": "hr"},
                {"tag": "markdown", "content": footer},
            ],
        }

    async def _post(self, payload: dict) -> bool:
        try:
            r = await self._client.post(self.webhook_url, json=payload)
            r.raise_for_status()
            data = r.json()
            if data.get("StatusCode") not in (0, None) and data.get("code") not in (0, None):
                log.warning("Lark 返回非零 code: %s", data)
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("Lark 推送失败: %s", exc)
            return False

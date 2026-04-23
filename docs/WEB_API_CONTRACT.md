# Web Dashboard API 契约 v1

> 前后端必须严格按此接口对齐。字段名、状态码不要擅自改。

## 约定
- 所有返回都是 JSON,顶层总有 `{ "ok": true|false }`(除非明确说没有)
- 时间戳 `ts` 统一用 **Unix 秒浮点**(SQLite 里就是 REAL)
- 金额 USD 用浮点数(frontend 显示时再缩写 B/M/K)
- 所有错误返回 `{ ok: false, error: "..." }` + 非 200 状态码
- 接口路径前缀 `/api/`
- 静态页面在 `/` (返回 index.html) 和 `/static/*`

## Pool Key 格式
`{chain}:{protocol}:{symbol}` — 例如 `ethereum:aave_v3:USDC`

## 接口清单

### `GET /api/status` — 系统状态
```json
{
  "ok": true,
  "server_started_at": 1713891000.0,
  "uptime_sec": 120,
  "monitor_running": true,
  "monitor_pid": 12345,
  "db": { "snapshots": 128, "alerts": 12, "path": "data/snapshots.db" },
  "chains_configured": ["ethereum"],
  "protocols_configured": ["aave_v3"]
}
```
Note: `monitor_running` 通过检查 `data/w3risk.pid` 是否存在且进程活着。如果不存在,仍然返回 `ok: true`,只是 `monitor_running: false`。

### `GET /api/protocols` — 协议清单(按配置)
```json
{
  "ok": true,
  "protocols": [
    {
      "name": "aave_v3",
      "display": "Aave v3",
      "chains": [
        { "name": "ethereum", "display": "以太坊", "chain_id": 1 }
      ]
    }
  ]
}
```
中文显示名从 `src/lark_notifier.py` 的 `CHAIN_ZH` / `PROTOCOL_ZH` map 复用(后端 agent 抽出来或 import)。

### `GET /api/pools` — 所有池子最新快照
从 SQLite 拉每个 pool_key 最新一条 `reserve_snapshots`。
```json
{
  "ok": true,
  "pools": [
    {
      "pool_key": "ethereum:aave_v3:USDC",
      "chain": "ethereum",
      "protocol": "aave_v3",
      "symbol": "USDC",
      "asset": "0xA0b869...",
      "ts": 1713891000.0,
      "block_number": 24942874,
      "supply_usd": 1790621155.0,
      "borrow_usd": 1765678015.0,
      "available_liquidity_usd": 24943140.0,
      "utilization_pct": 98.61,
      "price_usd": 0.9999
    }
  ]
}
```

### `GET /api/pools/{pool_key}/overview`
```json
{
  "ok": true,
  "pool": { ...同上池子对象... , "chain_zh": "以太坊", "protocol_zh": "Aave v3" }
}
```
如果 `pool_key` 不存在,404 + `{ ok: false, error: "pool not found" }`。

### `GET /api/pools/{pool_key}/history?hours=24`
从 SQLite 按时间拉点,最多 500 个(下采样)。
```json
{
  "ok": true,
  "pool_key": "ethereum:aave_v3:USDC",
  "hours": 24,
  "series": [
    { "ts": 1713890000.0, "supply_usd": 1.79e9, "borrow_usd": 1.76e9,
      "available_liquidity_usd": 3e7, "utilization_pct": 98.6, "price_usd": 0.9999 }
  ]
}
```

### `GET /api/pools/{pool_key}/activity?hours=1&min_usd=100000`
用 `eth_getLogs` 拉 Aave v3 Pool 合约的 `Supply`/`Withdraw`/`Borrow`/`Repay`/`LiquidationCall` 事件,过滤到 USD 阈值以上,按时间倒序。
```json
{
  "ok": true,
  "pool_key": "ethereum:aave_v3:USDC",
  "hours": 1,
  "min_usd": 100000,
  "cached_at": 1713891000.0,
  "events": [
    {
      "ts": 1713890990.0,
      "block": 24942870,
      "tx_hash": "0xabc...",
      "event": "Supply",
      "event_zh": "存款",
      "user": "0xA1B...",
      "on_behalf_of": "0xA1B...",
      "amount_token": 2000000.0,
      "amount_usd": 2000000.0
    },
    { "...": "Withdraw / Borrow / Repay / LiquidationCall 字段类似" }
  ]
}
```
**缓存**:30 秒 TTL,keyed by `(pool_key, hours, min_usd)`。
**失败**:如果 get_logs 出错,返回 `{ ok: false, error: "..." }` 200,前端显示"数据不可用"。

### `GET /api/pools/{pool_key}/holders?hours=24` — Top 20 近期净流入
根据最近 N 小时的 Supply 和 Withdraw 事件,按地址聚合:`net_usd = supply_usd - withdraw_usd`。
```json
{
  "ok": true,
  "pool_key": "ethereum:aave_v3:USDC",
  "method": "net_flow",
  "hours": 24,
  "cached_at": 1713891000.0,
  "top": [
    {
      "rank": 1,
      "address": "0xA1B2C3...",
      "address_short": "0xA1B2...C3",
      "net_usd": 15234567.0,
      "supply_usd": 20000000.0,
      "withdraw_usd": 4765433.0,
      "tx_count": 8,
      "tag": null
    }
  ]
}
```
**缓存**:5 分钟 TTL。
分页受公共 RPC 限制,get_logs 每次最多 1000 blocks,需要分批合并。
如果 24h 拉不到任何事件,返回 `top: []`。

### `GET /api/pools/{pool_key}/alerts?limit=50`
```json
{
  "ok": true,
  "pool_key": "ethereum:aave_v3:USDC",
  "alerts": [
    {
      "ts": 1713891000.0,
      "level": "alert",
      "level_zh": "告警",
      "rule": "utilization_95pct",
      "message": "...",
      "metrics": { "utilization_pct": 98.6, "supply_usd": 1.79e9 }
    }
  ]
}
```

### `GET /api/permissions?protocol=aave_v3&chain=ethereum&hours=24`
在该 chain 的 PoolAddressesProvider 合约上拉指定事件:
- `PoolUpdated`, `PoolConfiguratorUpdated`, `PriceOracleUpdated`, `ACLManagerUpdated`, `ACLAdminUpdated`, `PoolDataProviderUpdated`, `ProxyCreated`, `AddressSet`, `AddressSetAsProxy`
- 同时拉通用 `OwnershipTransferred` 事件
```json
{
  "ok": true,
  "protocol": "aave_v3",
  "chain": "ethereum",
  "hours": 24,
  "events": [
    {
      "ts": 1713890000.0,
      "block": 24942000,
      "tx_hash": "0x...",
      "contract": "0x2f39d...",
      "contract_role": "PoolAddressesProvider",
      "event": "PoolUpdated",
      "description_zh": "Pool 合约地址被更新",
      "old_value": "0x...",
      "new_value": "0x..."
    }
  ]
}
```
24h 没事件时返回 `events: []`(这是 Aave 常态)。

### `GET /api/alerts/recent?limit=20`
全局最近告警,不按 pool 过滤。
```json
{
  "ok": true,
  "alerts": [
    { "ts": ..., "level": ..., "level_zh": ..., "rule": ..., "pool_key": ...,
      "chain": ..., "protocol": ..., "symbol": ..., "message": ... }
  ]
}
```

### `GET /api/mutes`
```json
{
  "ok": true,
  "mutes": [
    {
      "pool_key": "ethereum:aave_v3:USDT",
      "rule": null,
      "until": 1713900000.0,
      "human_until": "2.0 小时后",
      "reason": "long-term high util",
      "muted_at": 1713890000.0
    }
  ]
}
```

### `POST /api/mutes` — 新增屏蔽
Body(JSON):
```json
{ "pool_key": "ethereum:aave_v3:USDT", "rule": null, "duration": "24h", "reason": "..." }
```
返回 `{ ok: true, mute: {...} }`。

### `DELETE /api/mutes` — 取消屏蔽
Query: `?pool_key=X&rule=Y`,`rule` 缺省=全部。
返回 `{ ok: true, removed: 1 }`。

---

## 前后端共识

- 前端默认以 5 秒间隔 poll `/api/status`(更新状态栏)
- 前端打开某 pool 时,同时发 overview / history / alerts 三个请求(activity / holders / permissions 由 tab 懒加载)
- 所有后端 RPC 调用都经过已有 `RpcPool`(含重试/熔断)
- 失败接口前端显示"数据不可用",不要刷红;错误日志只在控制台

## 端口
默认 **8787**,env 可配 `WEB_PORT=8787`。

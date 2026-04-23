# RPC 基础设施设计

> 对应 PRD 第 6.3 节

## 一、多节点分档

每条链的 RPC 池按**可靠性 + 成本**分三档:

| 档位 | 角色 | 选取原则 | 用途 |
|---|---|---|---|
| **Tier 1 (Primary)** | 主力 | 官方 RPC / 大厂公共 | 默认路由,承担 70% 流量 |
| **Tier 2 (Secondary)** | 常备备用 | 知名第三方公共 | 承担 30% 流量 + T1 挂了顶上 |
| **Tier 3 (Emergency)** | 应急 | Alchemy/Infura Free Tier | 仅当 T1+T2 全挂时启用 |

## 二、路由策略

**加权随机 + 健康度加权**

```
selectProvider(chain):
    candidates = providers[chain] filtered by:
        - status == HEALTHY
        - tier <= current_tier_level
        - not in cooldown
    if candidates empty:
        trigger escalation (T2→T3)
        if still empty:
            emit ALERT: RPC_POOL_EXHAUSTED
            return null
    return weighted_random(candidates, weights=effective_weight)

effective_weight = base_weight × health_score × (1 - current_load_ratio)
```

## 三、重试策略:四层递进

```
Layer 1 (同节点快重试): 相同 provider,100ms 后重试 1 次
         ↓ 仍失败
Layer 2 (换节点重试):   切到同 tier 下一个 provider,立即重试
         ↓ 仍失败
Layer 3 (指数退避):     500ms → 2s → 8s,最多 3 次,期间轮换节点
         ↓ 仍失败
Layer 4 (降级策略):     标记本次采集失败,用上次缓存值 + TTL 告警
```

## 四、重试决策表

| 错误类型 | HTTP/Error code | 是否重试 | 策略 |
|---|---|---|---|
| 网络超时 | ETIMEDOUT / ECONNRESET | ✅ | L1 → L2 |
| 限流 | 429 / -32005 | ✅ | 跳过 L1,直接 L2 |
| 节点过载 | 503 / 502 | ✅ | L2 + 扣健康分 |
| 区块未同步 | -32000 block not found | ✅ | L2 换节点 |
| RPC 执行错误 | -32015 execution reverted | ❌ | 合约层问题,不重试 |
| 参数错误 | -32602 | ❌ | 代码 bug,不重试 |
| 方法不支持 | -32601 | ✅ | L2 换节点 |
| 数据一致性错误 | block 不匹配 | ✅ | L2 + 强制锁定 block 重试 |

## 五、熔断器(Circuit Breaker)

```
状态机:
  CLOSED (正常)
    ↓ 连续失败 5 次 / 60s 内错误率 > 50%
  OPEN (熔断,冷却 60s,不派发请求)
    ↓ 冷却结束
  HALF_OPEN (试探,只派发 10% 流量)
    ↓ 10 次试探中 ≥ 8 次成功
  CLOSED
    ↓ 试探失败
  OPEN (冷却翻倍,最长 10min)
```

### 健康分(0-100,影响路由权重)
- 成功 +1(上限 100)
- 失败 -5
- 超时 -10
- 限流 -15
- < 30 进入 HALF_OPEN 观察
- < 10 强制 OPEN

## 六、幂等保障

我们只读不写,天然幂等。但仍需注意:
- 重试时**必须锁定 blockNumber**,不然 batch 里不同 call 拿到不同区块
- 重试超时要短(总耗时 ≤ 30s)

## 七、健康检查独立 Worker

```
每 30s:
  for each provider:
    t0 = now()
    try:
      blockNumber = eth_blockNumber()
      latency = now() - t0
      if latency > 3000ms: degraded
      if blockNumber lags > 5 blocks behind cluster median: stale
      if success: healthy
    catch:
      mark as unhealthy
    emit metric: rpc.health.{provider}
```

**集群共识块高**:取所有 healthy provider 的 blockNumber 中位数,落后太多的节点判定 **stale**。

## 八、可观测性指标

```
rpc_request_total{chain, provider, method, status}
rpc_request_duration_seconds{chain, provider, method}  # p50/p95/p99
rpc_retry_total{chain, provider, layer}
rpc_circuit_breaker_state{chain, provider}
rpc_health_score{chain, provider}
rpc_block_lag{chain, provider}
```

### 元告警(监控的监控)
| 触发条件 | 级别 |
|---|---|
| 单链所有 T1 provider 全部 OPEN | 🟠 |
| 单链所有 provider 全部 OPEN | 🔴(监控系统自身挂了) |
| 单个 provider 健康分 < 30 持续 5 分钟 | 🟡 |
| 全局 RPC 错误率 > 20% 持续 2 分钟 | 🟠 |
| T3 Emergency 被激活 | 🟡 |

## 九、各链公共 RPC 候选

| 链 | 候选公共 RPC |
|---|---|
| Ethereum | `cloudflare-eth.com` / `rpc.ankr.com/eth` / `ethereum.publicnode.com` / `eth.llamarpc.com` |
| Arbitrum | `arb1.arbitrum.io/rpc` / `rpc.ankr.com/arbitrum` / `arbitrum.publicnode.com` |
| Optimism | `mainnet.optimism.io` / `rpc.ankr.com/optimism` / `optimism.publicnode.com` |
| Base | `mainnet.base.org` / `base.publicnode.com` / `base.llamarpc.com` |
| Polygon | `polygon-rpc.com` / `rpc.ankr.com/polygon` / `polygon-bor.publicnode.com` |
| BNB | `bsc-dataseed.binance.org` / `rpc.ankr.com/bsc` / `bsc.publicnode.com` |
| Avalanche | `api.avax.network/ext/bc/C/rpc` / `avalanche-c-chain.publicnode.com` |
| Gnosis | `rpc.gnosischain.com` / `gnosis.publicnode.com` |
| Katana | ⚠️ 待实测,目前生态较新,公共 RPC 数量有限 |

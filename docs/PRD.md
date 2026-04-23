# DeFi 借贷池安全预警系统 PRD v0.1

> 项目名:web3_risk_dashboard
> 版本:v0.1 (2026-04-23)

## 1. 背景与目标

### 1.1 背景
Aave、Compound、Morpho、Spark 等主流借贷协议多链部署,单点事件(预言机操纵、抵押品脱锚、大鲸鱼挤兑)会通过清算机制和共享抵押品快速传染到多链。现有监控(DeFiLlama、Dune、Nansen)偏向事后分析,缺少**秒级-分钟级**的攻击前兆信号。

### 1.2 目标
构建一个**1分钟级刷新**的主动预警系统,在攻击发生的早期(5-15 分钟内)通过 TVL 突变、流动性异常、大户行为三个维度联动判定,推送分级预警。

### 1.3 非目标(v1 不做)
- 不做自动资金避险/自动平仓(只告警,不执行)
- 不做智能合约字节码层面的漏洞扫描
- 不做跨协议资金流追踪图谱(留给 v2)

---

## 2. 用户与场景

| 角色 | 使用场景 |
|---|---|
| 协议风控/Risk Steward | 及时发现本协议池子的异常,触发治理熔断提案 |
| DeFi 基金/做市商 | 接收预警后主动撤流动性、平仓杠杆 |
| 安全研究员 | 回溯攻击时间线,复盘事件传导路径 |
| 大户/鲸鱼 | 监控自己持有的抵押品资产是否被集中减持 |

---

## 3. 监控范围(v1)

### 3.1 协议白名单
- **Aave** v3(Ethereum / Arbitrum / Optimism / Base / Polygon / Avalanche / BNB)
- **Compound** v3(Ethereum / Arbitrum / Base / Polygon)
- **Morpho Blue**(Ethereum / Base / Katana)
- **Spark**(Ethereum / Gnosis)
- **Venus**(BNB Chain)
- **Radiant**(Arbitrum / BNB)

### 3.2 池子筛选规则
- TVL > $10M 的池子自动纳入
- 支持用户手动添加/移除(白名单 + 黑名单)
- 支持按资产类型过滤(stablecoin / LST / LRT / 蓝筹 / 长尾)

### 3.3 合约事件监控范围(Track B)

在 Track A(指标轮询)之外,增加基于链上事件订阅的合约安全监控。纳入监控的事件类型:

**3.3.1 高危权限事件**
- `OwnershipTransferred`(Ownable)
- `AdminChanged`(TransparentProxy)
- `Upgraded` / `BeaconUpgraded`(UUPS / Beacon Proxy)
- `RoleGranted` / `RoleRevoked`(AccessControl)
- `Paused` / `Unpaused`(Pausable)

**3.3.2 高危资金事件**
- ERC20 `Transfer`(单笔大额、短时间高频、目标为陌生地址)
- ERC20 `Approval` 后快速出现 `TransferFrom`
- LP `Burn` 速率明显高于 `Mint`

**3.3.3 高危池子事件**
- Uniswap v2/v3、Curve、Balancer 的 `Mint` / `Burn` / `Swap` / `Flash` / `Collect`
- 价格短时间大幅偏移
- `Flash` 与大额 `Swap` 在同一区块或短时间窗口内频繁出现

### 3.4 事件类 vs 指标类监控对比

| 维度 | Track A:指标轮询(原有) | Track B:事件订阅(新增) |
|---|---|---|
| 数据源 | RPC `eth_call` 读取合约状态 | RPC `eth_getLogs` / `eth_subscribe` |
| 触发方式 | 定时拉取(60s/180s/600s) | 区块事件驱动(秒级) |
| 主要对象 | 借贷池 TVL / 利用率 / 流动性深度 | ERC20 / Proxy / AccessControl / LP 事件 |
| 关注风险 | 资金规模异动、脱锚、挤兑 | 权限劫持、授权盗币、闪电贷、Rug pull |
| 规则特征 | 阈值 + 滑动窗口 | 事件组合 + 时间窗口 + 白名单 |
| 延迟目标 | ≤ 60s | ≤ 30s(目标同区块) |

两个 Track **互补且并行运行**,共享同一套 RPC 基础设施、存储、规则引擎与 Lark 告警通道。

---

## 4. 核心功能需求

### 4.1 TVL 监控(FR-01)
| 指标 | 说明 | 采集频率 |
|---|---|---|
| Total Supply | 池子总存款 | 1 min |
| Total Borrow | 池子总借出 | 1 min |
| Utilization Rate | Borrow / Supply | 1 min |
| Available Liquidity | Supply - Borrow | 1 min |
| TVL 变化率 | 1m / 5m / 1h / 24h | 滑动窗口 |

**预警规则**:
- 🟡 Warning: 5 分钟内 TVL 下降 > 5%
- 🟠 Alert: 5 分钟内 TVL 下降 > 15% 或 Utilization > 95%
- 🔴 Critical: 1 分钟内 TVL 下降 > 10% **且** 出现清算事件

### 4.2 流动性深度监控(FR-02)
- 抓取 Uniswap v3 / Curve / Balancer 等实时 pool state
- 计算滑点 1% / 2% / 5% 对应的可卖出量
- 计算清算可承受规模

**预警规则**:
- 🟠 Alert: 1% 滑点深度 < 池子可清算头寸的 20%
- 🔴 Critical: 出现脱锚(抵押品现价 vs Oracle 价格偏差 > 2%)

### 4.3 Top 20 持仓地址监控(FR-03)
- 拉取 aToken / cToken / Morpho position 的持有者排名
- 记录每个地址的 supply / borrow / net position / health factor
- 标签化已知地址,未知地址打 tag

**行为监控**:
| 行为 | 触发条件 | 预警级别 |
|---|---|---|
| 大额提款 | 单笔 > 池子 TVL 2% 或 > $5M | 🟡 |
| 连续提款 | 10 分钟内同一地址 3 次提款 | 🟠 |
| Health Factor 恶化 | HF 从 >1.5 跌至 <1.1 | 🟠 |
| Top 5 地址同步提款 | 5 分钟内 ≥3 个 Top 地址有提款动作 | 🔴 |
| 新建大额空头仓位 | 单地址新借出 > $10M | 🟡 |

### 4.4 筛选与订阅(FR-04)
- 按协议 / 链 / 池子 / 资产类型筛选
- 按告警级别订阅推送

### 4.5 推送通道(FR-05)
- **Lark 机器人**(v1 主通道)
- Telegram Bot (v2)
- Discord Webhook (v2)
- Email (Critical only, v2)
- 自定义 Webhook (v2)

### 4.6 告警分级体系(统一)

项目原有分级 `info / warning / alert / critical` 与新纳入的 `L0 / L1 / L2 / L3` **等价**,下表给出一一映射。规则引擎内部统一存储为整型等级(0/1/2/3),对外输出两套标识兼容历史配置。

| 新等级 | 旧等级 | 触发条件示例 | 通知方式 | 去重策略 |
|---|---|---|---|---|
| **L0** | info | 单次 Swap / 单次 Approval / 小额 Transfer | 仅写审计日志,不推送 | 不去重,落盘即可 |
| **L1** | warning | 单笔外流 > TVL 3–5%;5min 内 ≥3 笔异常 Transfer;新地址首次获得授权;单次 Burn > LP 3% | Lark(v1);Telegram(v2) | 同池同规则 10min 内去重 |
| **L2** | alert | 大额 Transfer + 陌生地址;Burn 持续 > Mint 且 > 3min;大额 Approval 后 5min 内出现 TransferFrom;Swap 价格偏移 > 10% | Lark + Telegram(v2) + 人工确认 | 同池同规则 5min 内去重 |
| **L3** | critical | AdminChanged/Upgraded 后 10min 内出现资金外流;连续 Burn 后价格暴跌 > 30%;Flash + 大额 Swap + 剧烈价格波动;Approval + TransferFrom 指向同一目标 | Lark + Telegram + Email + 值班电话(v2) | 不去重,逐条推送 |

说明:
- L0 是默认级别,所有事件均落库,便于后续回溯和基线计算。
- L1–L3 的"触发条件示例"在 FR-06 ~ FR-10 与附录 A 中给出具体规则 ID 和阈值。
- 同一事件可能同时命中多条规则,最终等级取 `max(matched_levels)`。

### 4.7 合约权限事件监控(FR-06)

监控对象:所有纳入白名单的协议核心合约(借贷池、金库、Router、Proxy)。

| 触发条件 | 示例事件 | 默认级别 | 规则 ID |
|---|---|---|---|
| `OwnershipTransferred` 触发,new owner 非白名单多签 | Aave PoolAddressesProvider | L2 | R-AUTH-01 |
| `AdminChanged` 触发 | TransparentProxy admin 变更 | L2 | R-AUTH-02 |
| `Upgraded` / `BeaconUpgraded` 触发 | UUPS 实现合约替换 | L2 | R-AUTH-03 |
| `RoleGranted(DEFAULT_ADMIN_ROLE, ...)` | 新地址获得 admin 角色 | L2 | R-AUTH-04 |
| `Paused` / `Unpaused` 触发 | 协议紧急暂停 | L1 | R-AUTH-05 |
| 权限事件 + 10min 内出现大额 Transfer | 组合判定 | **L3** | R-AUTH-10 |

实现要点:事件订阅走 Track B,订阅 topic0 过滤;未知地址通过 Etherscan label + 自维护库交叉比对。

### 4.8 资金流事件监控(FR-07)

监控对象:ERC20 Transfer / Approval / TransferFrom,聚焦协议金库、抵押品资产、治理代币。

| 触发条件 | 默认级别 | 规则 ID |
|---|---|---|
| 单笔 Transfer > 池 TVL 3% 或 > $5M | L1 | R-FLOW-01 |
| 5min 内同一 from 地址 ≥3 笔异常 Transfer | L1 | R-FLOW-02 |
| Transfer 目标为新地址(链上首次出现 < 24h) | L1 | R-FLOW-03 |
| 大额 `Approval` 后 5min 内出现相同 spender 的 `TransferFrom` | L2 | R-FLOW-10 |
| `Approval` + `TransferFrom` 指向同一非白名单地址 | **L3** | R-FLOW-11 |

去噪:白名单地址(多签、CEX、Router)跳过 R-FLOW-01 / R-FLOW-03。

### 4.9 流动性池事件监控(FR-08)

监控对象:Uniswap v2/v3、Curve、Balancer 的热门 LP(与 Track A 的抵押品资产相关的池)。

| 触发条件 | 默认级别 | 规则 ID |
|---|---|---|
| 单次 `Burn` > LP 总量 3% | L1 | R-POOL-01 |
| `Burn` 持续 > `Mint`,窗口 ≥ 3min | L2 | R-POOL-02 |
| `Swap` 导致价格偏移 > 10% | L2 | R-POOL-03 |
| 连续 `Burn` 后价格暴跌 > 30% | **L3** | R-POOL-10 |
| `Flash` + 大额 `Swap` 同区块 + 价格波动 > 5% | **L3** | R-POOL-11 |

价格来源:池内现价(sqrtPriceX96 / 储备比),与 Chainlink Oracle 交叉校验偏差。

### 4.10 白名单 + 行为基线去噪(FR-09)

误报控制的基础设施,所有 FR-06 ~ FR-08 规则在判定前先经过本节过滤。

- **白名单分类**:项目方多签 / CEX 存取款 / 常见 Router / Aggregator / 自营做市。
- **行为基线**:滚动窗口 7 天,记录每地址每合约的 Transfer / Swap / Approval 频次与金额分布。
- **动态阈值**:规则的绝对阈值与 `max(静态阈值, 基线均值 + 3σ)` 取较大者。
- **高频做市池单独建基线**,避免因常态大额 Swap 触发 L1/L2。
- 配置文件格式见附录 C。

### 4.11 事件组合规则引擎(FR-10)

单一事件原则上不直接判定 L3,必须满足事件组合 + 时间窗口。

- **时间窗口缓存**:Redis sorted set,key 按 `(chain, contract, event_type)` 分桶,保留最近 30min 事件。
- **组合判定**:规则引擎加载 YAML 配置,定义"事件 A 在窗口 W 内后接事件 B"的剧本(见附录 A)。
- **级别升降**:组合命中时将单事件的 L1/L2 升级为 L3;若在组合窗口内出现白名单动作(如项目方官方公告)可降级。
- **审计追踪**:每条告警附带命中的规则 ID、原始事件 hash 列表、时间窗口快照。

---

## 5. 非功能需求

| 维度 | 要求 |
|---|---|
| 数据刷新 | 核心池 ≤ 60s,普通池 ≤ 180s,边缘池 ≤ 600s;Critical 事件走事件订阅,目标 ≤ 30s |
| 可用性 | 99.5%(月停机 < 4h) |
| 数据完整性 | RPC 失败自动 failover,每链至少 3 个 provider |
| 历史回溯 | 所有指标保留 90 天原始数据,1 年聚合数据 |
| 准确率 | False positive < 10%,False negative(漏报 Critical)< 1% |
| 成本 | RPC 零成本(公共节点);存储 + 服务器月度 ~$80(v1) |
| RPC 可用性 | 单节点故障 30s 内自动切换 |
| 重试预算 | 单次数据采集总重试耗时 ≤ 30s |
| 熔断恢复 | OPEN → HALF_OPEN 默认 60s,失败后指数翻倍,最长 10min |

---

## 6. 技术选型

### 6.1 数据源(v1:公共节点)
- **链上数据**:公共 RPC,每链至少 3 个端点 failover
- **子图**:Aave / Compound / Morpho 官方 Subgraph(补全历史 + 衍生指标)
- **DEX 深度**:1inch API / 0x API / 直接读 Uniswap v3 pool state
- **Oracle 价格**:Chainlink on-chain read + CoinGecko 交叉验证
- **地址标签**:Etherscan label + Arkham + 自维护库

### 6.2 技术栈
| 模块 | 选型 |
|---|---|
| 运行时 | Python 3.11+ |
| 区块链交互 | web3.py (AsyncWeb3) |
| HTTP | httpx (async) |
| 配置 | PyYAML |
| 调度 | asyncio loop + apscheduler(可选) |
| 存储 | SQLite(v1 demo) → TimescaleDB(v2) |
| 通知 | Lark Bot Webhook |

### 6.3 RPC 基础设施(详见 `docs/RPC_INFRASTRUCTURE.md`)
- **多节点分三档**:T1 主力、T2 备用、T3 应急
- **路由策略**:加权随机 + 健康分动态调整
- **重试**:四层递进(同节点快重试 → 换节点 → 指数退避 → 降级)
- **熔断器**:每 provider 独立状态机
- **健康检查**:独立 Worker 每 30s 主动探测

### 6.4 架构
```
[RPC × N] [Subgraph] [DEX API] [Oracle]
    ↓          ↓           ↓         ↓
    └──→ Collector Workers (asyncio) ←──┘
              ↓
         SQLite / TimescaleDB  ←→  Redis (hot cache)
              ↓
      Rule Engine (定时 + 事件触发)
              ↓
      Alert Dispatcher ──→ Lark / TG / Discord / Webhook
              ↓
         API Gateway ──→ Web Dashboard (v2)
```

---

## 7. 决策记录

| ID | 决策 | 日期 |
|---|---|---|
| D-001 | 监控范围增加 Morpho Blue on Katana | 2026-04-23 |
| D-002 | v1 使用公共 RPC 节点,不引入付费档 | 2026-04-23 |
| D-003 | 配置多个公共节点 + 完整重试机制 | 2026-04-23 |
| D-004 | v1 demo 使用 Python + asyncio + SQLite | 2026-04-23 |
| D-005 | v1 主通道使用 Lark Bot Webhook | 2026-04-23 |
| D-006 | 合并合约事件监控 track,采用 L0-L3 分级 + 剧本规则 | 2026-04-23 |

---

## 8. 里程碑

| 阶段 | 交付物 | 预计工期 |
|---|---|---|
| **M1 (demo)** | 单链 Ethereum Aave v3 采集 + Lark 告警 | 当天 |
| M2 | 多链扩展(Arb/Base/OP/BNB) | 1 周 |
| M3 | Compound / Morpho / Spark 接入 | 2 周 |
| M4 | Top 20 持仓监控 | 1.5 周 |
| M5 | 流动性深度 + 脱锚检测 | 1.5 周 |
| M6 | Web Dashboard + 用户订阅 | 2 周 |
| M7 | Track B 事件订阅基础设施 + 权限事件监控(FR-06)+ 白名单(FR-09) | 2 周 |
| M8 | 攻击剧本规则引擎 + 组合判定(FR-07/FR-08/FR-10)+ 行为基线 | 2 周 |
| **总计** | **v1 MVP(含 Track A + Track B)** | **~12 周** |

---

## 9. 待确认问题(回滚入参)

- [x] Q1: Morpho 是否支持 Katana?→ **已加入**
- [x] Q2: v1 是否使用公共节点?→ **是**
- [x] Q3: 是否配置多节点 + 重试?→ **是**
- [ ] Q4: Katana 公共 RPC 可用性?→ **待实测**
- [ ] Q5: 是否接入 Alchemy Free Tier 作为 T3 兜底?→ **待定**
- [ ] Q6: v1 MVP 是否限定在 Ethereum Aave v3?→ **默认是**
- [ ] Q7: Track B 事件订阅使用 `eth_getLogs` 轮询还是 WebSocket `eth_subscribe`?→ **默认轮询,WS 作为可选增强**
- [ ] Q8: 白名单与行为基线是否共用同一张存储表?→ **待定**

---

## 附录 A:攻击模式剧本

每个剧本定义"事件组合 + 时间窗口 + 级别 + 命中规则 ID",由 FR-10 事件组合规则引擎执行。

### A.1 闪电贷攻击剧本(PLAY-FLASHLOAN)
- **涉及事件**:`Flash` + 大额 `Swap` + 价格偏移 > 5%
- **时间窗口**:同一区块或 ≤ 数秒
- **默认级别**:L3
- **命中规则**:R-POOL-11
- **触发条件**:在同一 tx 或同一 block 内,命中以上三个子事件即判 L3。
- **处置建议**:立即推送 Lark + Telegram;人工值班确认是否为白帽/套利。

### A.2 权限劫持剧本(PLAY-AUTHHIJACK)
- **涉及事件**:`AdminChanged` / `Upgraded` / `OwnershipTransferred` + 后续大额 `Transfer`
- **时间窗口**:权限变更后 ≤ 10 分钟
- **默认级别**:L3
- **命中规则**:R-AUTH-10(由 R-AUTH-01/02/03 + R-FLOW-01 联动升级)
- **触发条件**:权限事件落库后,启动 10min 窗口;窗口内出现 > TVL 3% 的 Transfer 即判 L3。
- **降级**:若 new owner 在白名单多签列表内,降级到 L1。

### A.3 Rug pull 剧本(PLAY-RUGPULL)
- **涉及事件**:连续 `Burn` + 价格暴跌 > 30%
- **时间窗口**:3–10 分钟
- **默认级别**:L3
- **命中规则**:R-POOL-10(由 R-POOL-01/02 + 价格数据联动升级)
- **触发条件**:窗口内 Burn 次数 ≥ 3 且累计 LP 销毁 > 20%,同时现价与 5min 前价格偏差 > 30%。

### A.4 授权盗币剧本(PLAY-APPROVALDRAIN)
- **涉及事件**:大额 `Approval` + 快速 `TransferFrom`
- **时间窗口**:授权到转账 1–5 分钟
- **默认级别**:L3
- **命中规则**:R-FLOW-11(由 R-FLOW-10 + 同目标地址联动升级)
- **触发条件**:Approval 的 spender 与后续 TransferFrom 的 `msg.sender` 一致,且 to 地址为非白名单;金额 > 用户历史均值 + 3σ。

---

## 附录 B:时间窗口表

规则引擎内统一使用的时间窗口常量,所有 FR-06 ~ FR-10 和附录 A 剧本均引用本表。

| 场景 | 起始事件 | 结束事件 | 窗口长度 | 用于规则 |
|---|---|---|---|---|
| 授权到转账 | `Approval` | `TransferFrom` | 1–5 分钟 | R-FLOW-10 / R-FLOW-11 / PLAY-APPROVALDRAIN |
| 升级到资金流出 | `Upgraded` / `AdminChanged` | 大额 `Transfer` | 5–10 分钟 | R-AUTH-10 / PLAY-AUTHHIJACK |
| LP 抽离 | 首次 `Burn` | 价格暴跌 | 3–10 分钟 | R-POOL-02 / R-POOL-10 / PLAY-RUGPULL |
| 闪电贷攻击 | `Flash` | 价格偏移 | 同一区块 / 数秒内 | R-POOL-11 / PLAY-FLASHLOAN |
| 连续异常 Transfer | 首次 Transfer | 第 N 笔 Transfer | 5 分钟 | R-FLOW-02 |

窗口长度可在配置文件中覆盖,默认值与本表一致。

---

## 附录 C:去噪与白名单

### C.1 白名单分类

| 类别 | 含义 | 示例 | 数据来源 |
|---|---|---|---|
| `multisig` | 项目方/协议官方多签 | Aave Governance Executor、Gnosis Safe | 官方文档 + Etherscan label |
| `cex` | 中心化交易所存取款热钱包 | Binance 14、Coinbase 10 | Arkham + Etherscan |
| `router` | 常见 Router / Aggregator | Uniswap UniversalRouter、1inch Router、0x Exchange Proxy | 协议文档 |
| `mm` | 自营/合作做市商 | Wintermute、GSR 地址集 | 内部维护 |
| `infra` | 桥、Relayer、跨链适配器 | LayerZero Relayer、Axelar Gateway | 官方公告 |

### C.2 行为基线计算方法

- **采样窗口**:滚动 7 天。
- **聚合粒度**:`(address, contract, event_type)` 三元组。
- **统计量**:每日次数 `count_daily`、每日金额均值 `mean_amount_daily`、标准差 `std_amount_daily`。
- **动态阈值**:`threshold = max(静态阈值, mean + k*std)`,默认 `k = 3`。
- **冷启动**:新地址/新合约样本不足 3 天时,退化到纯静态阈值。
- **重算频率**:每日 UTC 00:00 重算一次;热门做市池每 6h 重算。

### C.3 白名单配置文件格式建议

路径:`config/whitelist.yaml`

```yaml
# 白名单配置,供 FR-09 去噪系统加载
version: 1
updated_at: 2026-04-23
entries:
  - address: "0x25F2226B597E8F9514B3F68F00f494cF4f286491"
    chain: ethereum
    category: multisig
    label: "Aave Governance Executor (short)"
    source: "https://docs.aave.com/..."
    effective_from: 2024-01-01

  - address: "0x28C6c06298d514Db089934071355E5743bf21d60"
    chain: ethereum
    category: cex
    label: "Binance 14"
    source: "etherscan-label"

  - address: "0x66a9893cC07D91D95644AEDD05D03f95e1dBA8Af"
    chain: ethereum
    category: router
    label: "Uniswap UniversalRouter v4"

rules:
  # 命中白名单时,下列规则默认跳过或降级
  skip_rules: [R-FLOW-01, R-FLOW-03]
  downgrade_rules:
    R-AUTH-10: L1    # 权限变更目标若为 multisig,则从 L3 降到 L1
```

基线数据独立存储在 `baseline` 表中,不进配置文件,由 FR-09 系统自动维护。

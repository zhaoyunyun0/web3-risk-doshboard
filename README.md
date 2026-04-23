# web3_risk_dashboard

DeFi 借贷池安全预警系统 — 监控 Aave v3 / Compound / Morpho / Spark 等主流协议,
通过 TVL、utilization、流动性、大户持仓等维度提前发现攻击或异常流出,并推送 Lark 告警。

当前版本 **v0.1 demo**,覆盖 Ethereum + Aave v3。更多协议/链按 PRD 路线图逐步扩展。

完整需求:[docs/PRD.md](docs/PRD.md)
RPC 基础设施详设:[docs/RPC_INFRASTRUCTURE.md](docs/RPC_INFRASTRUCTURE.md)

---

## 🚀 一键启动(最小路径)

```bash
cd ~/web3_risk_dashboard

# 首次:复制 .env,填入 LARK_WEBHOOK_URL
cp .env.example .env
vim .env

# 监控 + Web Dashboard 一键全起
./w3risk up

# 打开浏览器
open http://localhost:8787
```

分开启停也行:

```bash
# 只起监控(Lark 告警)
./w3risk start        # ./w3risk stop

# 只起 Web Dashboard
./w3risk web          # ./w3risk web-stop

# 全部一起
./w3risk up           # ./w3risk down
```

`./w3risk` 命令总览:

```
一键生命周期:   up / down
监控进程:       start / stop / restart / status / logs
Web Dashboard:  web / web-stop
自检:           probe / snapshot / test-lark
告警屏蔽降噪:   mutes / mute / unmute
```

运行 `./w3risk help` 看完整示例。

---

## 特性(demo 已实现)

- ✅ 多公共 RPC 节点池(每链 3-5 个),加权随机 + 健康分路由
- ✅ 四层重试 + 独立熔断器(每 provider)
- ✅ Aave v3 每 reserve 采集:`total_supply` / `total_debt` / `utilization` / `oracle price` / USD TVL
- ✅ 规则引擎:TVL 下跌、utilization 过高、borrow 激增、liquidity drain
- ✅ Lark Bot Webhook 告警(interactive card,按级别着色)
- ✅ 按协议/链/资产 symbol 过滤(env + yaml)
- ✅ 告警去重(同一 pool+rule 5 分钟内只推一次)
- ✅ SQLite 持久化(snapshots + alerts,7 天保留,启动 bootstrap 回加载历史)
- ✅ 兼容 MKR 这类 `bytes32 symbol` 老合约(string → bytes32 fallback)
- ✅ 干净退出:signal handler + 所有 aiohttp session 显式关闭
- ✅ **Web Dashboard**:FastAPI + 单页 SPA,6 个 tab(概览 / 趋势 / 活动 / 持仓 / 权限 / 告警)
- ✅ **大额活动 on-demand 查询**:Supply / Withdraw / Borrow / Repay / LiquidationCall 事件实时拉取
- ✅ **Top 20 近期净流入排名**:按 24h Supply-Withdraw 净流向聚合
- ✅ **协议权限监控**:PoolAddressesProvider 的 9 个权限事件(PoolUpdated、ACLAdminUpdated 等)
- ✅ **Track B 链上事件实时告警**:主循环每 tick `eth_getLogs` 权限 + 代理升级事件,新事件推 Lark L2 卡片(PRD FR-06)

## 未做(v1 计划中)

- ❌ 静态 aToken 余额排名(当前用"近期净流入"代替,Subgraph 接入后可切换)
- ❌ 多协议(Compound / Morpho / Spark 骨架已预留)
- ❌ DEX 流动性深度 + 脱锚检测
- ❌ Dashboard 实时推送(目前 tab 数据 5s polling,下一步接 WebSocket)

---

## 目录结构

```
web3_risk_dashboard/
├── README.md
├── requirements.txt
├── .env.example
├── config/
│   ├── rpc.yaml           # 每链的多个公共节点配置
│   ├── protocols.yaml     # PoolAddressesProvider 地址 + 资产白名单
│   └── rules.yaml         # 告警规则阈值
├── docs/
│   ├── PRD.md
│   └── RPC_INFRASTRUCTURE.md
├── src/
│   ├── abis.py            # 最小 ABI
│   ├── config.py          # 配置加载
│   ├── logger.py
│   ├── circuit_breaker.py # 熔断器 + 健康分
│   ├── rpc_pool.py        # 多节点路由 + 重试
│   ├── aave_v3_collector.py
│   ├── snapshot_store.py  # 滚动历史
│   ├── rule_engine.py     # 规则评估
│   ├── lark_notifier.py   # Lark Bot webhook
│   └── main.py            # CLI 入口 + 主循环
└── data/  logs/           # 运行时目录(git-ignored)
```

---

## 快速开始

### 1. 安装依赖

```bash
cd ~/web3_risk_dashboard

# 建议用虚拟环境
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env,填入 Lark webhook URL
# LARK_WEBHOOK_URL=https://open.larksuite.com/open-apis/bot/v2/hook/xxxx
```

可选环境变量(见 `.env.example`):
- `ENABLED_CHAINS` — 仅启用某些链,默认 `ethereum`
- `ALERT_MIN_LEVEL` — 最低推送级别:`info|warning|alert|critical`,默认 `warning`
- `COLLECT_INTERVAL_SEC` — 采集间隔秒,默认 60
- `LARK_PUSH_INFO=true` — 开启心跳推送(每 10 tick 一次)

### 3. 自检(不改动生产,先跑诊断)

```bash
# 验证 RPC 节点可达性
./w3risk probe

# 验证 Lark webhook 能收到消息(会额外发一条中文卡片样例)
./w3risk test-lark

# 拉一次 Aave v3 reserves,打印现状(不推送)
./w3risk snapshot
```

`snapshot` 命令大致输出:
```
=== chain: ethereum ===
  WETH     supply=$ 2,134,556,123 borrow=$   834,211,000 util=39.08% price=$  3,215.4321 block=22510123
  USDC     supply=$ 1,564,332,000 borrow=$ 1,389,221,000 util=88.80% price=$      1.0000 block=22510123
  ...
```

### 4. 启动监控循环

```bash
./w3risk start   # 后台启动,推荐
./w3risk status  # 检查
./w3risk logs    # tail -f
./w3risk stop    # 优雅停止

# 如果想前台跑(方便调试)
python -m src.main run
```

启动后 Lark 群会收到一条 `🚀 w3_risk_dashboard 已启动` 消息,之后每 60 秒拉一次数据、按规则评估、只推送触发的告警。告警卡片为中文,按级别着色:🟡预警 / 🟠告警 / 🔴严重。

---

## 告警规则(`config/rules.yaml`)

| 类型 | 示例规则 | 级别 |
|---|---|---|
| **tvl_drop** | 5 分钟内 TVL 跌 > 5% | warning |
|  | 5 分钟内 TVL 跌 > 15% | alert |
|  | 1 分钟内 TVL 跌 > 10% | critical |
| **utilization** | utilization > 95% | alert |
|  | utilization > 99% | critical |
| **borrow_surge** | 5 分钟借款增 > 20% | warning |
|  | 1 分钟借款增 > 10% | alert |
| **liquidity_drain** | 5 分钟可用流动性跌 > 30% | alert |
|  | 1 分钟可用流动性跌 > 20% | critical |

规则都可在 `rules.yaml` 里改阈值、加新规则。**同一 (pool, rule) 5 分钟内只推送一次**。

---

## Web Dashboard

```bash
./w3risk web     # 启动 (默认 8787)
open http://localhost:8787
```

页面结构:

```
┌───────────────────────────────────────────────────────┐
│ 🛡 w3_risk_dashboard              状态栏               │
├─────────────┬─────────────────────────────────────────┤
│ ▾ Aave v3   │ Aave v3 · 以太坊 · USDC                  │
│   ▾ 以太坊   │ ┌─[概览][趋势][活动][持仓][权限][告警]─┐ │
│     • USDC ▰│ │                                     │ │
│     • USDT  │ │  <当前 tab>                          │ │
│     • WETH  │ │                                     │ │
│     ...     │ └─────────────────────────────────────┘ │
├─────────────┴─────────────────────────────────────────┤
│ 📢 最近告警(全局,30s 自动刷新)                       │
└───────────────────────────────────────────────────────┘
```

6 个 tab 数据源:

| Tab | 数据 | 来源 | 缓存 |
|---|---|---|---|
| **概览** | 当前存款 / 借款 / 利用率 / 流动性 / 价格 / 区块 | SQLite 最新快照 | 无 |
| **趋势** | 24h / 6h / 1h / 7d 多线图 | SQLite history | 无 |
| **大额活动** | Supply / Withdraw / Borrow / Repay / LiquidationCall 事件 | `eth_getLogs` on-demand | 30s |
| **持仓** | 近 24h 净流入排名 Top 20(Supply - Withdraw) | `eth_getLogs` 聚合 | 5min |
| **权限** | PoolAddressesProvider 的权限事件 | `eth_getLogs` | 60s |
| **告警** | 该池子历史告警 | SQLite `alerts` 表 | 无 |

API 文档:`docs/WEB_API_CONTRACT.md`

前端特性:
- 深色主题,数据密度高(走 Grafana / Bloomberg 风格)
- 地址/hash 自动缩写,hover 看完整,tx_hash 跳 Etherscan
- URL hash 路由:`#/pool/ethereum:aave_v3:USDC/activity` 刷新不丢状态
- Mock 模式:`?mock=1` 不启后端也能看全部 UI,方便前端二开

---

## Track B:链上事件监控

除 Track A(指标轮询)外,运行循环里内置事件线:每 tick 对 PoolAddressesProvider + Pool 代理合约做 `eth_getLogs`,命中的事件经去重后:

- 入库 `events` 表(UNIQUE `(chain, tx_hash, log_index)`)
- 按 `rules.yaml` 的 `events.levels` 映射级别(默认 `alert`/`warning`)
- 新事件推 Lark 中文卡片(标题如 "🟠 告警 · 链上权限事件 · 代理合约已升级(实现替换)")

**监听事件**:
- PAP 权限:`OwnershipTransferred` / `PoolUpdated` / `PoolConfiguratorUpdated` / `PriceOracleUpdated` / `ACLManagerUpdated` / `ACLAdminUpdated` / `PoolDataProviderUpdated` / `AddressSet` / `AddressSetAsProxy` / `ProxyCreated`
- Pool 代理(ERC1967 + Pausable):`AdminChanged` / `Upgraded` / `BeaconUpgraded` / `Paused` / `Unpaused`

**首次启动不推 Lark**:回看 ~1 周(ETH,L2 按 50k 块上限约 1 天)把历史事件静默入库,只有启动之后新发生的事件才会推送,避免重启刷屏。游标存 `event_cursors` 表(按 `(chain, contract)`),重启续跑。

在 `config/rules.yaml` 的 `events.levels` 段可改每个事件的默认级别。

---

## 告警屏蔽(降噪)

部分池子(如 Aave v3 的 WETH / USDT 长期 99%+ utilization)是市场正常状态,会反复触发高级告警。用 mute 降噪:

```bash
# 列出当前屏蔽
./w3risk mutes

# 屏蔽 USDT 所有告警 24 小时,并记录原因
./w3risk mute ethereum:aave_v3:USDT --duration 24h --reason "长期高利用率"

# 仅屏蔽 WETH 的某条规则,永久(不带 --duration)
./w3risk mute ethereum:aave_v3:WETH --rule utilization_99pct --reason "利率模型正常"

# 取消屏蔽
./w3risk unmute ethereum:aave_v3:USDT
./w3risk unmute ethereum:aave_v3:WETH --rule utilization_99pct
```

屏蔽规则存在 `data/mutes.yaml`,进程重启后自动加载;过期条目在运行时自动清理。

被屏蔽的告警**不入库也不推送**(只在日志里 `alert muted ...` 标记一行)。

---

## 持久化(SQLite)

启动 `run` 会自动在 `data/snapshots.db` 建库(WAL 模式),两张表:

- `reserve_snapshots`:每个 tick 每个 reserve 一行,字段含 `ts / pool_key / chain / protocol / symbol / asset / block_number / supply_usd / borrow_usd / available_liquidity_usd / utilization_pct / price_usd`
- `alerts`:每次触发一行,`ts / level / rule / pool_key / message / metrics(JSON)`

默认保留 7 天,启动时自动 prune 旧数据。**重启后会从 DB 读回最近 1 小时的历史**写入内存 store,规则引擎的滑动窗口比对因此跨进程连续。

手工查询示例:
```bash
sqlite3 data/snapshots.db "SELECT symbol, ts, utilization_pct FROM reserve_snapshots WHERE pool_key='ethereum:aave_v3:USDC' ORDER BY ts DESC LIMIT 5;"
sqlite3 data/snapshots.db "SELECT ts, level, rule, pool_key FROM alerts ORDER BY ts DESC LIMIT 10;"
```

---

## 扩展指南

### 添加新链(比如 Arbitrum)

1. `config/rpc.yaml` 的 `chains.arbitrum` 已经预配置好 4 个公共节点
2. `config/protocols.yaml` 的 `aave_v3.arbitrum` 已有 PoolAddressesProvider
3. `.env` 里设置 `ENABLED_CHAINS=ethereum,arbitrum`
4. 重启

### 添加新协议(比如 Compound)

参考 `src/aave_v3_collector.py` 的结构,写一个 `compound_v3_collector.py`,在 `ChainWorker.init` 里实例化。

### 添加 Katana 链(Morpho)

Katana 生态较新,先用 `probe` 验证公共 RPC 可用性:
```bash
# 在 config/rpc.yaml 加入 katana 的候选节点
# 运行:
ENABLED_CHAINS=katana python -m src.main probe
```

---

## 故障排查

| 现象 | 可能原因 |
|---|---|
| `no workers initialized` | 所有 RPC 节点都挂了,或 `ENABLED_CHAINS` 写错 |
| `collector not initialized` | 启动期合约调用失败,见日志 |
| Lark 收不到消息 | webhook URL 错,或群开启了签名校验(需要 secret) |
| `execution reverted` | 合约调用参数错,通常是 PoolAddressesProvider 地址不对 |
| 全部 reserves 采集失败 | DataProvider 地址解析失败,检查 PoolAddressesProvider |

日志级别改成 `DEBUG`:
```bash
LOG_LEVEL=DEBUG python -m src.main run
```

---

## 下一步计划

按 PRD M2-M6 推进:
- M2: 多链扩展(Arb/Base/OP/BNB)
- M3: Compound / Morpho / Spark
- M4: Top 20 持仓监控
- M5: DEX 流动性深度 + 脱锚检测
- M6: Web Dashboard + 订阅系统

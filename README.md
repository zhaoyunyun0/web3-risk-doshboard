# web3_risk_dashboard

DeFi 借贷池安全预警系统 — 监控 Aave v3 / Compound / Morpho / Spark 等主流协议,
通过 TVL、utilization、流动性、大户持仓、链上权限变更等维度提前发现攻击或异常流出,
并推送 Lark 告警。配套 Web Dashboard(深色主题,类 Grafana/Bloomberg 密度)。

**当前版本**:v0.2 — 支持 **Ethereum + Avalanche C-Chain**(7 条链 RPC 预配),
Dashboard 已具备首页总览 / 全局搜索(Cmd+K) / 告警聚合 / Mute 跨进程同步 /
堆叠+对比趋势图切换 / 活动净流统计 等能力。更多协议/链按 PRD 路线图逐步扩展。

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

## 特性

### 🛡 采集 + 告警引擎

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

### 🔗 多链 + 权限监控

- ✅ **多链就绪**:7 条链 RPC 与 Aave v3 地址已预配 — Ethereum / Arbitrum / Optimism / Base / BNB / Polygon / **Avalanche C-Chain**。`.env` 的 `ENABLED_CHAINS` 逗号分隔即可开启(目前默认 `ethereum,avaxc`)
- ✅ **协议权限监控**:PoolAddressesProvider 的 9 个权限事件(PoolUpdated、ACLAdminUpdated 等)
- ✅ **Track B 链上事件实时告警**:主循环每 tick `eth_getLogs` 权限 + 代理升级事件,新事件推 Lark L2 卡片(PRD FR-06)
- ✅ **权限页扩展扫描**:除 PAP 外,还扫 PoolConfigurator / ACLManager / Pool 代理(SupplyCap/BorrowCap/ReservePaused/RoleGranted/Upgraded 等)

### 🌐 Web Dashboard

- ✅ **FastAPI + 单页 SPA**,单文件 `src/web/static/index.html`,无构建步骤
- ✅ **首页总览**(默认路由):KPI 卡 + 告警最多 top 10 + 利用率 TOP 8 + 流动性紧张 TOP 8 + 全局屏蔽表 + 最近告警,60s 自刷
- ✅ **6 个详情 tab**(按频次排序):告警 / 概览 / 趋势 / 活动 / 持仓 / 权限
- ✅ **侧栏告警徽章**:每个池子名旁实时显示 1h 激活告警数(已屏蔽的不计),45s 自刷
- ✅ **Cmd/Ctrl+K 全局搜索**:模糊匹配 symbol / pool_key / 链 / 协议,键盘导航
- ✅ **趋势图双模式**:对比(三条线独立 y 轴 auto-scale,稳定币池放大小幅波动)/ 堆叠(Borrow+Liquidity≈Supply 展示结构);新接入池子数据不足时横幅提示
- ✅ **告警聚合去重**:同 (rule,level, 60s 窗) 合并为 `×N` 展开可见组内每条
- ✅ **大额活动 on-demand 查询**:Supply / Withdraw / Borrow / Repay / LiquidationCall 事件实时拉取,顶部 4 张净流卡(流入/流出/清算/净差值)
- ✅ **Top 20 近期净流入排名**:按 24h Supply-Withdraw 净流向聚合
- ✅ **告警"已屏蔽"状态回显**:告警列表逐行显示"✅ 屏蔽中 · 剩余时间"标签 + 取消屏蔽按钮
- ✅ **Design tokens 化**:全站颜色/间距/字号/阴影走 CSS 变量,易于维护
- ✅ **状态栏分层 chip**:监控 / 告警(>100 红,>0 橙) / 快照 / 链 / WS 五个独立 chip
- ✅ **Skeleton 骨架屏**:表格/图表/KPI 各自有 shimmer 占位
- ✅ **侧栏折叠持久化**:localStorage,刷新保持
- ✅ **WebSocket 实时推送**:Dashboard 状态栏与"最近告警"通过 `/api/ws/stream` 服务端 push 更新;WS 断开自动退回 5s/30s 轮询兜底
- ✅ **禁用浏览器缓存**:`/` 响应 `Cache-Control: no-cache`,避免旧 JS 残留

### 🔕 Mute(告警屏蔽)

- ✅ **CLI + Web UI 双入口**:支持整池屏蔽或按 (pool, rule) 精确屏蔽,带过期时间
- ✅ **跨进程自动同步**:监控进程每次 `find()` 检查 `mutes.yaml` mtime,Web UI 新加的屏蔽 ≤60 秒内监控进程生效(之前需重启才行,v0.2 修复)
- ✅ **过期自动清理**:到期条目在运行时自动 prune

## 未做(v1 计划中)

- 🟡 静态 aToken 余额排名(代码就绪,见下文 M4 段;配好 The Graph key 即开启)
- 🟡 大额存/取款单笔告警(阈值已设计,实施中)
- 🟡 中英双语界面(规划中)
- ❌ 多协议(Compound / Morpho / Spark 骨架已预留)
- ❌ DEX 流动性深度 + 脱锚检测

---

## 目录结构

```
web3_risk_dashboard/
├── README.md
├── requirements.txt
├── .env.example
├── w3risk                 # CLI 入口(start/stop/up/down/mute/probe 等)
├── config/
│   ├── rpc.yaml           # 每链的多个公共节点配置(7 条链)
│   ├── protocols.yaml     # PoolAddressesProvider 地址 + 资产白名单
│   └── rules.yaml         # 告警规则阈值 + 事件级别映射
├── docs/
│   ├── PRD.md
│   ├── RPC_INFRASTRUCTURE.md
│   └── WEB_API_CONTRACT.md
├── src/
│   ├── abis.py                  # 最小 ABI
│   ├── config.py                # 配置加载
│   ├── logger.py
│   ├── circuit_breaker.py       # 熔断器 + 健康分
│   ├── rpc_pool.py              # 多节点路由 + 重试
│   ├── aave_v3_collector.py     # Track A:pool 快照采集
│   ├── event_tracker.py         # Track B:链上事件监听
│   ├── events.py                # 事件 ABI + topic0 + 中文描述
│   ├── snapshot_store.py        # 滚动历史
│   ├── rule_engine.py           # 规则评估
│   ├── mute_store.py            # 告警屏蔽(跨进程同步)
│   ├── hidden_pools.py          # 池子软删除
│   ├── lark_notifier.py         # Lark Bot webhook
│   ├── sqlite_sink.py           # SQLite 持久化
│   ├── holders_subgraph.py      # The Graph 持仓查询
│   ├── main.py                  # CLI 入口 + 主循环
│   └── web/
│       ├── api.py               # FastAPI 路由
│       ├── on_demand.py         # 活动/持仓/权限 的 eth_getLogs 查询
│       ├── resolver.py          # Aave 部署地址懒解析(Pool/PoolConfigurator/ACLManager 等)
│       ├── permission_abis.py   # PoolConfigurator/ACLManager/Pool 代理事件定义
│       ├── cache.py             # 活动/持仓/权限响应缓存
│       ├── ws_hub.py            # WebSocket 广播
│       └── static/index.html    # 前端单文件 SPA(约 2000 行)
└── data/  logs/                 # 运行时目录(git-ignored)
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
- `ENABLED_CHAINS` — 仅启用某些链,默认 `ethereum,avaxc`(可选值:`ethereum`/`arbitrum`/`optimism`/`base`/`polygon`/`bnb`/`avaxc`)
- `ALERT_MIN_LEVEL` — 最低推送级别:`info|warning|alert|critical`,默认 `warning`
- `COLLECT_INTERVAL_SEC` — 采集间隔秒,默认 60
- `LARK_PUSH_INFO=true` — 开启心跳推送(每 10 tick 一次)
- `THE_GRAPH_AAVE_V3_URL_<CHAIN>` — The Graph subgraph URL,用于持仓静态排名(可选,见下文 M4 段)

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

### Track A:快照阈值规则(已实现)

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

### Track B:链上事件告警(已实现,见下文 Track B 段)

### 计划中:大额单笔转账告警(v0.3)

- `large_withdraw_1m` 单笔流出(Withdraw/Borrow) ≥ $1M → warning
- `large_withdraw_5m` 单笔流出 ≥ $5M → alert
- `large_withdraw_10m` 单笔流出 ≥ $10M → critical
- avaxc 等小池(TVL < $100M)可叠加**占 TVL 百分比**阈值,防止固定金额对小池子不敏感
- 流入(Supply/Repay)暂不计入告警,按需再加

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
│ 🛡 w3_risk_dashboard    🟢监控 🚨告警 💾快照 🔗链 ⚡WS │
├─────────────┬─────────────────────────────────────────┤
│ 🏠 总览 [N] │ Aave v3 · 以太坊 · USDC                  │
│ [搜索Cmd+K] │ ┌─[告警][概览][趋势][活动][持仓][权限]─┐ │
│ ▾ Aave v3   │ │                                     │ │
│   ▾ avaxc   │ │  <当前 tab>                          │ │
│     • USDt ●│ │                                     │ │
│   ▾ 以太坊  │ │                                     │ │
│     • USDC ●│ └─────────────────────────────────────┘ │
│     ...     │                                         │
├─────────────┴─────────────────────────────────────────┤
│ 📢 最近告警(全局,30s 自动刷新,同规则 60s 窗聚合 ×N)  │
└───────────────────────────────────────────────────────┘
```

### 首页"总览"(默认路由)

无 hash 时默认进入,包含:
- **5 张 KPI 卡**:池子总数 / TVL / 加权利用率 / 激活告警数 / 屏蔽数
- **告警最多的池子 Top 10**(最近 1h,排除屏蔽)
- **利用率 TOP 8**(进度条可视化)
- **流动性紧张 TOP 8**(available / supply 比例)
- **全局激活屏蔽规则表**(所有 chain/protocol)
- **近 10 条告警**
- 60s 自刷

### 6 个详情 tab(按使用频次排序)

| Tab | 数据 | 来源 | 缓存 |
|---|---|---|---|
| **告警** | 该池子历史告警 + 已屏蔽状态回显 | SQLite `alerts` 表 + `/api/mutes` | 无 |
| **概览** | 当前存款 / 借款 / 利用率 / 流动性 / 价格 / 区块 + Mute 面板 + 软删除 | SQLite 最新快照 | 无 |
| **趋势** | 24h / 6h / 1h / 7d 多线图,对比/堆叠双模式 | SQLite history | 无 |
| **活动** | Supply / Withdraw / Borrow / Repay / LiquidationCall 事件 + 净流 4 卡 | `eth_getLogs` on-demand | 30s |
| **持仓** | 近 24h 净流入排名 Top 20(Supply - Withdraw) | `eth_getLogs` 聚合 / The Graph | 5min |
| **权限** | 协议级权限变更 · 24h/3d/7d 切换 · 带进度计时 | `eth_getLogs` | 60s |

API 文档:`docs/WEB_API_CONTRACT.md`

### 快捷键

| 快捷键 | 行为 |
|---|---|
| `Cmd+K` / `Ctrl+K` | 打开全局搜索(模糊匹配 symbol/链/协议) |
| `↑` / `↓` / `Enter` / `Esc` | 搜索面板内导航 |

### 前端特性

- 深色主题 + design tokens(CSS 变量,易改色板)
- 数据密度高(走 Grafana / Bloomberg 风格)
- 地址/hash 自动缩写,hover 看完整,tx_hash 跳 Etherscan
- URL hash 路由:`#/pool/ethereum:aave_v3:USDC/activity` 刷新不丢状态
- 侧栏告警徽章 45s 自刷(已屏蔽的不计)
- 侧栏树折叠状态持久化(localStorage)
- Skeleton 骨架屏(表格/图表/KPI 各自的 shimmer)
- Mock 模式:`?mock=1` 不启后端也能看全部 UI,方便前端二开

---

## M4:Top 20 静态余额(The Graph)

持仓 tab 现在支持两种数据源,顶部按钮一键切换:

| 模式 | 数据来源 | 内容 | 配置 |
|---|---|---|---|
| `subgraph` | Aave v3 官方 subgraph | aToken 余额(含累积利息)的**静态快照排名** | 需 The Graph API key |
| `net_flow`(默认) | RPC `eth_getLogs` | 最近 N 小时的 Supply - Withdraw 净流入 | 无需额外配置 |

**启用 subgraph 模式**:

1. 在 https://thegraph.com/studio/apikeys/ 生成 API key
2. 在 https://aave.com/docs/developers/smart-contracts/subgraph 查到每条链的 Aave v3 subgraph ID
3. `.env` 加一行(每条链独立,空则该链走 net_flow):

```
THE_GRAPH_AAVE_V3_URL_ETHEREUM=https://gateway.thegraph.com/api/<KEY>/subgraphs/id/<ID>
# 其它链:..._ARBITRUM, ..._OPTIMISM, ..._BASE, ..._POLYGON, ..._BNB
```

4. 重启 `./w3risk web`,持仓 tab 默认自动切到静态余额;未配置的链照常降级 `net_flow`

实现上,subgraph 查 `userReserves` 的 `scaledATokenBalance` 前 20,用 `reserve.liquidityIndex` 乘回真实余额(对应链上 `aToken.balanceOf()`)。

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

屏蔽规则存在 `data/mutes.yaml`,**监控进程 + Web 进程跨进程自动同步**(文件 mtime 检测,变化即 reload,延迟 ≤60 秒)。过期条目在运行时自动清理。

被屏蔽的告警**不入库也不推送**(只在日志里 `alert muted ...` 标记一行)。

**在 Web UI 屏蔽**:概览 tab 的"告警屏蔽"面板,或告警 tab 每行的"🔕 屏蔽 7d"按钮 — 点击后即刻写 `mutes.yaml`,监控进程最多 60 秒内感知(下轮采集 tick)。

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

按 PRD 推进:
- **v0.3** 大额存/取款单笔告警:单笔 ≥ $1M warning / ≥ $5M alert / ≥ $10M critical,avaxc 可叠加"占池 TVL 百分比"维度(因池子较小,固定金额阈值偏保守)
- **v0.3** 权限页"旧值→新值"人类可读化:Cap 数字千分位、Paused 的 boolean、role bytes32 解码
- **v0.3** 中英双语界面 + 顶栏一键切换(localStorage 持久化)
- **v0.4** 多协议:Compound / Morpho / Spark
- **v0.5** DEX 流动性深度 + 脱锚检测
- **v0.6** 订阅系统(WxBot/Slack/Webhook generic)

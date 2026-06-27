# vnpy · mini

给 [vnpy](https://github.com/vnpy/vnpy) 套的一个**极简（minimalism）Web 界面**。

后端不重复造轮子——交易/行情/撮合全部交给 vnpy 的 `MainEngine`；这里只做一层很薄的
FastAPI 桥接：把 vnpy 的实时事件用 WebSocket 推给浏览器，把浏览器的下单/撤单/订阅
转成 vnpy 的请求对象。前端是**纯原生 JS，无框架、无构建步骤**，黑白 + 蓝色极简风格。

功能
----
- 账户权益 / 可用 / 浮动盈亏实时刷新
- 行情订阅 + 自选列表（点击某行即选中到图表/下单/策略）
- **实时走势图**（原生 canvas，无第三方库）
- 下单（买卖 / 开平分段选择，限价）、委托列表、单笔撤单、**一键全撤**
- 持仓列表（净额持仓）+ **一键平仓**
- 成交回报、运行日志
- **策略引擎**：内置「双均线交叉」示例策略，可填合约/快慢线/手数后启动，
  在引擎内消费实时行情自动下单，列表可见净仓与触发次数，支持一键停止
  （mock 与 live 通用——换成你自己的策略只需改 `MAStrategy`）

```
vnpy-mini/
├── server/app.py     # FastAPI 桥接层（含 mock 与 live 两套引擎）+ AI/信号/调度 接口
├── web/              # 极简前端：index.html / style.css / app.js
├── research/         # ★ 研究层：A股多因子回测/验证/防过拟合 + 组合风控（research/README.md）
├── advisor/          # ★ AI 顾问：日频上下文 → Claude 决策（含 mock 兜底）
├── scheduler/        # ★ 交易日开盘/收盘调度，到点自动跑顾问例程
├── requirements.txt
└── README.md
```

> **三层定位**：`research/` 是研究的"脑"（找因子、严谨回测、防过拟合、产出选股信号 + 组合风控），
> `advisor/`+`scheduler/` 是"日频决策中枢"（到点汇总市场→交给 Claude→给出操作建议），
> `server/`+`web/` 是执行与监控的"脸"（模拟盘/实盘下单）。
> 链路：**调度器到点 → 汇总(持仓+量化信号+大盘+新闻) → Claude 顾问决策 → 界面一键执行**。

## AI 顾问 + 调度（日频决策闭环）

到 **开盘前(09:15)/收盘后(15:05)**，调度器自动汇总当日上下文（持仓、量化多因子信号、
大盘涨跌、财经快讯、个股走势），交给 **Claude（Opus 4.8，自适应思考 + 结构化输出）**
判断"下一步是否操作、如何操作"，产出：
- **量化调仓建议**（actions：目标权重，受单票/行业/换手约束）
- **波段想法**（swing：入场/止损/止盈，半主观短线）
- 大盘看法、风险姿态、风险提示、信心度

界面上「今日信号」一键生成量化选股，「AI 顾问决策」一键跑开盘/收盘例程，建议可一键建仓。

```bash
# 接 Claude（推荐）：
export ANTHROPIC_API_KEY=sk-...
python -m server.app           # 顾问用真 Claude；否则自动退回确定性 mock 兜底
```

> ⚠️ **安全**：顾问只**给建议**，由你人工确认后才下单，**绝不自动实盘**。无 API key
> 时走 mock，仅演示链路、不构成投资建议。实盘前务必先在模拟盘验证。

> **两层定位（研究）**：研究层能跑出一个诚实性测试，证明它"只奖励真信号、不会把噪声
> 美化成 alpha"。跑一下：`python -m research.run_research`。

---

## 两种模式

| 模式 | 用途 | 依赖 |
|------|------|------|
| **mock**（默认） | 打开就能看界面：内置随机游走行情 + 本地撮合，不需要 vnpy/券商 | 仅 fastapi/uvicorn/pydantic |
| **live** | 真·实时模拟盘：加载 vnpy + CtpGateway，连 **SimNow** | 额外装 vnpy、vnpy_ctp |

---

## 一、先跑 mock 看界面（任意系统，1 分钟）

```bash
cd vnpy-mini
pip install -r requirements.txt
python -m server.app                # 默认 mock 模式
# 浏览器打开 http://127.0.0.1:8000
```

试一下：
1. 在「行情」里输入 `rb2510`，交易所选 `SHFE`，点**订阅** → 最新价开始每秒跳动。
2. 在「下单」里填 `rb2510`、价格填高于现价（买单）、数量 `1`，**提交委托** →
   委托出现在列表，价格被行情触及后**自动成交**，持仓与浮动盈亏实时更新。

> mock 模式纯属界面/数据流演示，价格是随机数，**不代表任何真实行情**。

---

## 二、切到 live（真·实时模拟盘，建议 Windows + Python 3.10）

### 1. 安装 vnpy 与 CTP 网关
```bash
pip install vnpy vnpy_ctp
```
> `vnpy_ctp` 带编译好的 C++ 接口，Windows 上 `pip` 直接装最省事；Linux/Mac 需自行编译，较折腾。

### 2. 注册 SimNow（上期所官方仿真，免费）
- 到 SimNow 官网注册，拿到 **InvestorID / 密码 / BrokerID(默认 9999)** 与
  **交易/行情服务器地址**（有 7x24 与盘中两套环境）。

### 3. 启动 live 模式
```bash
# Windows PowerShell
$env:WEBMINI_MODE="live"; python -m server.app
# macOS / Linux
WEBMINI_MODE=live python -m server.app
```

### 4. 在 `server/app.py` 里填好连接参数 或 用「连接」按钮传入
CTP 连接字典的键（vnpy 规定的中文键）：
```python
setting = {
    "用户名":     "你的InvestorID",
    "密码":       "你的密码",
    "经纪商代码": "9999",
    "交易服务器": "tcp://180.168.146.187:10130",   # 以 SimNow 当前公布为准
    "行情服务器": "tcp://180.168.146.187:10131",
    "产品名称":   "simnow_client_test",
    "授权编码":   "0000000000000000",
    "柜台环境":   "测试",
}
```
> 服务器地址、产品名称、授权码以 SimNow 官网**当前公布**为准，会变。
> 把它接到「连接」按钮：前端 `POST /api/connect {setting: {...}}` 即可，或在
> `VnpyEngine.connect` 里写死默认值。

连上后：在「行情」订阅期货合约（如 `rb2510`/`SHFE`），下单走 SimNow 仿真撮合，
**全程实时、但不用真钱**。

---

## 接口约定（前端 ⇄ 后端）

REST：
- `GET  /api/status`     → `{mode, ok}`
- `GET  /api/snapshot`   → 账户/持仓/委托/成交/行情 首屏快照
- `POST /api/connect`    → `{setting:{...}}`
- `POST /api/subscribe`  → `{symbol, exchange}`
- `POST /api/order`      → `{symbol, exchange, direction(LONG/SHORT), offset(OPEN/CLOSE), type, price, volume}`
- `POST /api/cancel`     → `{orderid, symbol, exchange}`
- `GET  /api/strategies` → 运行中的策略列表
- `POST /api/strategy/start` → `{name, symbol, exchange, fast, slow, volume}`
- `POST /api/strategy/stop`  → `{id}`

WebSocket `/ws`：服务器单向推送 `{type, data}`，
`type ∈ {snapshot, tick, order, trade, position, account, strategy, log}`。

要加新功能（K线、策略启停、条件单等），在 `EngineBase` 加方法、
两套引擎各实现一份、前端加对应渲染即可。

---

## ⚠️ 风险提示
- 这是个人学习/研究用的轻量界面，**未做鉴权**，默认只绑 `127.0.0.1`，不要暴露到公网。
- live 模式即便是 SimNow 仿真也建议先吃透；真要接实盘，交易所 API key/券商凭证
  自行保管，**先 paper、再小资金**。

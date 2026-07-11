# QuantFlow 集成:AI 节点 + 单人安全加固 + 内置聊天换 Claude

在 **panda_quantflow 之上叠加**(尽量不改平台源码),外加一份代码级安全审查。

**文件清单**
| 文件 | 作用 | 放哪 |
|---|---|---|
| `ai_advisor_node.py` | AI 策略顾问节点(描述+回测→Claude→策略代码) | `src/panda_plugins/custom/` |
| `ai_strategy_loop_node.py` | **AI 策略自迭代节点**(生成→回测→读回测→再改 多轮闭环) | `src/panda_plugins/custom/`(需与上一个同放) |
| `run_local_secure.py` | **单人安全启动器**(只绑 127.0.0.1 + 拦跨站请求) | `src/` |
| `patched/llm-claude.patch` + `patched/llm/*` | **把内置聊天助手改用 Claude**(改平台两个 LLM 文件) | 见第四节 |

> 已全部对着 clone 下来的平台真实代码验证(见各节)。三件事:①单人安全 ②自迭代节点 ③内置聊天换 Claude。

---

## 〇、单人使用的安全加固(先做这个)

平台默认绑 `0.0.0.0`、无鉴权、回测节点直接 `exec`。**单机自己用**最现实的两个洞是:
局域网别人能扫到你端口、以及**你浏览别的网页时那网页偷偷向 `127.0.0.1:8000` 发请求跑工作流**
(=在你机器上执行代码)。`run_local_secure.py` 一次性堵住:

```bash
cp run_local_secure.py  panda_quantflow/src/
cd panda_quantflow/src
python run_local_secure.py      # 只绑 127.0.0.1 + LocalGuard 拦截跨站请求
# 打开 http://127.0.0.1:8000/quantflow/
```
它不改平台任何文件,只是换个入口:
- **只绑 `127.0.0.1`**(不再 0.0.0.0)——局域网扫不到;
- **LocalGuard 中间件**:拒绝 Origin 非本机的浏览器请求——挡住钓鱼站驱动的未授权操作/RCE;
- 顺带设 `RUN_MODE=LOCAL`、`MONGO_URI=127.0.0.1`,避免连厂商内网/用 prod 配置。

> 已验证:evil.com 的跨站请求被 403 拦截,本机同源 / 无 Origin 请求正常放行。
> (这解决"单人本机"。要邀请别人联网,见文末"邀请几个人"清单——需真鉴权 + 沙箱。)

**日常入口只记一个:`http://127.0.0.1:8000/dash`(统一工作台,只读)。**
总览(账户/权益曲线/挂单/报告)、自选行情(迷你走势)、资讯、决策·对账、报告列表
都在标签页里,QuantFlow 工作台和回测汇总表从右上角进 —— 不用再在地址栏换 URL。

**现役系统(2026-07 起)**:主引擎 `SIGNAL_ENGINE=alloc_p1` —— 股/债/金
40/40/20 月度再平衡(四轮无前视走查唯一过线策略,证据见
`A-SHARE-STRATEGY-RESEARCH.md`),AI 复核角色=审偏离。日常只有两条命令:
`LOAD_ALL=1 loader`(更新行情)→ `reviewer`(月初自动出再平衡信号,平时只
快照权益/复盘)。ETF 比赛线(momentum_v1)已退役留档。

> ⚠ 关于历史回测数字:见 `BACKTEST-AUDIT.md` —— 平台按当日开盘撮合而旧策略用当日
> 收盘算信号(前视偏差),涨跌停带也曾一律写成 ±10%。已修复(v2 无前视策略写法 +
> loader 按板块写带宽);以 `flows/a3-clean-vs-v1-flow.json` 重测的数字为准。

---

## 一、AI 策略顾问节点

### 它做什么
`输入`:你的**文字需求描述** + 一次回测的 **back_id**(从「股票回测」的 `backtest_id`
或「策略回测结果」节点连过来) + 你的**自选股清单**。
`处理`:读取该回测绩效(夏普/年化/回撤/原策略代码)→ 连同描述交给 **Claude(默认 Opus 4.8)**
→ 用平台自带的 `PromptsProvider`(回测引擎文档)约束、`BacktestCodeChecker` 校验。
`输出`:**strategy_code**(四函数结构,可直接连回「股票回测」的 `code`,形成 生成→回测→再优化 闭环)
+ analysis + explanation。

它**复用了 quantflow 本身的模块**:`PromptsProvider`、`BacktestCodeChecker`、`order_shares` API,
所以生成的策略符合它的引擎接口——正是你要的"用自带模块 + 加自定义模块"。

### 安装(本地,3 步)
```bash
# 1) 放入自定义节点目录
cp ai_advisor_node.py  panda_quantflow/src/panda_plugins/custom/ai_advisor_node.py
# 2) 装 Claude SDK 并配 key(不配则自动退回 deepseek 或 mock 骨架,节点照样能跑)
pip install anthropic
export ANTHROPIC_API_KEY=sk-...
# 3) 重启服务;UI 的「05-回测相关」分组里会出现「AI策略顾问」节点
```

### 在工作流里怎么接
```
[股票回测] --backtest_id--> [AI策略顾问] --strategy_code--> [股票回测] --> [策略回测结果]
                ^  description(文字) / watchlist(自选20只) 手填
```
1. 先跑一次「股票回测」拿到 `backtest_id`;
2. 把它连到「AI策略顾问」的 `back_id`,填上你的需求描述和自选;
3. 顾问输出的 `strategy_code` 再连回一个「股票回测」的 `code` 输入 → 一键得到新回测。
反复几轮就是"描述→AI 出策略→回测→AI 看回测再改"的闭环。

### 已验证(对着平台真实代码测过)
- 节点结构与 `@work_node`/`@ui`/`BaseWorkNode` 契约一致,能被平台加载器注册、UI 能读到字段;
- mock 兜底生成的双均线策略**编译通过,且干净通过平台自带的 `BacktestCodeChecker`**;
- 一个"检查器坑"已在节点里规避:平台的 `BacktestCodeChecker` **禁止 `context.run_info`**
  (尽管仓库自带示例策略在用它!),下单**必须带 `style=MarketOrderStyle`**、账户用字符串 `'8888'`。
  节点生成的代码和给 Claude 的提示都已按检查器要求写。

### 可选:把 Claude 也接进平台的聊天助手(不只是节点)
平台的 LLM 走 `src/panda_server/services/llm/enums/llm_model_type.py` 的 `MODEL_CONFIG`(用 OpenAI SDK 指向 DeepSeek)。
若也想让内置「回测助手」用 Claude:在 `LLMService.__init__` 里对 `base_url` 含 `anthropic` 的分支改用 Anthropic SDK,
并在 `MODEL_CONFIG` 加一条 `"Claude": {"model":"claude-opus-4-8", ...}`。本节点默认**直接调 Anthropic**,不依赖这步。

---

## 一之二、AI 策略自迭代节点(生成→回测→读回测→再改)

`ai_strategy_loop_node.py` —— 把整个闭环塞进**一个节点**,自动多轮优化:

- **第 1 轮**:按你的描述让 Claude 生成策略 → 平台检查器校验 → 用平台回测引擎跑 → 读绩效;
- **第 2..N 轮**:把上一轮的**代码 + 绩效**回喂 Claude 让它改进 → 再跑 → 再读;
- **结束**:到 `max_rounds`,或达到目标指标(如 `sharpe ≥ 1.5`)提前停;
- **输出**:`best_code`(最优那版,可连回「股票回测」复跑)+ `best_metrics` + 每轮 `rounds` 历史。

参数:`描述 / 自选 / max_rounds / target_metric(sharpe|back_profit_year|max_drawdown) / target_value /
回测区间与资金`。复用平台的 `main_workflow_stock.start`(同「股票回测」)、`BacktestCodeChecker`、
以及顾问节点的 LLM 函数(所以**两个节点文件要一起放**)。

> 已验证(依赖注入 + 假回测,离线可测):达标提前停、代码检查失败→回喂修复→次轮通过、
> 跑满轮数选最优、回测抛异常被捕获记录——四种路径都对。真实回测需你本机连 Mongo+数据才能跑。

---

## 三、把内置聊天助手改用 Claude

平台的内置助手(回测/因子/代码助手)走 `services/llm/`,默认用 DeepSeek。补丁让它**改用 Claude**:

**应用方式**(改平台两个文件,二选一):
```bash
# 方式 A:打补丁
cd panda_quantflow && git apply /path/to/patched/llm-claude.patch
# 方式 B:直接替换这两个文件
cp patched/llm/llm_model_type.py  src/panda_server/services/llm/enums/llm_model_type.py
cp patched/llm/llm_service.py     src/panda_server/services/llm/base/llm_service.py
# 然后配 key 并重启
export ANTHROPIC_API_KEY=sk-...
```
改了什么:
- `MODEL_CONFIG` 加一条 `Claude`(provider=anthropic, model=claude-opus-4-8);
- `LLMService` 按 provider 分支:Claude 走 Anthropic SDK(自适应思考),**流式输出包了一层
  兼容 shim**,让原有消费方 `chunk.choices[0].delta.content / .reasoning_content` 无需改动;
- **默认路由**:一旦设了 `ANTHROPIC_API_KEY`,内置聊天自动走 Claude;没设则**原样回退 DeepSeek**
  (想强制用 DeepSeek 可设 `LLM_PROVIDER=deepseek`)。前端不用改。

> 已验证(假 Anthropic 客户端):路由到 Claude、system/developer 反馈折叠为 user 且首条为 user、
> 非流式返回文本、流式 chunk 的 content/reasoning 分离正确;无 key 时 DeepSeek 分支与原版逐字节一致(无回归)。
> ⚠ 隐私:走 Claude = 你的策略代码/提示发到 Anthropic(出境);走 DeepSeek = 发到深度求索。按合规选。

---

## 二、平台安全审查结论(clone 后代码级审查)

> 一句话:**本地单人用没问题;要"邀请几个人联网用",当前状态绝对不能直接上——先做下面的加固。**

QuantFlow 本质是"**用户提交 Python、服务器执行**"的平台,又几乎没有鉴权,所以多人联网时风险叠加。

### 关键发现(均带 file:line,已核实)
| 级别 | 位置 | 问题 |
|---|---|---|
| 🔴 严重 | `panda_server/main.py` + 所有 `uid` 头 | **完全没有鉴权**:身份=可伪造的 `uid` 请求头;跑工作流的唯一"门"是硬编码常量 `quantflow-auth == "2"` |
| 🔴 严重 | `panda_server/routes/trading_routes.py`(全部) | `/api/trading/*` **连 uid 都不要**:任何人可创建/删除期货实盘账户、发起 start/restart 实盘交易 |
| 🔴 严重 | `panda_backtest/.../strategy_utils.py:61` ← 回测节点 `code` 字段 | 回测策略代码**直接 `compile`+`exec` 进服务器进程,无检查、无沙箱** → 未鉴权 RCE |
| 🔴 严重 | `panda_server/utils/db_storage.py:72` | 从 Mongo `cloudpickle.loads()`;`panda_backtest`/`panda_trading` 多处从 Redis `pickle.loads()` → **能写库/写 Redis 就能 RCE** |
| 🔴 严重 | `panda_trading/trading_route/config_prod.ini` | **仓库里提交了真实生产密钥**(MySQL root、Redis 密码、内网 IP);`config.ini` 默认 `config_file=prod` 会去读 |
| 🟠 中 | `common/config/config.py` | 弱默认密码 `panda`/`123456`/`qweqwe`,**不改也静默启动** |
| 🟠 中 | `panda_server/main.py` | 绑 `0.0.0.0`、CORS `allow_origins=["*"] + allow_credentials=True`(规范禁止的组合)、`reload=True` |
| 🟠 中 | `panda_web/assets/*.js`(前端) | 预打包前端**默认回连 `http://api.pandaai.online`(明文 HTTP,带你的 token)**取行情/图表 |
| 🟠 中 | `panda_trading/models/.../trading_future_account.py` | 券商 CTP 账号密码**明文存 Mongo** |
| 🟡 低 | `services/llm/` | 内置 LLM 默认把你的**代码/提示发去 DeepSeek**(第三方,国内);其 AST "安全检查器"只是给 LLM 的建议、且可被 `getattr`/`ctypes`/子类遍历绕过 |
| 🟡 低 | `Dockerfile:1` | 基础镜像 `FROM panda_factor:latest` 未锁版本;`pip install -e .` 不用 lock |

### 加固清单

**A. 本地自己用(单机)** — 基本够了,做两件就行:
1. 别下厂商网盘 DB;`config.ini` 改成本地配置,别用 `prod`(免得连它内网/弱默认)。
2. 服务只绑 `127.0.0.1`(改 `main.py` 的 `host`),别对公网开端口。

**B. 邀请几个人联网用** — 必须全部做完再邀请:
1. **前面加一层认证网关**(Nginx + 一个登录页/Basic Auth,或 oauth2-proxy)——平台自己没有账号系统,这是唯一现实的多用户鉴权办法。
2. **应用只绑 `127.0.0.1`**,只让 Nginx 反代;**80/443 外其它端口一律不对公网**。
3. **Mongo / Redis 绝不暴露公网**,绑内网 + 开认证 + **改掉所有弱默认密码**(`panda`/`123456`/`qweqwe`)。
4. **把 `config_prod.ini` 里的厂商真密钥清掉**,换成你自己的、走环境变量。
5. **收紧 CORS**:把 `allow_origins` 改成你的域名,去掉 `["*"]+credentials` 组合;生产关 `reload`。
6. **回测/自定义节点=在你服务器上跑任意代码**:受邀者能提交代码就等于能拿你的机器。要么只邀请你**完全信任**的人,要么把回测执行放进**隔离沙箱/容器**(单独低权限进程,无宿主/网络访问)。这是最难、也最关键的一条。
7. **实盘接口 `/api/trading/*` 直接在反代层禁掉**(除非你确有实盘需求并已单独鉴权)。
8. 前端回连 `api.pandaai.online`(明文+token)按需拦掉/换自建行情;LLM 若不想把代码发去 DeepSeek,用本节点走 Claude(数据同样出境,注意合规)。

> 现实建议:**"几个有邀请码的人看看" ≈ 你在服务器上把 shell 权限发给了他们。** 所以第 1、2、6、7 条不做完,别开放。若这几个人是你完全信任的朋友、且不接实盘,风险可控;若是半陌生人,务必上沙箱。

### 许可证
平台 **AGPL-3.0**:自己本地用随便;**一旦联网给别人用就触发**——你得向这些用户提供你修改后的完整源码(含本节点)。小圈子风险低但要知道。

---

*本目录的节点已对着 clone 下来的平台真实代码验证:注册契约、UI schema、以及生成策略通过平台自带 BacktestCodeChecker。安全结论来自对 108M/556 个 py 文件的多维代码审查。*

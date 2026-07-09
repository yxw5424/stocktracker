# QuantFlow + AI 集成 —— Docker 一键本地部署

不用手动装 MongoDB 副本集、不用碰命令行装重依赖。装好 **Docker Desktop** 后一条命令起。

## 两个版本,按需选
| compose 文件 | 用途 | 数据 |
|---|---|---|
| `docker-compose.yml` | **只看效果**:空库,起 UI + AI 节点(mock 兜底出策略) | 无 |
| `docker-compose.data.yml` | **能真跑回测**:接 PandaAI 网盘预制库 + 起 factor 服务 | 你提供预制 `data/db` |

> 你选了 **A(网盘预制库)** → 用 **`docker-compose.data.yml`**,见文末「## 带数据版(A)」。

## 跑起来（Windows / Mac / Linux 通用）

```bash
# 1) 拉我的仓库（含 Docker 配置 + AI 节点 + 补丁）
git clone -b claude/quantdesigner-security-review-vaeby1 https://github.com/yxw5424/stocktracker.git
cd stocktracker/quantflow-integration/docker

# 2) 配 Claude key（可选；不填也能跑）
#    Windows:  copy .env.example .env    然后编辑 .env 填 ANTHROPIC_API_KEY
#    Mac/Lin:  cp   .env.example .env
cp .env.example .env

# 3) 一条命令起（首次会构建镜像，较慢）
docker compose up --build
```

浏览器打开:**http://127.0.0.1:8000/quantflow/**

停止:`Ctrl+C`,或 `docker compose down`(加 `-v` 连数据库一起清)。

## 它起了什么

| 服务 | 说明 |
|---|---|
| `mongo` | 单节点 MongoDB **副本集 rs0**(自动初始化,无需你手动 keyfile/auth) |
| `quantflow` | QuantFlow 本体 + 我的 `AI策略顾问` / `AI策略自迭代` 节点 + 内置聊天已换 Claude,以 **LOCAL 模式**(只用 Mongo)运行,**只绑 127.0.0.1** |

pin 了 panda_quantflow 到我审计/打补丁的那个提交,补丁保证能应用。

## 先看什么效果(零数据也能看)

1. 打开 `/quantflow/`,左侧节点面板里找 **「05-回测相关」→ AI策略顾问 / AI策略自迭代**;
2. 拖出来,填「描述 + 自选股」,运行 → 看它**输出策略代码**(没配 key 时走 mock 兜底,照样出一版能过平台校验的双均线策略);
3. 配了 `ANTHROPIC_API_KEY` 后,顾问和内置聊天都走 **Claude**。

## 灌你自己那 20 只(回测能真跑)—— 免费,无需 token

空库只能看 UI。要让 AI 出的策略**真跑回测**,得先把你自选股的行情灌进去。用 `akshare`(免费、不用 token),只灌你关心的那几只:

```bash
# 1) 编辑自选股清单(一行一个代码,600519 或 600519.SH 都行)
#    Windows:  notepad watchlist.txt
#    Mac/Lin:  nano watchlist.txt
#    文件里已有 5 只示例,改成你自己的 20 只。

# 2) 确保平台已经起着(docker compose up --build 那个终端别关),另开一个终端一次性灌数据:
docker compose run --rm loader
```

灌完 log 会打印每只写入多少条。它写了平台回测真正读的全部表:日线 `stock_market`、交易日历 `trade_calendar`/`trading_calendar_all`、股票名 `stock_info_new`。

可调(在命令前设环境变量,或写进 `.env`):
- `START_DATE=20230101`(默认 20240101)、`END_DATE`(默认今天)
- `LOAD_MINUTE=1` 额外拉 **分时线**(1 分钟,给看盘用;较慢量大,默认不拉)
- 例:`START_DATE=20230101 LOAD_MINUTE=1 docker compose run --rm loader`

> 想更新到最新行情,随时再跑一次 `docker compose run --rm loader`(按 symbol+date 幂等 upsert,不会重复)。

**⚠️ 灌完数据必须重启平台**:`docker compose restart quantflow`。平台会把"查过但不存在"的
股票/指数信息缓存在进程里,不重启的话,即使数据已经灌进库,正在运行的平台仍按"不存在"处理
(症状:基准指数报 `'NoneType' object has no attribute 'last'`)。

## ⚠️ 老实说三点

1. **首次 `--build` 大且慢**:会拉 `torch + tensorflow` 等,镜像好几个 G、可能十几二十分钟。这是平台依赖决定的,不是我加的。
2. **真回测需要行情数据**:这套零数据能看 UI 和 AI 节点,但**真跑回测要市场数据**——之后把 PandaAI 网盘预制库挂进 `mongo_data` 卷,或用 panda_factor + 你的 Tushare token 灌你那 20 只即可(要接我再给你加一段)。
3. **我这边没法跑 Docker 验证**:配置是按平台真实的环境变量/依赖/启动逻辑写的(已确认 LOCAL 模式只依赖 Mongo、节点加载失败会被跳过不影响启动)。但首次构建**万一**卡在某个系统库/依赖上,**把 `docker compose up --build` 的报错贴给我,我马上改**——第一次起 Docker 遇到点小坑是常事。

## 带数据版(A):接网盘预制库,回测能真跑

**1. 拿到并解压预制库**:找 PandaAI 小助理要网盘链接,下载(分包的话下全)、解压,找到里面的 **`data/db`** 目录(MongoDB 数据目录,里面一堆 `.wt` 文件)。

**2. 在 `.env` 里指向它**(在 `docker/` 目录下 `copy .env.example .env` 后编辑):
```
ANTHROPIC_API_KEY=sk-...            # 可留空
MONGO_DB_PATH=C:\Users\你\pandadb\data\db   # ← 指向解压出的 data/db（Windows 反斜杠）
```

**3. 起(用带数据的 compose)**:
```bash
docker compose -f docker-compose.data.yml up --build
```
- 工作流 UI:**http://127.0.0.1:8000/quantflow/**
- 因子/超级图表服务:**http://127.0.0.1:8111/**

这版怎么接的:Mongo 挂你的预制 `data/db` + 开 `--auth`(用预制库自带的 `panda/panda` 账号)+ 全部 **single 直连**(绕开预制库副本集里指向 `127.0.0.1` 的坑,也免了 keyfile);镜像里已把 panda_common 的 `config.yaml` 改指向 `mongo:27017`。

**这版可能踩的坑(我没法跑 Docker 验证,先跟你说清)**:
- **Mongo 版本**:预制库若是更老版本做的,`mongo:7` 启动可能报 FCV 错——把 `docker-compose.data.yml` 里 `image: mongo:7` 改成 `mongo:6` 再试。
- **`data/db` 路径**:必须精确指到那个 `db` 目录本身(不是它的上级)。Windows 路径用反斜杠。
- **权限**:Docker Desktop(Win/Mac)一般没问题;若报权限错把日志发我。
- 任何一步报错,**把 `docker compose -f docker-compose.data.yml up --build` 的报错整段贴给我,我照着改**。

## 安全

容器端口只映射到 `127.0.0.1`(外部访问不到)+ 入口用 `run_local_secure.py`(LocalGuard 拦跨站请求)。这满足**单人本机**。要邀请别人联网,另见上级目录 README 的"邀请几个人"清单(需真鉴权 + 沙箱)。

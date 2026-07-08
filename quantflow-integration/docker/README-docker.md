# QuantFlow + AI 集成 —— Docker 一键本地部署

不用手动装 MongoDB 副本集、不用碰命令行装重依赖。装好 **Docker Desktop** 后一条命令起。

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

## ⚠️ 老实说三点

1. **首次 `--build` 大且慢**:会拉 `torch + tensorflow` 等,镜像好几个 G、可能十几二十分钟。这是平台依赖决定的,不是我加的。
2. **真回测需要行情数据**:这套零数据能看 UI 和 AI 节点,但**真跑回测要市场数据**——之后把 PandaAI 网盘预制库挂进 `mongo_data` 卷,或用 panda_factor + 你的 Tushare token 灌你那 20 只即可(要接我再给你加一段)。
3. **我这边没法跑 Docker 验证**:配置是按平台真实的环境变量/依赖/启动逻辑写的(已确认 LOCAL 模式只依赖 Mongo、节点加载失败会被跳过不影响启动)。但首次构建**万一**卡在某个系统库/依赖上,**把 `docker compose up --build` 的报错贴给我,我马上改**——第一次起 Docker 遇到点小坑是常事。

## 安全

容器端口只映射到 `127.0.0.1`(外部访问不到)+ 入口用 `run_local_secure.py`(LocalGuard 拦跨站请求)。这满足**单人本机**。要邀请别人联网,另见上级目录 README 的"邀请几个人"清单(需真鉴权 + 沙箱)。

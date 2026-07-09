# panda_quantflow + AI 集成 —— 本地一键镜像（LOCAL 模式，只依赖 Mongo）
# build context = quantflow-integration/（见 docker-compose.yml）
FROM python:3.12-slim

ENV TZ=Asia/Shanghai PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential libsnappy-dev ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) panda_factor（数据/因子层）——装上以解决 panda_data 等 import；无数据也能装
RUN git clone --depth 1 https://github.com/PandaAI-Tech/panda_factor.git
RUN pip install -e ./panda_factor/panda_common ./panda_factor/panda_factor \
        ./panda_factor/panda_data ./panda_factor/panda_data_hub \
        ./panda_factor/panda_llm ./panda_factor/panda_factor_server
# panda_common 只认自己的 config.yaml：把 Mongo 指向容器网络里的 mongo，并用直连(single)
RUN sed -i 's#127\.0\.0\.1:27017#mongo:27017#; s#MONGO_TYPE: "replica_set"#MONGO_TYPE: "single"#' \
        /app/panda_factor/panda_common/panda_common/config.yaml

# 2) panda_quantflow —— pin 到已审计/已适配补丁的提交，保证补丁一定能应用
RUN git clone https://github.com/PandaAI-Tech/panda_quantflow.git && \
    cd panda_quantflow && git checkout 688b90e74a738b84567efe622a2d9c1e5ce10e00
RUN pip install -e ./panda_quantflow
RUN pip install anthropic

# 3) 放入 AI 节点 + 安全启动器 + 内置聊天换 Claude 补丁（context = quantflow-integration/）
COPY ai_advisor_node.py ai_strategy_loop_node.py /app/panda_quantflow/src/panda_plugins/custom/
COPY run_local_secure.py /app/panda_quantflow/src/
COPY patched/llm-claude.patch /tmp/llm-claude.patch
RUN cd panda_quantflow && git apply /tmp/llm-claude.patch
# Mongo 连接器：允许空用户名（本地无鉴权 => 不拼 user:pass@，否则 pymongo 报
# "The empty string is not valid username"）。整文件替换，避开 f-string 转义坑。
COPY docker/patched_quantflow/mongodb_handler.py /app/panda_quantflow/src/common/connector/mongodb_handler.py
# 股票回测节点：平台原代码 except 里用 e.message(不存在)把真实错误吞成
# AttributeError；换成打印真实异常+traceback 尾部，方便排错。
COPY docker/patched_quantflow/backtest_stock_node.py /app/panda_quantflow/src/panda_plugins/internal/backtest_stock_node.py

WORKDIR /app/panda_quantflow/src
# 容器内绑 0.0.0.0（靠 compose 的 127.0.0.1 端口映射做网络隔离）；LocalGuard 仍拦跨站请求
ENV WEBMINI_HOST=0.0.0.0 WEBMINI_PORT=8000
EXPOSE 8000
CMD ["python", "run_local_secure.py"]

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

WORKDIR /app/panda_quantflow/src
# 容器内绑 0.0.0.0（靠 compose 的 127.0.0.1 端口映射做网络隔离）；LocalGuard 仍拦跨站请求
ENV WEBMINI_HOST=0.0.0.0 WEBMINI_PORT=8000
EXPOSE 8000
CMD ["python", "run_local_secure.py"]

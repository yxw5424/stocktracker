# AI 信号复核官:规则出信号 → agent 收资料复核 → 纸面执行 → 事后总结。
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TZ=Asia/Shanghai
RUN pip install "pymongo>=4" pandas akshare openai anthropic
WORKDIR /app
COPY review/daily_review.py /app/daily_review.py
CMD ["python", "/app/daily_review.py"]

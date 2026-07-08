# 轻量数据导入器:akshare 拉自选股行情 -> Mongo。与主镜像分开,免得把 akshare 塞进大镜像。
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TZ=Asia/Shanghai
RUN pip install "pymongo>=4" akshare pandas
WORKDIR /app
COPY loader/load_watchlist.py /app/load_watchlist.py
CMD ["python", "/app/load_watchlist.py"]

# -*- coding: utf-8 -*-
"""
QuantFlow 本地安全启动器 —— 单人本机使用的安全加固（不改平台源码）。

把本文件放到  panda_quantflow/src/  下，然后：
    cd panda_quantflow/src
    python run_local_secure.py

它做两件关键的事，堵住单机使用最现实的两个洞：
  1) 只绑 127.0.0.1（不再 0.0.0.0）——局域网里别人扫不到你的端口。
  2) LocalGuard 中间件：拒绝「来自其它网站的浏览器请求」（Origin 非本机）。
     —— 这挡住的是最阴险的一种攻击：你浏览别的网页时，那网页偷偷向
        127.0.0.1:8000 发请求跑工作流(=在你机器上执行代码)。平台没有鉴权、
        回测节点又直接 exec，所以必须在入口就把跨站请求拦掉。

放行：无 Origin 的请求（本地 UI 同源、curl、服务端脚本）+ Origin 指向本机的请求。
不改平台任何文件；只是换一个入口启动 app。

注意：这解决的是「单人本机」。要邀请别人联网用，另见 README（需要真鉴权+沙箱）。
"""

import os
from urllib.parse import urlparse

# 安全默认（真实 env 已设则不覆盖）：本地模式 + 别连厂商内网
os.environ.setdefault("RUN_MODE", "LOCAL")
os.environ.setdefault("MONGO_URI", "127.0.0.1:27017")

HOST = os.getenv("WEBMINI_HOST", "127.0.0.1")
PORT = int(os.getenv("WEBMINI_PORT", "8000"))
ALLOWED = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"127.0.0.1", "localhost"}


# 前端部分请求(如 AI 助手聊天)带官方网关前缀 /pandaApi/quantflow，本地没有网关
# 会 404 —— 在入口把这个前缀剥掉，等价于官方 nginx 的 rewrite。
_GATEWAY_PREFIX = "/pandaApi/quantflow"


class LocalGuard:
    """纯 ASGI 中间件：拦截 Origin 非本机的 HTTP 请求（防钓鱼站驱动的未授权操作/RCE）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = dict(scope.get("headers") or [])
            origin = headers.get(b"origin", b"").decode()
            if origin:
                host = urlparse(origin).netloc or origin
                if host not in ALLOWED:
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                    await send({"type": "http.response.body",
                                "body": "LocalGuard: 已拦截跨站请求（仅允许本机访问）".encode()})
                    return
            path = scope.get("path", "")
            if path.startswith(_GATEWAY_PREFIX + "/"):
                scope = dict(scope)
                scope["path"] = path[len(_GATEWAY_PREFIX):]
                raw = scope.get("raw_path")
                if isinstance(raw, (bytes, bytearray)):
                    scope["raw_path"] = raw[len(_GATEWAY_PREFIX):]
        await self.app(scope, receive, send)


def build_app():
    # 导入平台 app（不会触发其 __main__ 里的 0.0.0.0 启动）
    from panda_server.main import app
    # 把回测可视化报告目录挂成网页:http://127.0.0.1:8000/reports/latest.html
    try:
        from starlette.staticfiles import StaticFiles
        rep_dir = os.getenv("REPORT_DIR", "/reports")
        os.makedirs(rep_dir, exist_ok=True)
        app.mount("/reports", StaticFiles(directory=rep_dir, html=True), name="reports")
    except Exception as exc:
        print(f"[warn] /reports 静态目录挂载失败(不影响平台): {exc}")

    # 只读看板:/dash 页面 + /dash/data JSON(不放任何会改状态的接口)
    try:
        import sys
        sys.path.insert(0, "/app/panda_quantflow/src/panda_plugins/custom")
        sys.path.insert(0, os.path.dirname(__file__))
        import dashboard as _dash
        from starlette.responses import HTMLResponse, JSONResponse

        async def _dash_page(request):
            return HTMLResponse(_dash.DASH_HTML)

        async def _dash_data(request):
            from fastapi.concurrency import run_in_threadpool
            return JSONResponse(await run_in_threadpool(_dash.collect))

        async def _dash_quotes(request):
            from fastapi.concurrency import run_in_threadpool
            return JSONResponse(await run_in_threadpool(_dash.quotes))

        async def _dash_news(request):
            from fastapi.concurrency import run_in_threadpool
            sym = request.query_params.get("symbol", "all")
            return JSONResponse(await run_in_threadpool(_dash.news, sym))

        async def _dash_screen(request):
            from fastapi.concurrency import run_in_threadpool
            return JSONResponse(await run_in_threadpool(_dash.screen))

        async def _dash_detail(request):
            from fastapi.concurrency import run_in_threadpool
            sym = request.query_params.get("symbol", "")
            return JSONResponse(await run_in_threadpool(_dash.detail, sym))

        async def _watch_list(request):
            from fastapi.concurrency import run_in_threadpool
            return JSONResponse(await run_in_threadpool(_dash.watchlist))

        async def _watch_add(request):
            from fastapi.concurrency import run_in_threadpool
            code = request.query_params.get("code", "")
            return JSONResponse(await run_in_threadpool(_dash.watchlist_add, code))

        async def _watch_remove(request):
            from fastapi.concurrency import run_in_threadpool
            sym = request.query_params.get("symbol", "")
            return JSONResponse(await run_in_threadpool(_dash.watchlist_remove, sym))

        async def _refresh(request):
            return JSONResponse(_dash.refresh_start())

        async def _refresh_status(request):
            return JSONResponse(_dash.refresh_status())

        app.add_route("/dash", _dash_page, methods=["GET"])
        app.add_route("/dash/data", _dash_data, methods=["GET"])
        app.add_route("/dash/quotes", _dash_quotes, methods=["GET"])
        app.add_route("/dash/news", _dash_news, methods=["GET"])
        app.add_route("/dash/screen", _dash_screen, methods=["GET"])
        app.add_route("/dash/detail", _dash_detail, methods=["GET"])
        # 写操作:自选管理/增量更新/定时任务管理;交易类操作没有任何接口。
        # LocalGuard 已拦截跨站请求,这些 POST 只可能来自本机浏览器。
        app.add_route("/dash/watchlist", _watch_list, methods=["GET"])
        app.add_route("/dash/watchlist/add", _watch_add, methods=["POST"])
        app.add_route("/dash/watchlist/remove", _watch_remove, methods=["POST"])
        app.add_route("/dash/refresh", _refresh, methods=["POST"])
        app.add_route("/dash/refresh/status", _refresh_status, methods=["GET"])

        # Routine 调度器:定时任务(行情更新/复核官/轻量备份),系统页管理
        try:
            import scheduler as _sched
            _sched.start()

            async def _jobs(request):
                from fastapi.concurrency import run_in_threadpool
                return JSONResponse(await run_in_threadpool(_sched.jobs_list))

            async def _jobs_update(request):
                from fastapi.concurrency import run_in_threadpool
                q = request.query_params
                return JSONResponse(await run_in_threadpool(
                    _sched.job_update, q.get("id", ""), q.get("enabled"),
                    q.get("time"), q.get("trading_days_only")))

            async def _jobs_run(request):
                from fastapi.concurrency import run_in_threadpool
                return JSONResponse(await run_in_threadpool(
                    _sched.job_run_now, request.query_params.get("id", "")))

            async def _jobs_log(request):
                from fastapi.concurrency import run_in_threadpool
                return JSONResponse(await run_in_threadpool(
                    _sched.job_log, request.query_params.get("id", "")))

            async def _jobs_create(request):
                from fastapi.concurrency import run_in_threadpool
                q = request.query_params
                return JSONResponse(await run_in_threadpool(
                    _sched.job_create, q.get("name", ""), q.get("time", "09:35"),
                    q.get("prompt", ""), q.get("symbol", ""), q.get("trading_days_only", "1")))

            async def _jobs_delete(request):
                from fastapi.concurrency import run_in_threadpool
                return JSONResponse(await run_in_threadpool(
                    _sched.job_delete, request.query_params.get("id", "")))

            app.add_route("/dash/jobs", _jobs, methods=["GET"])
            app.add_route("/dash/jobs/update", _jobs_update, methods=["POST"])
            app.add_route("/dash/jobs/run", _jobs_run, methods=["POST"])
            app.add_route("/dash/jobs/log", _jobs_log, methods=["GET"])
            app.add_route("/dash/jobs/create", _jobs_create, methods=["POST"])
            app.add_route("/dash/jobs/delete", _jobs_delete, methods=["POST"])
        except Exception as exc:
            print(f"[warn] Routine 调度器未启动(不影响平台): {exc}")
        print("  统一工作台 -> http://127.0.0.1:8000/dash")
    except Exception as exc:
        print(f"[warn] /dash 看板挂载失败(不影响平台): {exc}")
    return LocalGuard(app)


if __name__ == "__main__":
    import uvicorn
    print(f"\n  QuantFlow [本地安全模式] -> http://{HOST}:{PORT}/quantflow/   （仅本机可访问）\n")
    uvicorn.run(build_app(), host=HOST, port=PORT, log_config=None)

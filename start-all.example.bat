@echo off
chcp 65001 >nul
REM ====== stocktracker 一键启动模板 ======
REM 用法:把本文件复制为 start-all.bat,改下面两行,然后双击 start-all.bat。
REM (start-all.bat 已在 .gitignore 里,含密码也不会上传。)

cd /d "E:\dev\stcoktracker"

set STK_PASSWORD=改成你的访问密码
REM 生成一个随机 STK_SECRET:  python -c "import secrets;print(secrets.token_hex(24))"
set STK_SECRET=粘贴一串随机十六进制

start "stk-数据循环"  cmd /k python scripts\run_local.py
start "stk-控制台8777" cmd /k python -m server.app
start "stk-隧道"       cmd /k cloudflared tunnel --url http://127.0.0.1:8777

echo 本地控制台 http://127.0.0.1:8777 ;分享链接看"stk-隧道"窗口的 trycloudflare 地址。
pause

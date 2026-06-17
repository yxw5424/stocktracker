@echo off
REM stocktracker one-click launcher TEMPLATE.
REM Usage: copy this to start-all.bat, edit the 2 lines below, then double-click start-all.bat.
REM (start-all.bat is gitignored, so your password is never uploaded.)
REM NOTE: keep this .bat ASCII-only; cmd mis-parses non-ASCII .bat files.
cd /d "E:\dev\stcoktracker"

REM free port 8777 if a previous console is still running (avoid bind error)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "127.0.0.1:8777" ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

set STK_PASSWORD=your-access-password
REM generate a random secret:  python -c "import secrets;print(secrets.token_hex(24))"
set STK_SECRET=paste-random-hex-here

start "stk-data"    cmd /k "chcp 65001>nul & python scripts\run_local.py"
start "stk-console" cmd /k "chcp 65001>nul & python -m server.app"
REM tunnel: cpolar (CN intranet-penetration, no VPN). First time only: cpolar authtoken YOUR_TOKEN
start "stk-tunnel"  cmd /k "chcp 65001>nul & cpolar http 8777"

echo Local console: http://127.0.0.1:8777   Share link: stk-tunnel window or http://127.0.0.1:9200
pause

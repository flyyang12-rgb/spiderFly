@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [SpiderFly] 正在创建服务所需的独立 Python 环境...
  python -m venv .venv
  if errorlevel 1 goto :failed
)

echo [SpiderFly] 正在检查后端依赖...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r backend\requirements.txt
if errorlevel 1 goto :failed

pushd frontend
if not exist "node_modules" (
  echo [SpiderFly] 正在安装前端依赖...
  call npm install
  if errorlevel 1 goto :frontend_failed
)
echo [SpiderFly] 正在构建管理页面...
call npm run build
if errorlevel 1 goto :frontend_failed
popd

set "ENV_HOST="
set "ENV_PORT="
if exist ".env" (
  for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if /i "%%A"=="SPIDERFLY_HOST" set "ENV_HOST=%%B"
    if /i "%%A"=="SPIDERFLY_PORT" set "ENV_PORT=%%B"
  )
)
if not defined SPIDERFLY_HOST (
  if defined ENV_HOST (
    set "SPIDERFLY_HOST=%ENV_HOST%"
  ) else (
    set "SPIDERFLY_HOST=0.0.0.0"
  )
)
if not defined SPIDERFLY_PORT (
  if defined ENV_PORT (
    set "SPIDERFLY_PORT=%ENV_PORT%"
  ) else (
    set "SPIDERFLY_PORT=8000"
  )
)
set "BROWSER_HOST=127.0.0.1"
if /i not "%SPIDERFLY_HOST%"=="0.0.0.0" set "BROWSER_HOST=%SPIDERFLY_HOST%"

echo.
echo [SpiderFly] 服务将以单进程、单 worker 运行。
echo [SpiderFly] 误开第二次时会自动拦截。
echo [SpiderFly] 本机访问：http://%BROWSER_HOST%:%SPIDERFLY_PORT%
echo [SpiderFly] 局域网访问：http://%COMPUTERNAME%:%SPIDERFLY_PORT%
echo [SpiderFly] 名称不可用时：http://^<本机IPv4地址^>:%SPIDERFLY_PORT%
echo [SpiderFly] 当前监听：%SPIDERFLY_HOST%:%SPIDERFLY_PORT%
echo [SpiderFly] 默认首次登录信息：data\首次登录信息.txt
echo [SpiderFly] 首次给伙伴使用前，请运行一次“开启局域网访问.bat”。
echo [SpiderFly] 请仅在可信局域网或 Tailscale 私网使用，不要直接暴露到公网。
echo.

start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://%BROWSER_HOST%:%SPIDERFLY_PORT%'"
".venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host "%SPIDERFLY_HOST%" --port "%SPIDERFLY_PORT%" --workers 1
if errorlevel 1 goto :failed
goto :eof

:frontend_failed
popd

:failed
echo.
echo [SpiderFly] 启动失败，请查看上方错误。
pause
exit /b 1

@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [SpiderFly] 正在创建独立 Python 环境...
  python -m venv .venv
  if errorlevel 1 goto :failed
)

".venv\Scripts\python.exe" -c "import fastapi, uvicorn, requests, PIL" >nul 2>&1
if errorlevel 1 (
  echo [SpiderFly] 正在安装后端依赖...
  ".venv\Scripts\python.exe" -m pip install -q -r backend\requirements.txt
  if errorlevel 1 goto :failed
)

pushd frontend
if not exist "node_modules" (
  echo [SpiderFly] 正在安装前端依赖...
  call npm install
  if errorlevel 1 goto :frontend_failed
)
echo [SpiderFly] 正在构建 Kocotree 风格前端...
call npm run build
if errorlevel 1 goto :frontend_failed
popd

echo [SpiderFly] 启动地址：http://127.0.0.1:8000
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000'"
".venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
goto :eof

:frontend_failed
popd

:failed
echo [SpiderFly] 启动失败，请查看上方错误。
pause
exit /b 1

@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure_lan_access.ps1" -Port 8000
if errorlevel 1 (
  echo.
  echo [SpiderFly] 局域网配置未完成；上方会显示原因。
) else (
  echo.
  echo [SpiderFly] 配置完成，现在可以把显示的地址发给伙伴。
)
echo.
pause

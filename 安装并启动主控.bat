@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
  set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0scripts\setup-controller.ps1"
if errorlevel 1 pause

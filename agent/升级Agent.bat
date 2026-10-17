@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0upgrade.ps1" %*
if errorlevel 1 pause

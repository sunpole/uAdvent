@echo off
setlocal
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%~dp0advent_gui.ps1"
exit /b

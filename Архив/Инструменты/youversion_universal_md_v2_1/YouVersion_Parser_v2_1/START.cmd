@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 "%~dp0youversion_md.py" %*
) else (
    python "%~dp0youversion_md.py" %*
)
echo.
pause

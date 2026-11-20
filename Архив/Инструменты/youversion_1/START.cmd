@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  set "PYTHON=py -3"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python 3 is not installed. Install it from https://www.python.org/downloads/windows/
    echo During setup, enable "Add Python to PATH".
    pause
    exit /b 1
  )
  set "PYTHON=python"
)
%PYTHON% -c "import playwright" >nul 2>nul
if errorlevel 1 (
  echo Installing required package: playwright
  %PYTHON% -m pip install playwright
  if errorlevel 1 (
    echo Cannot install playwright. Check internet access and Python/pip.
    pause
    exit /b 1
  )
)
%PYTHON% "%~dp0youversion_to_md.py"
echo.
pause

@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
where.exe py.exe >nul 2>nul
if errorlevel 1 goto use_python
py -3 -X utf8 "%~dp0Программа\cleaner.py" %*
goto done
:use_python
where.exe python.exe >nul 2>nul
if errorlevel 1 goto missing
python -X utf8 "%~dp0Программа\cleaner.py" %*
goto done
:missing
echo Для запуска нужен Python 3.10 или новее с компонентом tkinter.
pause
exit /b 1
:done
if not errorlevel 1 exit /b 0
if "%~1"=="" pause
exit /b 1

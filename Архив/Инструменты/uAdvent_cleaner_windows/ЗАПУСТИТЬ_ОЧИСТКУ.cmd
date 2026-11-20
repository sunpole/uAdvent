@echo off
setlocal
chcp 65001 >nul
title uAdvent - очистка планов YouVersion

where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 "%~dp0clean_advent.py" %*
) else (
    where python >nul 2>nul
    if %errorlevel% equ 0 (
        python "%~dp0clean_advent.py" %*
    ) else (
        echo.
        echo ОШИБКА: Python 3 не найден.
        echo Установите Python с https://www.python.org/downloads/
        echo Во время установки включите Add Python to PATH.
    )
)
echo.
echo Работа завершена. Нажмите любую клавишу, чтобы закрыть окно.
pause >nul

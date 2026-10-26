@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 main.py servir
) else (
  python main.py servir
)
pause

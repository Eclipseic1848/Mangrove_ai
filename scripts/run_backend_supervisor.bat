@echo off
chcp 65001 >nul
setlocal
set "PY=%~1"
set "ROOT=%~2"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%ROOT%"

:restart_backend
"%PY%" -X utf8 -u scripts\dev_reload.py --mangrove-service-root "%ROOT%"
set "BACKEND_EXIT=%ERRORLEVEL%"
>>"%ROOT%\logs\dev_reload.log" echo [%date% %time%] [Mangrove 外层监督] dev_reload 退出（退出码 %BACKEND_EXIT%），2 秒后恢复。
echo [Mangrove 外层监督] dev_reload 退出（退出码 %BACKEND_EXIT%），2 秒后恢复。
timeout /t 2 /nobreak >nul
goto restart_backend

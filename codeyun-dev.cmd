@echo off
setlocal

cd /d "%~dp0"

set CODEYUN_DEV_CONSOLE_HOST=1
if not defined CODEYUN_DEV_BACKEND_RELOAD_MODE set CODEYUN_DEV_BACKEND_RELOAD_MODE=outer

where uv >nul 2>nul
if %ERRORLEVEL%==0 (
    uv run dev.py %*
    exit /b %ERRORLEVEL%
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" dev.py %*
    exit /b %ERRORLEVEL%
)

echo uv is not available and .venv\Scripts\python.exe was not found.
echo Install uv dependencies first, then run this launcher again.
exit /b 1

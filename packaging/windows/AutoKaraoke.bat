@echo off
chcp 65001 >nul
rem AutoKaraoke launcher. First start: uv installs Python and the editor libraries
rem (about 300 MB) into %LOCALAPPDATA%\AutoKaraoke; the AI components are downloaded
rem from inside the app.
setlocal
set "HERE=%~dp0"
set "APP=%HERE%app"
set "DATA=%LOCALAPPDATA%\AutoKaraoke"
set /p LOCK_HASH=<"%APP%\LOCKHASH"
set "PATH=%HERE%bin;%PATH%"
set "UV_PROJECT_ENVIRONMENT=%DATA%\venv"
set "UV_PYTHON_INSTALL_DIR=%DATA%\python"
set "UV_PYTHON_PREFERENCE=only-managed"
set "AUTOKARAOKE_UV=%HERE%bin\uv.exe"
set "AUTOKARAOKE_APP=%APP%"
set "AUTOKARAOKE_LOCK_HASH=%LOCK_HASH%"
if not defined AUTOKARAOKE_PROJECTS set "AUTOKARAOKE_PROJECTS=%USERPROFILE%\Documents\AutoKaraoke"
set "PYTHONPATH=%APP%\src"
set "PYTHONHOME="

if not exist "%DATA%\venv\.autokaraoke-base-%LOCK_HASH%" (
    echo 正在准备 AutoKaraoke：首次启动或更新约需下载 300 MB...
    "%AUTOKARAOKE_UV%" sync --project "%APP%" --frozen --no-install-project --no-dev --inexact --extra gui
    if errorlevel 1 (
        echo.
        echo 安装失败，请检查网络连接后重试。
        pause
        exit /b 1
    )
    type nul > "%DATA%\venv\.autokaraoke-base-%LOCK_HASH%"
)
if not exist "%AUTOKARAOKE_PROJECTS%" mkdir "%AUTOKARAOKE_PROJECTS%"
start "" "%DATA%\venv\Scripts\pythonw.exe" -m autokaraoke.cli gui %*

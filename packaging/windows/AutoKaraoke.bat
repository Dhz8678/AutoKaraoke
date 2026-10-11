@echo off
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
    echo Preparing AutoKaraoke: first start or update, downloads about 300 MB...
    "%AUTOKARAOKE_UV%" sync --project "%APP%" --frozen --no-install-project --no-dev --inexact --extra gui
    if errorlevel 1 (
        echo.
        echo Setup failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
    type nul > "%DATA%\venv\.autokaraoke-base-%LOCK_HASH%"
)
if not exist "%AUTOKARAOKE_PROJECTS%" mkdir "%AUTOKARAOKE_PROJECTS%"
start "" "%DATA%\venv\Scripts\pythonw.exe" -m autokaraoke.cli gui %*

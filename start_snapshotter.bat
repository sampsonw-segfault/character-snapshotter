@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Setting up Character Snapshotter virtual environment...
    where py >nul 2>&1
    if %errorlevel%==0 (
        py -3.14 -m venv .venv 2>nul
        if not exist ".venv\Scripts\python.exe" (
            py -3 -m venv .venv
        )
    ) else (
        python -m venv .venv
    )

    if not exist ".venv\Scripts\python.exe" (
        echo Failed to create virtual environment. Make sure Python 3.10+ is installed.
        pause
        exit /b 1
    )

    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Failed to install Python dependencies.
        pause
        exit /b 1
    )
)

start "" ".venv\Scripts\pythonw.exe" "src\main.py"

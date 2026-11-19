@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up Character Snapshotter build virtual environment...
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
)

".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 (
    echo Failed to install build dependencies.
    pause
    exit /b 1
)

if not exist "assets\app.ico" (
    ".venv\Scripts\python.exe" scripts\generate_icon.py
    if errorlevel 1 (
        echo Failed to generate application icon.
        pause
        exit /b 1
    )
)

".venv\Scripts\pyinstaller.exe" --clean --noconfirm CharacterSnapshotter.spec
if errorlevel 1 (
    echo PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo Built dist\CharacterSnapshotter.exe successfully.

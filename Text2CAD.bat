@echo off
rem Starts the Text2CAD desktop app without a console window.
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Text2CAD is not installed yet. Double-click install.bat first.
    pause
    exit /b 1
)
cd /d "%~dp0"
start "" "%~dp0.venv\Scripts\pythonw.exe" -m text2cad.gui

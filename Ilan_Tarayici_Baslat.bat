@echo off
cd /d "%~dp0"
set PYTHONPATH=src
start "" "%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe" -m scanner tray

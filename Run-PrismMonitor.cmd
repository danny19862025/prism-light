@echo off
rem Start Prism Monitor from source (standard user; no administrator rights needed).
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe call Setup-Windows.cmd --no-driver
.venv\Scripts\python.exe toolsetch_sensors.py >nul
.venv\Scripts\python.exe toolsetch_monitor_deps.py >nul
start "" .venv\Scripts\pythonw.exe monitor_main.py

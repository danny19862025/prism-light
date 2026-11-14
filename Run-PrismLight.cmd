@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call Setup-Windows.cmd --no-driver
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
  call Setup-Windows.cmd --no-driver
  if errorlevel 1 goto failed
)
if not exist "vendor\openrgb\manifest.json" (
  ".venv\Scripts\python.exe" tools\fetch_openrgb.py
  if errorlevel 1 goto failed
)
if /i "%~1"=="--demo" goto run
if /i "%~1"=="--smoke-test" goto run
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "tools\setup_pawnio.ps1"
if errorlevel 1 goto failed
set "PRISM_PYTHON=%~dp0.venv\Scripts\pythonw.exe"
set "PRISM_DIR=%~dp0."
set "PRISM_ARGS=main.py %*"
powershell.exe -NoProfile -Command "Start-Process -FilePath $env:PRISM_PYTHON -ArgumentList $env:PRISM_ARGS -WorkingDirectory $env:PRISM_DIR -Verb RunAs"
if errorlevel 1 goto failed
exit /b 0
:run
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo Prism Light could not start. Read the error above and the Windows section in README.md.
pause
exit /b 1

@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call Setup-Windows.cmd
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
  call Setup-Windows.cmd
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo Prism Light could not start. Read the error above and the Windows section in README.md.
pause
exit /b 1

@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Install 64-bit Python 3.12 or newer from https://www.python.org/downloads/windows/
  echo Include the Python launcher, then run this file again.
  exit /b 1
)
py -3 -c "import sys; assert sys.version_info >= (3,12), 'Python 3.12 or newer is required'"
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
echo Windows setup complete.
exit /b 0

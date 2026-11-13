@echo off
setlocal
cd /d "%~dp0"
call Setup-Windows.cmd
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto failed
set QT_QPA_PLATFORM=offscreen
".venv\Scripts\python.exe" -m unittest discover -s tests -v
if errorlevel 1 goto failed
".venv\Scripts\python.exe" build.py
if errorlevel 1 goto failed
start "" /wait "dist\PrismLight\PrismLight.exe" --smoke-test
if errorlevel 1 goto failed
echo Built and checked dist\PrismLight\PrismLight.exe. Keep the entire PrismLight folder together.
echo A portable Windows ZIP is also available in dist.
pause
exit /b 0
:failed
echo Windows build failed. Read the error above before trying again.
pause
exit /b 1

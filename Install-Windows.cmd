@echo off
setlocal
cd /d "%~dp0"
if not exist "PrismLight.exe" (
  call Setup-Windows.cmd
  if errorlevel 1 goto failed
  call Run-PrismLight.cmd
  exit /b
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "tools\setup_pawnio.ps1"
if errorlevel 1 goto failed
set "PRISM_EXE=%~dp0PrismLight.exe"
set "PRISM_DIR=%~dp0."
powershell.exe -NoProfile -Command "Start-Process -FilePath $env:PRISM_EXE -WorkingDirectory $env:PRISM_DIR -Verb RunAs"
if errorlevel 1 goto failed
exit /b 0
:failed
echo Installation did not complete. See the error above and retry.
pause
exit /b 1

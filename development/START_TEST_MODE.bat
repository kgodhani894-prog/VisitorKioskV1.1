@echo off
set "LAUNCHER=%~dp0tools\launch.ps1"
if exist "%~dp0app\tools\launch.ps1" set "LAUNCHER=%~dp0app\tools\launch.ps1"
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%LAUNCHER%" -Mode Test
if errorlevel 1 (
  echo The Elite RF Visitor Kiosk test launcher could not start.
  pause
)

@echo off
setlocal
title Set Up Elite RF Visitor Kiosk
echo Starting the Elite RF company setup assistant...
set "SETUP=%~dp0tools\company_setup.ps1"
set "PROJECT=%~dp0"
if exist "%~dp0app\tools\company_setup.ps1" (
  set "SETUP=%~dp0app\tools\company_setup.ps1"
  set "PROJECT=%~dp0app"
)
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%SETUP%" -ProjectPath "%PROJECT%"
if errorlevel 1 (
  echo.
  echo Setup is not complete. Read the messages above, then run this file again.
)
echo.
pause

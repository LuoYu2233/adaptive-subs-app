@echo off
rem Run Adaptive Subs from the source code (developers). Users: download the Windows program from
rem GitHub Releases instead. Needs the .venv described in docs/开发说明.md.
rem Opens the same window as AdaptiveSubs.exe; settings, results and logs stay in this folder.
cd /d "%~dp0"
title Adaptive Subs (source)
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   .venv not found. Set it up first, see docs\开发说明.md:
  echo     python -m venv .venv
  echo     .venv\Scripts\python -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -u app\main.py %*
if errorlevel 1 pause

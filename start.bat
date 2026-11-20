@echo off
rem Double-click to watch locally: starts the local server in this folder and opens the player.
rem With the project's .venv it runs server.py (paste any YouTube link, annotations stream in live);
rem server.py picks a free port by itself and opens the browser at the right address.
rem Without .venv, a plain file server (saved videos only).
rem (Opening player.html directly does not work: browsers block loading data from file:// pages.)
cd /d "%~dp0"
title Adaptive Subs - local server
if not defined PORT set PORT=8765
echo.
echo   Keep this window open while watching. Close it to stop.
if exist ".venv\Scripts\python.exe" goto live

echo   .venv not found: saved videos only. See README to enable opening any YouTube link.
echo   Player: http://127.0.0.1:%PORT%/player.html
if not defined ASUBS_NO_BROWSER start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:%PORT%/player.html"
python -m http.server %PORT% --bind 127.0.0.1
goto after

:live
".venv\Scripts\python.exe" -u server.py --port %PORT% --open

:after
if errorlevel 1 (
  echo.
  echo   Could not start the server. Check that Python is installed: run  python --version
  echo.
  pause
)

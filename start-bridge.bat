@echo off
rem Starts the Telescopius bridge and opens its web page. Keep this window open while using NINA.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m telescopius_bridge --open %*
) else (
  python -m telescopius_bridge --open %*
)
if errorlevel 1 pause

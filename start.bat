@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 server.py
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python 3.9+ required: https://www.python.org/downloads/
  ) else (
    python server.py
  )
)
pause

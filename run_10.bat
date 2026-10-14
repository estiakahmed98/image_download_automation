@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. Run setup_windows.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" main.py --limit 10 %*
set "download_exit=%errorlevel%"
pause
exit /b %download_exit%

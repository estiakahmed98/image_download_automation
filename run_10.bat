@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe main.py --input "Products Name.txt" --category "Accessories/Keyboard" --limit 10 --max-images 5
pause

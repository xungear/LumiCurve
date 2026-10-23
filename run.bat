@echo off
rem LumiCurve - start the local server and open the browser
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.10+ and run:  pip install -r requirements.txt
  pause
  exit /b 1
)
python app\server.py
pause

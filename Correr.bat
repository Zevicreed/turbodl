@echo off
cd /d %~dp0
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" app.py
  goto :fin
)
where python >nul 2>nul
if %errorlevel%==0 (
  python -m pip install -q -r requirements.txt
  python app.py
  goto :fin
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -m pip install -q -r requirements.txt
  py app.py
  goto :fin
)
if exist "%USERPROFILE%\.local\bin\uv.exe" (
  "%USERPROFILE%\.local\bin\uv.exe" run --with yt-dlp python app.py
  goto :fin
)
echo No encontre Python ni uv en esta PC. Instala Python desde python.org marcando "Add python.exe to PATH".
:fin
if errorlevel 1 pause

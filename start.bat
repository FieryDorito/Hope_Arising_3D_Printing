@echo off
REM Double-click to open the print shop app in the browser.
cd /d "%~dp0"
set OKVER=import sys; sys.exit(0 if (3,12) ^<= sys.version_info[:2] ^<= (3,14) else 1)

REM 1. Find Python 3.13 (or 3.12 / 3.14). Newer versions lack ready-made add-ons.
call :findpy
if not defined PY (
  echo Installing Python 3.13 for the app. This only happens once...
  py install 3.13
  call :findpy
)
if not defined PY (
  echo.
  echo This app needs Python 3.13. Install it from python.org
  echo ^(Windows installer, 64-bit^). Your other Python versions can stay.
  echo Then run this again.
  echo.
  pause
  exit /b 1
)

REM 2. Make the private Python folder (.venv), rebuilding it if it uses the wrong version
if exist .venv\Scripts\python.exe .venv\Scripts\python.exe -c "%OKVER%" >nul 2>&1
if exist .venv\Scripts\python.exe if errorlevel 1 (
  echo Rebuilding the app's Python folder with the right version...
  rmdir /s /q .venv
)
if not exist .venv\Scripts\python.exe (
  echo Setting up for the first time...
  %PY% -m venv .venv
)

REM 3. Install or repair the add-ons whenever any are missing (ready-made files only, no compiling)
.venv\Scripts\python.exe -c "import streamlit, trimesh, plotly, yaml, anthropic" >nul 2>&1
if errorlevel 1 (
  echo Installing what the app needs. This takes a few minutes, keep this window open...
  .venv\Scripts\python.exe -m pip install --disable-pip-version-check --only-binary=:all: -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Install failed. Take a screenshot of the messages above and send it to the team.
    pause
    exit /b 1
  )
)

REM 4. Check the settings files for mistakes before starting
.venv\Scripts\python.exe -m pipeline.config
if errorlevel 1 (
  pause
  exit /b 1
)

REM 5. Start the app (the browser opens by itself; keep this window open while using it)
echo Starting the app. A browser tab will open in a few seconds...
.venv\Scripts\python.exe -m streamlit run ui\app.py --server.headless false
pause
exit /b 0

:findpy
set PY=
for %%V in (3.13 3.12 3.14) do (
  if not defined PY py -%%V -c "%OKVER%" >nul 2>&1 && set PY=py -%%V
)
exit /b 0

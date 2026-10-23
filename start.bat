@echo off
REM Double-click to open the print shop app in the browser.
cd /d "%~dp0"

REM 1. Find Python 3.12 or newer
set PY=
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && set PY=py -3
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1 && set PY=python
if not defined PY (
  echo.
  echo Python 3.12 or newer is needed. Install it from python.org
  echo and tick "Add python.exe to PATH" during install. Then run this again.
  echo.
  pause
  exit /b 1
)

REM 2. Make the private Python folder (.venv) if missing or broken
.venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" >nul 2>&1
if errorlevel 1 (
  echo Setting up for the first time...
  if exist .venv rmdir /s /q .venv
  %PY% -m venv .venv
)

REM 3. Install or repair the add-ons whenever any are missing
.venv\Scripts\python.exe -c "import streamlit, trimesh, plotly, yaml, anthropic" >nul 2>&1
if errorlevel 1 (
  echo Installing what the app needs. This takes a few minutes, keep this window open...
  .venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Install failed. Take a screenshot of the messages above and send it to the team.
    pause
    exit /b 1
  )
)

REM 4. Start the app
.venv\Scripts\python.exe -m streamlit run ui\app.py --server.headless false
pause

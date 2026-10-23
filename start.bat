@echo off
REM Double-click to open the print shop app in the browser.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo First-time setup, please wait...
  py -3 -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python -m streamlit run ui\app.py --server.headless false
pause

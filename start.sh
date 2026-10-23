#!/usr/bin/env bash
# Start the print shop app (Linux/macOS).
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python -m streamlit run ui/app.py --server.headless false

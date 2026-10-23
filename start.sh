#!/usr/bin/env bash
# Start the print shop app (Linux/macOS).
cd "$(dirname "$0")"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' || { echo "Python 3.12+ is needed."; exit 1; }
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -c "import streamlit, trimesh, plotly, yaml, anthropic" 2>/dev/null \
  || .venv/bin/python -m pip install -r requirements.txt || { echo "Install failed."; exit 1; }
.venv/bin/python -m streamlit run ui/app.py --server.headless false

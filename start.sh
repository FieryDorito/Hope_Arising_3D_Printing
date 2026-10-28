#!/usr/bin/env bash
# Start the print shop app (Linux/macOS).
cd "$(dirname "$0")"
python3 -c 'import sys; sys.exit(0 if (3,12) <= sys.version_info[:2] <= (3,14) else 1)' || { echo "Python 3.12-3.14 is needed (3.13 recommended)."; exit 1; }
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -c "import streamlit, trimesh, plotly, yaml, anthropic, fast_simplification" 2>/dev/null \
  || .venv/bin/python -m pip install -r requirements.txt || { echo "Install failed."; exit 1; }
.venv/bin/python -m streamlit run ui/app.py --server.headless false

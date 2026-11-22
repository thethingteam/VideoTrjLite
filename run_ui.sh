#!/bin/sh
# Start the desktop UI from source (Linux / macOS). Windows: run_ui.bat
cd "$(dirname "$0")" || exit 1
if [ ! -x ".venv/bin/python" ]; then
  echo "Virtual environment not found. Create it with:"
  echo "  python3 -m venv .venv"
  echo "  .venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi
exec .venv/bin/python main.py "$@"

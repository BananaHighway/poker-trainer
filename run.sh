#!/usr/bin/env bash
# macOS/Linux launcher. No args: start the app at http://127.0.0.1:8765
cd "$(dirname "$0")"
if [ $# -eq 0 ]; then
  echo "Poker Trainer -> http://127.0.0.1:8765"
  exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
else
  exec .venv/bin/python "$@"
fi

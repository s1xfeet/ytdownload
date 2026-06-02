#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PORT="${YTDOWNLOAD_PORT:-8765}"

if [ ! -d ".venv" ]; then
  echo "Creating venv…"
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
fi

# Find a free port if PORT is taken.
while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

URL="http://127.0.0.1:$PORT"
echo "Starting YTDownload at $URL"

# Open the browser shortly after the server starts.
( sleep 1.2; open "$URL" ) &

exec ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT"

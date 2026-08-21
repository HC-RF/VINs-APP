#!/usr/bin/env bash
# Start the VIN Decoder locally on macOS/Linux.
#
#   ./run.sh            # start the server
#   ./run.sh test       # run the test suite instead
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=".venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
  echo "Installing dependencies..."
  "$PYTHON" -m pip install --quiet --upgrade pip
  "$PYTHON" -m pip install --quiet -r requirements-dev.txt
fi

if [ ! -f .env ]; then
  echo "No .env found - copying .env.example (free providers only, no API cost)."
  cp .env.example .env
fi

if [ "${1:-}" = "test" ]; then
  exec "$PYTHON" -m pytest
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
echo
echo "  VIN Decoder -> http://${HOST}:${PORT}"
echo "  API docs    -> http://${HOST}:${PORT}/api/docs"
echo
exec "$PYTHON" -m uvicorn app.main:app --host "$HOST" --port "$PORT" --reload

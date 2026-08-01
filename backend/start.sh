#!/usr/bin/env bash
cd "$(dirname "$0")"
echo "[ElementWar] Installing dependencies..."
pip install -q -r requirements.txt
echo "[ElementWar] Starting server at http://localhost:3000"
echo "[ElementWar] Web client: http://localhost:3000/"
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 3000

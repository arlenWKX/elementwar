@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [ElementWar] Installing dependencies...
pip install -q -r requirements.txt
echo [ElementWar] Starting server at http://localhost:3000
echo [ElementWar] Web client: http://localhost:3000/
python -m uvicorn app.main:app --host 0.0.0.0 --port 3000

#!/usr/bin/env bash
# Render-safe: health on $PORT + bot as main process
set +e
PORT="${PORT:-8080}"

python3 -u -c "
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(os.environ.get('PORT', '8080'))
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{\"status\":\"online\",\"bot\":\"Ash_best_movie_bot\"}')
    def log_message(self, *a):
        pass
print('[health] listening on', port, flush=True)
HTTPServer(('0.0.0.0', port), H).serve_forever()
" &
echo "[start] health started port=$PORT"

# Pre-check: critical modules must import (catch missing b64 early)
python3 -u -c "
import sys
sys.path.insert(0, '.')
errors = []
for mod in ['handlers.force_sub_handler', 'handlers.save_media', 'handlers.group_filter', 'handlers.database']:
    try:
        __import__(mod)
        print(f'[check] {mod} OK', flush=True)
    except Exception as e:
        print(f'[check] {mod} FAIL: {type(e).__name__}: {e}', flush=True)
        errors.append(mod)
if errors:
    print('[FATAL] import failed:', errors, flush=True)
" 2>&1 || true

echo "[start] launching bot.py ..."
exec python3 -u bot.py

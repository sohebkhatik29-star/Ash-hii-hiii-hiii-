#!/usr/bin/env bash
# Render-safe starter: bind $PORT (health) + run bot as main process
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
HEALTH_PID=$!
echo "[start] health pid=$HEALTH_PID port=$PORT"

# Fail fast if save_media is still a broken loader
python3 -c "
from pathlib import Path
p = Path('handlers/save_media.py')
n = len(p.read_text().splitlines()) if p.exists() else 0
print(f'[check] save_media.py lines={n}', flush=True)
if n < 50:
    raise SystemExit('FATAL: handlers/save_media.py is still a loader/placeholder. Deploy full file.')
"

echo "[start] launching bot.py ..."
exec python3 -u bot.py

"""
Local dev server that serves static files and proxies external API requests
to avoid CORS issues. Used by the weather comparison dashboard.

Usage: python3 server.py          # starts on port 8080
       python3 server.py 9000     # custom port
"""

import http.server
import json
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
ROOT = Path(__file__).parent
PREDICTIONS_DIR = ROOT / 'predictions'


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if self.path.startswith('/proxy?'):
            self.handle_proxy()
        elif self.path == '/predictions':
            self.handle_list_predictions()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == '/save-prediction':
            self.handle_save_prediction()
        else:
            self.send_error(404)

    def handle_save_prediction(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, 'Invalid JSON')
            return

        station = payload.get('station', 'UNKN').upper()
        date_str = payload.get('date', 'no-date')
        now_utc = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        filename = f'{station}_{date_str}_{now_utc}.json'

        PREDICTIONS_DIR.mkdir(exist_ok=True)
        out_path = PREDICTIONS_DIR / filename
        out_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        print(f'  SAVED → predictions/{filename} ({len(body)} bytes)')

        resp = json.dumps({'ok': True, 'file': filename}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(resp))
        self.end_headers()
        self.wfile.write(resp)

    def handle_list_predictions(self):
        files = []
        if PREDICTIONS_DIR.is_dir():
            files = sorted(
                [f.name for f in PREDICTIONS_DIR.glob('*.json')],
                reverse=True,
            )
        resp = json.dumps(files).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(resp))
        self.end_headers()
        self.wfile.write(resp)

    def handle_proxy(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        url = params.get('url', [None])[0]

        if not url:
            self.send_error(400, 'Missing url parameter')
            return

        allowed = ('api.weather.com', 'aviationweather.gov', 'api.weather.gov',
                   'mesonet.agron.iastate.edu', 'ensemble-api.open-meteo.com',
                   'api.open-meteo.com')
        host = urllib.parse.urlparse(url).hostname
        if host not in allowed:
            self.send_error(403, f'Host {host} not in allowlist')
            return

        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'WeatherDashboard/1.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                content_type = resp.headers.get('Content-Type', 'application/json')
        except Exception as e:
            self.send_error(502, str(e))
            return

        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        path = args[0].split()[1] if args else ''
        if path.startswith('/proxy'):
            url = urllib.parse.parse_qs(urllib.parse.urlparse(path).query).get('url', [''])[0]
            host = urllib.parse.urlparse(url).hostname or '?'
            print(f"  PROXY → {host}{urllib.parse.urlparse(url).path}")
        elif not path.startswith(('/favicon', '/.', '/node_modules')):
            super().log_message(format, *args)


if __name__ == '__main__':
    with http.server.HTTPServer(('', PORT), Handler) as srv:
        print(f'Serving on http://localhost:{PORT}')
        print(f'  Dashboards:')
        print(f'    http://localhost:{PORT}/weather_predict.html')
        print(f'    http://localhost:{PORT}/weather_dashboard_metar.html')
        print(f'    http://localhost:{PORT}/weather_compare.html')
        print(f'  Proxy: /proxy?url=<encoded_url>')
        print()
        srv.serve_forever()

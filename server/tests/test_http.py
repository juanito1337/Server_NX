"""Real HTTP smoke test, using a subprocess and isolated local state."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def test_real_http_server_and_gui(tmp_path):
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'data_dir': str(tmp_path / 'managed'), 'sources': {}, 'logs': {}}), encoding='utf-8')
    token = 'isolated-http-test-token-not-real-' + 'c' * 32
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    environment = {**os.environ, 'NX_ADMIN_TOKEN': token}
    environment.pop('NX_ERP_TOKEN', None)
    process = subprocess.Popen([sys.executable, '-m', 'nxserver', '--settings', str(settings), '--port', str(port)],
                               cwd=Path(__file__).resolve().parents[1], env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(base + '/health', timeout=1) as response:
                    assert json.load(response)['mode'] == 'local-pilot'
                break
            except (urllib.error.URLError, TimeoutError):
                if process.poll() is not None:
                    raise AssertionError('HTTP server terminated during startup.')
                time.sleep(0.1)
        else:
            raise AssertionError('HTTP server did not become ready.')
        with urllib.request.urlopen(base + '/') as response:
            html = response.read().decode('utf-8')
            assert 'NX-Verwaltung' in html
            assert response.headers['X-Frame-Options'] == 'DENY'
        with urllib.request.urlopen(base + '/app.js') as response:
            javascript = response.read().decode('utf-8')
            assert 'Authorization' in javascript
        request = urllib.request.Request(base + '/api/v1/resources', headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(request) as response:
            assert json.load(response) == []
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

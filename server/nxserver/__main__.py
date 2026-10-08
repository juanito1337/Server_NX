import argparse

import uvicorn

from .api import create_app
from .config import startup
from .store import Store


def main():
    parser = argparse.ArgumentParser(description='Local NX administration pilot (no production connection).')
    parser.add_argument('--settings', required=True)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', choices=('127.0.0.1', '0.0.0.0'), default='127.0.0.1')
    options = parser.parse_args()
    if not 1 <= options.port <= 65535:
        parser.error('--port must be between 1 and 65535.')
    settings, token, erp_token, generated = startup(options.settings, options.host)
    store = Store(settings['data_dir'], settings.get('sources'), settings.get('logs'), settings.get('standards'),
                  settings.get('display_paths'))
    if generated:
        print('Lokaler Testschluessel (nur fuer diese Sitzung, in der GUI eingeben): ' + token, flush=True)
    app = create_app(store, token, erp_token)
    print(f'NX-Verwaltung: Listener {options.host}:{options.port} — keine Produktivanbindung', flush=True)
    uvicorn.run(app, host=options.host, port=options.port, workers=1)


if __name__ == '__main__':
    main()

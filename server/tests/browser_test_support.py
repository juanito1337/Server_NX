"""Bounded readiness checks for disposable local browser-test servers only."""
import http.client
import json
import time
import urllib.error
import urllib.request


def wait_for_test_server(process, port, *, timeout=15):
    """Do not attach a browser to an unready server or accept arbitrary HTTP."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('A local test port is required.')
    if type(timeout) not in (int, float) or not 0 < timeout <= 60:
        raise ValueError('A bounded test-server timeout is required.')
    # Environment proxy settings must not redirect this isolated loopback probe.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    last = 'no HTTP response'
    expected = {'status': 'ok', 'mode': 'local-pilot', 'production_connected': False}
    while True:
        code = process.poll()
        if code is not None:
            raise AssertionError(f'Isolated test server ended with exit code {code}; last probe: {last}.')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f'Isolated test server not ready within {timeout}s; last probe: {last}.')
        try:
            with opener.open(f'http://127.0.0.1:{port}/health', timeout=min(1, remaining)) as response:
                raw = response.read(4097)
                if response.status != 200 or len(raw) > 4096:
                    last = 'unexpected health status or response size'
                else:
                    value = json.loads(raw.decode('utf-8'))
                    if value == expected and type(value) is dict and value.get('production_connected') is False:
                        code = process.poll()
                        if code is not None:
                            raise AssertionError(f'Isolated test server ended with exit code {code} during readiness.')
                        return
                    last = 'unexpected health contract'
        except urllib.error.HTTPError as error:
            last = f'HTTP status {error.code}'
            error.close()
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException,
                ValueError, UnicodeError) as error:
            # Startup connections can fail before an HTTP response exists. They
            # are not readiness, nor permission to hide persistent failures.
            last = type(error).__name__
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(.1, remaining))

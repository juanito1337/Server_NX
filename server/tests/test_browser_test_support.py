"""Readiness failure/retry boundaries without a browser, socket, NX or server."""
import http.client
import io
import json
import urllib.error

import pytest

import browser_test_support as support


READY = b'{"status":"ok","mode":"local-pilot","production_connected":false}'


class Response:
    def __init__(self, raw=READY, status=200):
        self.raw, self.status, self.closed = raw, status, False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def read(self, limit):
        assert limit == 4097
        return self.raw[:limit]


class Process:
    def __init__(self, codes=()):
        self.codes = list(codes)

    def poll(self):
        return self.codes.pop(0) if self.codes else None


@pytest.fixture
def probe(monkeypatch):
    class Probe:
        now = 0

        def __init__(self):
            self.responses, self.calls, self.sleeps = [], [], []

        def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.now += seconds

        def open(self, url, *, timeout):
            self.calls.append((url, timeout))
            item = self.responses.pop(0) if self.responses else urllib.error.URLError('synthetic unavailable')
            if isinstance(item, BaseException):
                raise item
            return item

    value = Probe()
    def opener(handler):
        assert type(handler) is support.urllib.request.ProxyHandler
        assert handler.proxies == {}
        return value
    monkeypatch.setattr(support.urllib.request, 'build_opener', opener)
    monkeypatch.setattr(support.time, 'monotonic', lambda: value.now)
    monkeypatch.setattr(support.time, 'sleep', value.sleep)
    return value


@pytest.mark.parametrize('error', [
    urllib.error.URLError('synthetic unavailable'), TimeoutError('synthetic timeout'),
    ConnectionResetError('synthetic reset'), http.client.BadStatusLine('GET /health HTTP/1.1'),
    http.client.RemoteDisconnected('synthetic disconnected'),
])
def test_startup_transport_errors_are_not_readiness_and_can_recover(probe, error):
    ready = Response()
    probe.responses = [error, ready]
    support.wait_for_test_server(Process(), 54321, timeout=.3)
    assert ready.closed and len(probe.calls) == 2 and probe.sleeps == [.1]
    assert all(url == 'http://127.0.0.1:54321/health' and 0 < timeout <= .3
               for url, timeout in probe.calls)


@pytest.mark.parametrize('raw,status', [
    (b'not json', 200), (b'\xff', 200), (b'{}', 200), (b'[]', 200),
    (b'null', 200), (READY, 201), (READY + b' ' * 4096, 200),
    (READY.replace(b'false', b'0'), 200),
    (READY.replace(b'false', b'true'), 200),
    (READY.replace(b'local-pilot', b'unrelated'), 200),
    (json.dumps({'status': 'ok', 'mode': 'local-pilot', 'production_connected': False, 'extra': 1}).encode(), 200),
])
def test_unexpected_http_or_health_body_is_not_success(probe, raw, status):
    bad, ready = Response(raw, status), Response()
    probe.responses = [bad, ready]
    support.wait_for_test_server(Process(), 54321, timeout=.3)
    assert bad.closed and ready.closed and len(probe.calls) == 2


def test_http_error_is_closed_before_retry(probe):
    stream = io.BytesIO(b'synthetic unavailable')
    error = urllib.error.HTTPError('http://127.0.0.1:54321/health', 503, 'Unavailable', {}, stream)
    probe.responses = [error, Response()]
    support.wait_for_test_server(Process(), 54321, timeout=.3)
    assert stream.closed and len(probe.calls) == 2


def test_persistent_unreadiness_fails_at_deadline_without_unbounded_retries(probe):
    with pytest.raises(AssertionError, match='not ready within 0.25s.*URLError'):
        support.wait_for_test_server(Process(), 54321, timeout=.25)
    assert probe.now == .25 and len(probe.calls) == 3
    assert all(0 < timeout <= .25 for _, timeout in probe.calls)


@pytest.mark.parametrize('codes,attempts', [([4], 0), ([None, 4], 1)])
def test_exited_child_is_not_accepted_even_if_health_is_available(probe, codes, attempts):
    probe.responses = [Response()]
    with pytest.raises(AssertionError, match='exit code 4'):
        support.wait_for_test_server(Process(codes), 54321, timeout=.3)
    assert len(probe.calls) == attempts


@pytest.mark.parametrize('port', [True, 0, -1, 65536, '54321', 54321.0])
def test_invalid_port_never_probes(probe, port):
    with pytest.raises(ValueError, match='test port'):
        support.wait_for_test_server(Process(), port)
    assert not probe.calls


@pytest.mark.parametrize('timeout', [True, 0, -1, 61, float('inf'), float('nan'), '15'])
def test_invalid_deadline_never_probes(probe, timeout):
    with pytest.raises(ValueError, match='timeout'):
        support.wait_for_test_server(Process(), 54321, timeout=timeout)
    assert not probe.calls

"""Actual collector download controls; disposable Edge, synthetic STEP, no NX."""
import hashlib
import io
import json
import os
import zipfile

import pytest

from nxserver.geometry import GeometryJobs
from test_geometry_recovery_browser import prepare
from test_requests_browser import ui, hold, track

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated collector downloads.')


@pytest.fixture
def collector_ui(ui):
    from playwright.sync_api import expect
    page, store, release, _ = ui
    tool, _ = prepare(page, store)
    page.locator('#step-job-create').click()
    expect(page.locator('#message')).to_contain_text('Auftrag registriert')
    job = GeometryJobs(store).list(tool['id'])[0]
    page.locator('#geometry-jobs').get_by_role('button', name='Ergebnispaket', exact=True).click()
    page.locator('#result-key').fill('CUSTOM')
    expect(page.locator('#result-collector')).to_be_enabled()
    downloads = []
    page.on('download', lambda item: downloads.append(item))
    yield page, store, release, tool, job, downloads
    assert next(item for item in store.resources() if item['id'] == tool['id']) == tool
    assert store.active()['release_id'] is None


def finish_held(page):
    page.evaluate('window.__releaseHeld()')
    page.wait_for_function('()=>window.__oldActionFinished')


@pytest.mark.parametrize('change', ['key', 'library', 'key_aba', 'library_aba', 'logout', 'new_job'])
def test_changed_context_does_not_publish_old_collector(collector_ui, change):
    from playwright.sync_api import expect
    page, store, _, tool, job, downloads = collector_ui
    hold(page, '/collector-package'); track(page, 'result-collector')
    page.locator('#result-collector').click(); page.wait_for_function('()=>window.__held')
    if change.startswith('key'):
        page.locator('#result-key').fill('NEWER')
        if change.endswith('aba'): page.locator('#result-key').fill('CUSTOM')
    elif change.startswith('library'):
        page.locator('#result-library').select_option('tool-english')
        if change.endswith('aba'): page.locator('#result-library').select_option('tool-metric')
    elif change == 'logout':
        page.locator('#disconnect').click()
    else:
        page.locator('#geometry-jobs').get_by_role('button', name='Ergebnispaket', exact=True).click()
        page.locator('#result-key').fill('FRESH_CONTEXT')
    finish_held(page)
    assert downloads == []
    expect(page.locator('#message')).not_to_contain_text('Collector in neuen')
    if change != 'logout': expect(page.locator('#result-collector')).to_be_enabled()
    # The already accepted immutable server snapshot is retained, not cancelled
    # or regenerated as another geometry job because the browser moved on.
    assert len(GeometryJobs(store).get(job['id'])['collector_packages']) == 1
    assert len(GeometryJobs(store).list(tool['id'])) == 1


@pytest.mark.parametrize('change', ['key_aba', 'logout'])
def test_stale_collector_error_does_not_replace_newer_notice(collector_ui, change):
    from playwright.sync_api import expect
    page, _, _, _, _, downloads = collector_ui
    hold(page, '/collector-package', error=True); track(page, 'result-collector')
    page.locator('#result-collector').click(); page.wait_for_function('()=>window.__held')
    if change == 'logout': page.locator('#disconnect').click()
    else:
        page.locator('#result-key').fill('NEWER'); page.locator('#result-key').fill('CUSTOM')
    finish_held(page)
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    assert downloads == []


@pytest.mark.parametrize('defect', ['same_size', 'short', 'long', 'missing_hash', 'missing_size', 'invalid_size', 'oversize'])
def test_invalid_received_collector_is_not_saved(collector_ui, defect):
    from playwright.sync_api import expect
    page, _, _, _, _, downloads = collector_ui
    def tamper(route):
        response = route.fetch(); raw = response.body(); headers = dict(response.headers)
        if defect == 'same_size': raw = bytes([raw[0] ^ 1]) + raw[1:]
        elif defect == 'short': raw = raw[:-1]
        elif defect == 'long': raw += b'x'
        elif defect == 'missing_hash': headers.pop('x-content-sha256', None)
        elif defect == 'missing_size': headers.pop('content-length', None)
        elif defect == 'invalid_size': headers['content-length'] = '3.5'
        else: headers['content-length'] = str(64 * 1024 * 1024 + 1)
        # Browser interception may synthesize Content-Length when omitted. Use
        # a response wrapper below for metadata defects, not malformed HTTP.
        route.fulfill(status=200, body=raw, headers=headers)
    if defect in ('missing_size', 'invalid_size', 'oversize'):
        page.evaluate('''defect=>{const original=window.fetch;window.fetch=async(...args)=>{
          const response=await original(...args);if(!String(args[0]).endsWith('/collector-package'))return response;
          const get=response.headers.get.bind(response.headers);response.headers.get=name=>
            name.toLowerCase()==='content-length'?(defect==='missing_size'?null:defect==='invalid_size'?'3.5':String(64*1024*1024+1)):get(name);
          return response;};}''', defect)
    else: page.route('**/collector-package', tamper)
    track(page, 'result-collector'); page.locator('#result-collector').click()
    page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#message')).to_have_class('error')
    assert downloads == []
    expect(page.locator('#result-collector')).to_be_enabled()


def test_successful_collector_uses_streaming_receive_and_exact_snapshot(collector_ui):
    from playwright.sync_api import expect
    page, store, _, _, job, downloads = collector_ui
    page.evaluate('''()=>{const original=window.fetch;window.fetch=async(...args)=>{
      const response=await original(...args);if(String(args[0]).endsWith('/collector-package')){
        response.arrayBuffer=()=>{throw new Error('Unbounded arrayBuffer path used');};
        response.blob=()=>{throw new Error('Unbounded blob path used');};}return response;};}''')
    with page.expect_download() as event: page.locator('#result-collector').click()
    downloaded = event.value
    assert downloaded.failure() is None and downloaded.suggested_filename == 'NX_Result_Collector_' + job['id'][:8] + '.zip'
    raw = downloaded.path().read_bytes()
    snapshots = GeometryJobs(store).get(job['id'])['collector_packages']
    assert hashlib.sha256(raw).hexdigest() in snapshots
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        configuration = json.loads(archive.read('collector.json'))
        assert configuration['root_key'] == 'CUSTOM' and configuration['root_library_id'] == 'tool-metric'
    expect(page.locator('#message')).to_contain_text('Collector in neuen')
    assert len(downloads) == 1


def test_double_click_accepts_only_one_collector_request(collector_ui):
    page, _, _, _, _, downloads = collector_ui
    requests = []
    page.on('request', lambda request: requests.append(request.url) if request.url.endswith('/collector-package') else None)
    hold(page, '/collector-package'); track(page, 'result-collector')
    page.locator('#result-collector').click(); page.wait_for_function('()=>window.__held')
    page.evaluate("document.getElementById('result-collector').onclick()")
    finish_held(page)
    assert len(requests) == 1 and len(downloads) == 1


def test_new_download_survives_late_completion_of_previous_context(collector_ui):
    from playwright.sync_api import expect
    page, store, _, _, job, downloads = collector_ui
    hold(page, '/collector-package'); track(page, 'result-collector')
    page.locator('#result-collector').click(); page.wait_for_function('()=>window.__held')
    page.locator('#geometry-jobs').get_by_role('button', name='Ergebnispaket', exact=True).click()
    page.locator('#result-key').fill('NEWER')
    with page.expect_download() as event: page.locator('#result-collector').click()
    downloaded = event.value
    with zipfile.ZipFile(downloaded.path()) as archive:
        assert json.loads(archive.read('collector.json'))['root_key'] == 'NEWER'
    finish_held(page)
    assert len(downloads) == 1
    expect(page.locator('#result-key')).to_have_value('NEWER')
    expect(page.locator('#result-collector')).to_be_enabled()
    assert len(GeometryJobs(store).get(job['id'])['collector_packages']) == 2


def test_current_network_failure_is_visible_and_explicit_retry_works(collector_ui):
    from playwright.sync_api import expect
    page, store, _, _, job, downloads = collector_ui
    page.route('**/collector-package', lambda route: route.abort('connectionfailed'))
    track(page, 'result-collector'); page.locator('#result-collector').click()
    page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#message')).to_have_class('error')
    expect(page.locator('#result-collector')).to_be_enabled()
    assert downloads == []
    page.unroute('**/collector-package')
    with page.expect_download() as event: page.locator('#result-collector').click()
    assert event.value.failure() is None and len(downloads) == 1
    assert len(GeometryJobs(store).get(job['id'])['collector_packages']) == 1


@pytest.mark.parametrize('change', ['key_aba', 'logout', 'catalog_revision'])
def test_context_drift_during_stream_cancels_reader_without_download(collector_ui, change):
    from playwright.sync_api import expect
    page, store, _, tool, job, downloads = collector_ui
    page.evaluate('''()=>{const original=window.fetch;window.fetch=async(...args)=>{
      const response=await original(...args);if(!String(args[0]).endsWith('/collector-package'))return response;
      window.__collectorSignal=args[1].signal;
      const getReader=response.body.getReader.bind(response.body);response.body.getReader=()=>{
        const reader=getReader();let first=true;window.__cancelled=0;window.__released=0;
        return {async read(){const value=await reader.read();if(!first)return value;first=false;
          return new Promise(resolve=>{window.__releaseChunk=()=>resolve(value);});},
          async cancel(){window.__cancelled++;try{await reader.cancel();}catch{}},
          releaseLock(){window.__released++;reader.releaseLock();}};};return response;};}''')
    track(page, 'result-collector'); page.locator('#result-collector').click()
    page.wait_for_function('()=>typeof window.__releaseChunk==="function"')
    if change == 'key_aba':
        page.locator('#result-key').fill('NEWER'); page.locator('#result-key').fill('CUSTOM')
    elif change == 'logout': page.locator('#disconnect').click()
    else:
        payload = {key: value for key, value in tool.items() if key not in ('id', 'revision', 'updated_at')}
        payload['name'] = 'Newer source revision'
        newer = store.save_resource(payload, 'fixture', tool['id'], 1)
        # The fixture's final invariant now expects this explicit synthetic write.
        tool.clear(); tool.update(newer)
        page.locator('#refresh').click(); expect(page.locator('#result-key')).to_have_value('')
    page.evaluate('window.__releaseChunk()'); page.wait_for_function('()=>window.__oldActionFinished')
    assert downloads == [] and page.evaluate('window.__collectorSignal.aborted')
    assert page.evaluate('window.__cancelled') == page.evaluate('window.__released') == 1
    expect(page.locator('#message')).not_to_contain_text('Empfangs-SHA und Größe bestätigt')
    assert len(GeometryJobs(store).get(job['id'])['collector_packages']) == 1

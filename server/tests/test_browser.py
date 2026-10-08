"""Opt-in real Edge GUI workflow. Never uses live settings or user profiles.

NX_BROWSER_TEST=1 python -m pytest tests/test_browser.py -q
Uses the installed Edge, not playwright install msedge (which could replace it).
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from nxserver.libraries import OPTIONAL_PALETTE, SPECS


@pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1', reason='Opt-in isolated browser test.')
def test_gui_normalizes_imports_catalog_and_edits_native_columns(tmp_path):
    playwright = pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect
    source, old, new = (tmp_path / name for name in ('source', 'old', 'new'))
    for root in (source, old, new):
        for identifier in ('tool-metric', 'holder-metric'):
            path = root / SPECS[identifier]['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#CLASS SAMPLE\nFORMAT LIBRF DESCR\nDATA |STD|Siemens\n#END_DATA\n', encoding='utf-8')
    legacy = source / SPECS['tool-metric']['legacy_path']
    legacy.parent.mkdir(parents=True)
    legacy.write_text('#CLASS SAMPLE\nFORMAT LIBRF DESCR\nDATA |HD_TOOL|Henning tool\n#END_DATA\n', encoding='utf-8')
    palette = source / OPTIONAL_PALETTE
    palette.write_text('<Palette></Palette><PaletteEntry/>', encoding='utf-8')
    originals = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'data_dir': str(tmp_path / 'managed'), 'sources': {'test': str(source)},
                                    'standards': {'2406': str(old), '2512': str(new)}, 'logs': {}}), encoding='utf-8')
    token = 'isolated-browser-token-not-real-' + 'z' * 32
    environment = {**os.environ, 'NX_ADMIN_TOKEN': token}
    environment.pop('NX_ERP_TOKEN', None)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, '-m', 'nxserver', '--settings', str(settings), '--port', str(port)],
        cwd=Path(__file__).resolve().parents[1], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + '/health', timeout=1).close()
                break
            except (urllib.error.URLError, TimeoutError):
                if process.poll() is not None:
                    raise AssertionError('Isolated GUI server terminated.')
                time.sleep(0.1)
        else:
            raise AssertionError('Isolated GUI server not ready.')
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                page = browser.new_page(viewport={'width': 1400, 'height': 950})
                failures = []
                page.on('pageerror', lambda error: failures.append(str(error)))
                page.on('dialog', lambda dialog: dialog.accept('GUI Export' if dialog.type == 'prompt' else None))
                page.goto(base)
                page.locator('#token').fill(token)
                page.locator('#connect').click()
                expect(page.locator('#message')).to_contain_text('Verbunden')
                assert page.locator('#kind option').count() == 21
                page.locator('#import-preview').click()
                expect(page.locator('#import-state')).to_contain_text('konfliktfrei')
                page.locator('#import').click()
                expect(page.locator('#message')).to_contain_text('Neuer Ordner erstellt')
                page.locator('#library-inventory').click()
                expect(page.locator('#native-library option')).to_have_count(2)
                page.locator('#native-library').select_option('holder-metric')
                page.locator('#library-read').click()
                expect(page.locator('#native-block option')).to_have_count(1)
                page.locator('#native-new').click()
                expect(page.locator('#native-fields input')).to_have_count(2)
                page.locator('#name').fill('GUI holder')
                page.locator('#native-fields input').nth(0).fill('GUI_HOLDER')
                page.locator('#native-fields input').nth(1).fill('GUI holder description')
                page.locator('#camstatus').select_option('approved')
                page.locator('#save').click()
                expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert')
                page.locator('#catalog-preview').click()
                expect(page.locator('#catalog-state')).to_contain_text('1 neue Objekte, 0 Konflikte')
                page.locator('#catalog-import').click()
                expect(page.locator('#message')).to_contain_text('Stammdaten übernommen')
                expect(page.locator('#resources tr')).to_have_count(2)
                page.locator('#releases').get_by_role('button', name='Neuen Export ableiten').click()
                expect(page.locator('#releases tr')).to_have_count(2)
                page.locator('#resources tr').filter(has_text='Henning tool').get_by_role('button', name='Bearbeiten').click()
                page.locator('#camstatus').select_option('approved')
                page.locator('#save').click()
                expect(page.locator('#message')).to_contain_text('preparation must be validated')
                page.locator('#camstatus').select_option('draft')
                step = b"ISO-10303-21;HEADER;FILE_SCHEMA(('AUTOMOTIVE_DESIGN'));ENDSEC;DATA;#1=MANIFOLD_SOLID_BREP('fixture',$);ENDSEC;END-ISO-10303-21;"
                page.locator('#assetfile').set_input_files({'name': 'browser-fixture.step', 'mimeType': 'application/octet-stream', 'buffer': step})
                page.locator('#upload').click()
                expect(page.locator('#message')).to_contain_text('Datei gespeichert')
                page.locator('#save').click()
                expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert')
                expect(page.locator('#step-asset option')).to_have_count(1)
                expect(page.locator('#step-tool-type option')).to_have_count(10)
                page.locator('#step-preview').click()
                expect(page.locator('#message')).to_contain_text('STEP-Text geprüft')
                page.locator('#step-job-create').click()
                expect(page.locator('#geometry-jobs tr')).to_have_count(1)
                with page.expect_download() as downloaded:
                    page.locator('#geometry-jobs').get_by_role('button', name='Importpaket herunterladen').click()
                download = downloaded.value
                assert download.suggested_filename.startswith('NX_STEP_')
                assert download.failure() is None
                expect(page.locator('#geometry-jobs')).to_contain_text('awaiting_nx')
                assert not failures, failures
                page.screenshot(path=str(tmp_path / 'native-management.png'), full_page=True)
            finally:
                browser.close()
        assert all(p.read_bytes() == content for p, content in originals.items())
        managed = list((tmp_path / 'managed' / 'r').glob('*/nx'))
        assert len(managed) == 2
        assert all(not (p / OPTIONAL_PALETTE).exists() and not (p / SPECS['tool-metric']['legacy_path']).exists() for p in managed)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

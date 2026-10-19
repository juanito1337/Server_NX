"""Opt-in Edge + actual static unpacker, isolated DB; no NX/setup is launched."""
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from nxserver.safety import offline_path


@pytest.mark.skipif(os.name!='nt' or os.environ.get('NX_BROWSER_TEST')!='1' or
    os.environ.get('NX_EXTRACTOR_TEST')!='1',reason='Opt-in isolated Edge and real local static reader.')
def test_gui_extracts_both_real_packages_and_selects_inputs_without_creating_releases(tmp_path):
    playwright=pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect
    configured=json.loads(offline_path(Path(__file__).parents[1]/'settings.example.json').read_text(encoding='utf-8-sig'))
    source=tmp_path/'input';target=tmp_path/'output';source.mkdir();target.mkdir()
    settings={key:configured[key] for key in ('installers','extractor','standards')}
    settings.update(data_dir=str(tmp_path/'managed'),sources={'test':str(source)},logs={},sidts={},
        source_roots={'inputs':str(source)},target_roots={'outputs':str(target)})
    settings_file=tmp_path/'isolated.json';settings_file.write_text(json.dumps(settings),encoding='utf-8')
    before={version:hashlib.sha256(offline_path(item['path']).read_bytes()).hexdigest()
        for version,item in settings['installers'].items()}
    token='isolated-extraction-browser-not-real-'+'t'*40
    environment=dict(os.environ)
    for key in ('NX_ADMIN_TOKEN_FILE','NX_ERP_TOKEN','NX_ERP_TOKEN_FILE'):environment.pop(key,None)
    environment['NX_ADMIN_TOKEN']=token
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-m','nxserver','--settings',str(settings_file),'--port',str(port)],
        cwd=Path(__file__).resolve().parents[1],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    base=f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:urllib.request.urlopen(base+'/health',timeout=1).close();break
            except (urllib.error.URLError,TimeoutError):
                if process.poll() is not None:raise AssertionError('Isolated extraction server terminated.')
                time.sleep(0.1)
        else:raise AssertionError('Isolated extraction server not ready.')
        with playwright.sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':1400,'height':950});failures=[]
                page.on('pageerror',lambda error:failures.append(str(error)))
                page.on('dialog',lambda dialog:dialog.accept())
                page.goto(base);page.locator('#token').fill(token);page.locator('#connect').click()
                expect(page.locator('#message')).to_contain_text('Verbunden')
                page.locator('#tab-update').click()
                expect(page.locator('#extract-version option')).to_have_count(2)
                expect(page.locator('#extract-state')).to_contain_text('Windows-Entpacker geprüft')
                for number,version in enumerate(('2406','2512'),1):
                    page.locator('#extract-version').select_option(version)
                    expect(page.locator('#extract-start')).to_be_enabled()
                    page.locator('#extract-start').click()
                    expect(page.locator('#message')).to_contain_text('NX'+version+' statisch entpackt',timeout=60000)
                    expect(page.locator('#upgrade-jobs tr')).to_have_count(number)
                    choose=page.locator('#upgrade-jobs').get_by_role('button',name='Als SIDT-Eingabe wählen',exact=True)
                    expect(choose).to_have_count(1);choose.click()
                    expect(page.locator('#message')).to_contain_text('NX'+version+': SIDT-Eingabe ausgewählt')
                    expect(page.locator('#upgrade-jobs')).to_contain_text('Als SIDT-Eingabe ausgewählt')
                    expect(page.locator('#upgrade-build')).to_be_disabled()
                page.locator('#upgrade-check').click()
                expect(page.locator('#upgrade-prerequisites')).to_contain_text('SIDT-Deklarationen: NX2406 · NX2512',timeout=15000)
                expect(page.locator('#active')).to_contain_text('Noch kein freigegebener Datenstand lokal aktiviert')
                page.locator('#tab-manage').click()
                expect(page.locator('#releases tr')).to_have_count(0)
                page.locator('#disconnect').click()
                expect(page.locator('#extract-start')).to_be_disabled()
                expect(page.locator('#upgrade-jobs tr')).to_have_count(0)
                assert not failures,failures
            finally:browser.close()
        with sqlite3.connect(tmp_path/'managed/management.sqlite3') as db:
            assert db.execute('SELECT count(*) FROM releases').fetchone()[0]==0
            assert db.execute('SELECT count(*) FROM sidt_inputs').fetchone()[0]==2
            assert db.execute('SELECT count(*) FROM migration_jobs').fetchone()[0]==2
        assert all(hashlib.sha256(offline_path(settings['installers'][version]['path']).read_bytes()).hexdigest()==digest
            for version,digest in before.items())
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

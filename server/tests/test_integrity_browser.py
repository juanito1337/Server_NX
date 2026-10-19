"""Isolated Edge diagnostic workflow, including checkpoint download/upload."""
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

from nxserver.store import Store,TOOL_DATABASE
from test_libraries import write,simple


@pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge workflow.')
def test_integrity_gui_checkpoint_roundtrip_and_tamper_detection(tmp_path):
    playwright=pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect
    source=tmp_path/'source';write(source,TOOL_DATABASE,simple())
    data=tmp_path/'managed';store=Store(data,{'test':source})
    release=store.import_release('test','2512','Integrity fixture','fixture')
    store.save_resource({'kind':'gauge','name':'Gauge','cam':{},'cam_status':'draft','erp':None,'asset_ids':[]},'fixture')
    audit=store.audit_entries();before=store.manifest(Path(release['folder']))
    settings_file=tmp_path/'settings.json';settings_file.write_text(json.dumps({'data_dir':str(data),'sources':{'test':str(source)},'logs':{}}),encoding='utf-8')
    token='isolated-integrity-browser-token-'+'i'*40;environment=dict(os.environ)
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
                if process.poll() is not None:raise AssertionError('Isolated integrity server terminated.')
                time.sleep(0.1)
        else:raise AssertionError('Isolated integrity server not ready.')
        with playwright.sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':1400,'height':950});failures=[]
                page.on('pageerror',lambda error:failures.append(str(error)))
                page.on('dialog',lambda dialog:dialog.accept())
                page.goto(base);page.locator('#token').fill(token);page.locator('#connect').click()
                expect(page.locator('#message')).to_contain_text('Verbunden')
                page.locator('#tab-prepare').click();page.locator('#integrity-open').click()
                expect(page.locator('#panel-manage')).to_be_visible()
                expect(page.locator('#integrity-checkpoint')).to_be_disabled()
                page.locator('#integrity-start').click()
                expect(page.locator('#integrity-state')).to_contain_text('Nachweise konsistent',timeout=15000)
                expect(page.locator('#integrity-trust')).to_contain_text('kein externer Vergleichsanker')
                expect(page.locator('#integrity-issues tr')).to_have_count(0)
                with page.expect_download() as download:page.locator('#integrity-checkpoint').click()
                assert download.value.failure() is None
                downloaded=Path(download.value.path());checkpoint=json.loads(downloaded.read_text(encoding='utf-8'))
                assert checkpoint['files_checked'] and checkpoint['schema_version']==1
                page.locator('#integrity-anchor').set_input_files({'name':'Checkpoint.json','mimeType':'application/json',
                    'buffer':b'\xef\xbb\xbf'+json.dumps(checkpoint).encode()})
                page.locator('#integrity-start').click()
                expect(page.locator('#integrity-trust')).to_contain_text('Externer Vergleichsanker geprüft',timeout=15000)
                # No metadata/file mutation from either diagnostic operation.
                assert store.audit_entries()==audit and store.manifest(Path(release['folder']))==before
                file=Path(release['folder'])/TOOL_DATABASE;file.write_bytes(file.read_bytes()+b'#External change\n')
                page.locator('#integrity-start').click()
                expect(page.locator('#integrity-state')).to_contain_text('Abweichungen gefunden',timeout=15000)
                expect(page.locator('#integrity-issues')).to_contain_text('artifact_integrity')
                expect(page.locator('#integrity-checkpoint')).to_be_disabled()
                page.locator('#integrity-jobs').get_by_role('button',name='Ergebnis ansehen').last.click()
                expect(page.locator('#integrity-state')).to_contain_text('Nachweise konsistent',timeout=15000)
                # Old reports are explicitly timestamped snapshots, not a repair.
                assert file.read_bytes().endswith(b'#External change\n')
                page.evaluate(r"""()=>{
                    const original=window.fetch;
                    window.fetch=(...args)=>original(...args).then(response=>
                        /\/integrity\/[a-f0-9-]+$/.test(String(args[0]))?new Promise(resolve=>{
                            window.releaseStaleIntegrityResponse=()=>resolve(response);
                        }):response);
                }""")
                page.locator('#integrity-jobs').get_by_role('button',name='Ergebnis ansehen').first.click()
                page.wait_for_function("()=>typeof window.releaseStaleIntegrityResponse==='function'")
                page.locator('#disconnect').click();page.evaluate('window.releaseStaleIntegrityResponse()');page.wait_for_timeout(150)
                expect(page.locator('#work')).to_be_hidden();expect(page.locator('#integrity-checkpoint')).to_be_disabled()
                expect(page.locator('#integrity-jobs tr')).to_have_count(0)
                expect(page.locator('#integrity-anchor')).to_have_value('')
                assert not failures,failures
            finally:browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

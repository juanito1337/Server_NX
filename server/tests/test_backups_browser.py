"""Real isolated Edge backup/download/recovery UI; no NX or real server data."""
import hashlib
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

from nxserver.store import Store, TOOL_DATABASE
from test_libraries import simple,write


@pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge workflow.')
def test_backup_gui_download_isolated_recovery_and_logout_with_late_response(tmp_path):
    playwright=pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect
    source=tmp_path/'source';write(source,TOOL_DATABASE,simple())
    data=tmp_path/'managed';store=Store(data,{'test':source})
    release=store.import_release('test','2512','Synthetic GUI release','fixture');before=store.manifest(Path(release['folder']))
    settings=tmp_path/'settings.json';settings.write_text(json.dumps({'data_dir':str(data),'sources':{'test':str(source)}}),encoding='utf-8')
    token='isolated-backup-browser-token-'+'b'*40;environment=dict(os.environ)
    for key in ('NX_ADMIN_TOKEN_FILE','NX_ERP_TOKEN','NX_ERP_TOKEN_FILE'):environment.pop(key,None)
    environment['NX_ADMIN_TOKEN']=token
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-m','nxserver','--settings',str(settings),'--port',str(port)],
        cwd=Path(__file__).resolve().parents[1],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    base=f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:urllib.request.urlopen(base+'/health',timeout=1).close();break
            except (urllib.error.URLError,TimeoutError):
                if process.poll() is not None:raise AssertionError('Isolated backup server terminated.')
                time.sleep(0.1)
        else:raise AssertionError('Isolated backup server not ready.')
        with playwright.sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':1400,'height':950});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)));page.on('dialog',lambda dialog:dialog.accept())
                page.goto(base);page.locator('#token').fill(token);page.locator('#connect').click()
                expect(page.locator('#message')).to_contain_text('Verbunden')
                page.locator('#backup-create').click()
                expect(page.locator('#backup-state')).to_contain_text('Sicherung vollständig erstellt',timeout=20000)
                expect(page.locator('#backup-snapshots tr')).to_have_count(1)
                row=page.locator('#backup-snapshots tr')
                with page.expect_download() as result:row.get_by_role('button',name='Prüfsummenbeleg herunterladen').click()
                receipt=json.loads(Path(result.value.path()).read_text(encoding='utf-8'))
                assert not receipt['signed']
                # Explicit small-download fallback: no native filesystem dialog
                # during headless tests. Large downloads require streaming API.
                page.evaluate('window.showSaveFilePicker=undefined')
                with page.expect_download() as result:row.get_by_role('button',name='Archiv herunterladen').click()
                assert hashlib.sha256(Path(result.value.path()).read_bytes()).hexdigest()==receipt['archive_sha256']
                page.locator('#recovery-backup').select_option(receipt['id'])
                page.locator('#recovery-name').fill('GUI_R')
                page.locator('#recovery-preview').click()
                expect(page.locator('#recovery-state')).to_contain_text('Geprüftes neues Ziel',timeout=20000)
                expect(page.locator('#recovery-create')).to_be_disabled()
                page.locator('#recovery-unsigned').check();expect(page.locator('#recovery-create')).to_be_enabled()
                page.locator('#recovery-name').fill('GUI_Changed');expect(page.locator('#recovery-create')).to_be_disabled()
                page.locator('#recovery-name').fill('GUI_R');page.locator('#recovery-preview').click()
                expect(page.locator('#recovery-create')).to_be_enabled(timeout=20000)
                page.locator('#recovery-create').click()
                expect(page.locator('#recovery-state')).to_contain_text('Isolierter Bestand erstellt',timeout=20000)
                restored=Store(tmp_path/'Recovered/GUI_R')
                assert restored.manifest(restored.release_folder(release['id']))==before
                assert restored.active()['release_id'] is None and store.manifest(Path(release['folder']))==before
                assert len(store.releases())==1
                # Late list responses after logout must not restore controls.
                page.evaluate(r"""()=>{const original=window.fetch;window.fetch=(...args)=>original(...args).then(response=>
                    String(args[0]).endsWith('/backups')?new Promise(resolve=>{window.releaseLateBackup=()=>resolve(response);}):response);} """)
                page.locator('#backup-refresh').click();page.wait_for_function("()=>typeof window.releaseLateBackup==='function'")
                page.locator('#disconnect').click();page.evaluate('window.releaseLateBackup()');page.wait_for_timeout(150)
                expect(page.locator('#work')).to_be_hidden();expect(page.locator('#recovery-create')).to_be_disabled()
                expect(page.locator('#backup-snapshots tr')).to_have_count(0)
                expect(page.locator('#backup-jobs tr')).to_have_count(0)
                assert not errors,errors
            finally:browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

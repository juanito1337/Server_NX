"""Opt-in isolated Edge reference decisions, bulk confirmation and safe imports."""
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

from nxserver.libraries import NativeTable, SPECS
from nxserver.store import Store
from test_references import reference_data
from test_upgrades import installation


@pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge workflow.')
def test_reference_gui_explicit_bulk_mapping_prepared_import_and_upgrade(tmp_path):
    playwright=pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect
    source,old,new=(tmp_path/name for name in ('source','old','new'))
    sidt_old,sidt_new=tmp_path/'sidt-old',tmp_path/'sidt-new'
    installation(old,sidt_old,'2406');installation(new,sidt_new,'2512')
    output=tmp_path/'output';output.mkdir()
    data=tmp_path/'managed'
    store=Store(data,{'test':source},standards={'2406':old,'2512':new},
                target_roots={'output':output},sidts={'2406':sidt_old,'2512':sidt_new})
    reference_data(store,source)
    # Real native keys must remain data, even if they name JS prototype members.
    file=source/SPECS['tool-metric']['path']
    file.write_bytes(file.read_bytes().replace(b'KEY1',b'__proto__'))
    before=store.manifest(source)
    settings_file=tmp_path/'settings.json'
    settings_file.write_text(json.dumps({'data_dir':str(data),'sources':{'test':str(source)},
        'standards':{'2406':str(old),'2512':str(new)},'target_roots':{'output':str(output)},
        'sidts':{'2406':str(sidt_old),'2512':str(sidt_new)}}),encoding='utf-8')
    token='isolated-reference-browser-token-'+'r'*40;environment=dict(os.environ)
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
                if process.poll() is not None:raise AssertionError('Isolated reference server terminated.')
                time.sleep(0.1)
        else:raise AssertionError('Isolated reference server not ready.')
        with playwright.sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':1400,'height':950});failures=[]
                page.on('pageerror',lambda error:failures.append(str(error)))
                page.on('dialog',lambda dialog:dialog.accept())
                page.goto(base);page.locator('#token').fill(token);page.locator('#connect').click()
                expect(page.locator('#message')).to_contain_text('Verbunden')
                page.locator('#tab-update').click();page.locator('#upgrade-mode').select_option('prepared')
                page.locator('#target').select_option('output');page.locator('#target-path').fill(str(output/'prepared'))
                page.locator('#import-preview').click()
                expect(page.locator('#conflicts tr')).to_have_count(2)
                expect(page.locator('#import')).to_be_disabled()
                row=page.locator('#conflicts tr').first
                expect(row.locator('input[list]')).to_have_value('')
                expect(row.get_by_role('button',name='Zuordnung bestätigen')).to_be_disabled()
                row.locator('input[list]').fill('TMC0_000055')
                row.get_by_role('checkbox').check()
                row.get_by_role('button',name='Zuordnung bestätigen').click()
                decision=json.loads(page.locator('#reference-resolutions').input_value())
                assert set(decision['tool-metric'])=={'__proto__','KEY2'}
                assert page.evaluate("()=>!Object.hasOwn(Object.prototype,'MATREF')")
                assert store.manifest(source)==before and store.releases()==[]
                page.locator('#import-preview').click()
                expect(page.locator('#import')).to_be_enabled()
                expect(page.locator('#reference-applied li')).to_have_count(2)
                # Undo really invalidates the preview; rechecking exposes the
                # missing reference again rather than silently reusing old data.
                page.locator('#reference-applied').get_by_role('button',name='Zuordnung entfernen').first.click()
                expect(page.locator('#import')).to_be_disabled()
                page.locator('#import-preview').click()
                expect(page.locator('#conflicts tr')).to_have_count(1)
                page.locator('#conflicts input[list]').fill('TMC0_000055')
                page.locator('#conflicts').get_by_role('button',name='Zuordnung bestätigen').click()
                page.locator('#import-preview').click();expect(page.locator('#import')).to_be_enabled()
                page.locator('#import').click()
                expect(page.locator('#message')).to_contain_text('Neuer Ordner erstellt',timeout=15000)
                assert len(store.releases())==1 and store.active()['generation']==0
                assert NativeTable.read(output/'prepared'/SPECS['tool-metric']['path']).groups()['__proto__'][0]['values']['MATREF']=='TMC0_000055'
                # Same control also participates in the asynchronous full wizard.
                page.locator('#tab-update').click();page.locator('#upgrade-mode').select_option('upgrade')
                page.get_by_text('Bestätigte Verweiszuordnungen (erweitert)',exact=True).click()
                page.locator('#reference-resolutions').fill('{}')
                page.locator('#target-path').fill(str(output/'upgrade'))
                page.locator('#upgrade-preview').click()
                expect(page.locator('#upgrade-file-conflicts tr')).to_have_count(2,timeout=15000)
                row=page.locator('#upgrade-file-conflicts tr').first
                row.locator('input[list]').fill('TMC0_000055');row.get_by_role('checkbox').check()
                row.get_by_role('button',name='Zuordnung bestätigen').click()
                expect(page.locator('#upgrade-build')).to_be_disabled()
                page.locator('#upgrade-preview').click()
                expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
                expect(page.locator('#reference-applied li')).to_have_count(2)
                page.locator('#upgrade-build').click()
                expect(page.locator('#message')).to_contain_text('Versionswechsel als neuer NX-Datenstand erstellt',timeout=15000)
                assert len(store.releases())==2 and store.active()['generation']==0
                assert store.manifest(source)==before
                assert NativeTable.read(output/'upgrade'/SPECS['tool-metric']['path']).groups()['KEY2'][0]['values']['MATREF']=='TMC0_000055'
                page.locator('#disconnect').click()
                expect(page.locator('#work')).to_be_hidden()
                expect(page.locator('#reference-resolutions')).to_have_value('{}')
                expect(page.locator('#reference-applied li')).to_have_count(0)
                assert not failures,failures
            finally:browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

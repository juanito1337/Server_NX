"""Corrupt selected input, explicit reselection and upgrade on isolated Edge only."""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from nxserver.libraries import SPECS
from nxserver.store import canonical
from nxserver.upgrades import LIVE
from test_selected_extraction_reports import selected,replace_report
from test_upgrades import installation
from test_libraries import write,simple
from browser_test_support import wait_for_test_server

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated selected-SIDT report recovery.')


@pytest.fixture
def selected_ui(selected,tmp_path):
    from playwright.sync_api import sync_playwright,expect
    store,manager,job,report,path,calls=selected
    old,new,source,output=[tmp_path/name for name in ('old','new','source','out')]
    so,sn=tmp_path/'so',tmp_path/'sn';installation(old,so,'2406');installation(new,sn,'2512')
    relative=SPECS['holder-metric']['path']
    write(old,relative,simple(descr='Old fixture'));write(new,relative,simple(descr='New fixture'))
    write(source,relative,simple(descr='Old fixture'));output.mkdir()
    settings={'data_dir':str(store.root),'sources':{'test':str(source)},'standards':{'2406':str(old),'2512':str(new)},
        'sidts':{'2406':str(so),'2512':str(sn)},'target_roots':{'out':str(output)}}
    config=tmp_path/'browser.json';config.write_text(json.dumps(settings),encoding='utf-8')
    environment=dict(os.environ);token='isolated-selected-sidt-'+'p'*40
    for key in ('NX_ADMIN_TOKEN_FILE','NX_ERP_TOKEN','NX_ERP_TOKEN_FILE'):environment.pop(key,None)
    environment['NX_ADMIN_TOKEN']=token
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-m','nxserver','--settings',str(config),'--port',str(port)],
        cwd=Path(__file__).parents[1],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        wait_for_test_server(process,port)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':1400,'height':1000});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)));page.on('dialog',lambda dialog:dialog.accept())
                page.goto('http://127.0.0.1:'+str(port));page.locator('#token').fill(token);page.locator('#connect').click()
                expect(page.locator('#message')).to_contain_text('Verbunden');page.locator('#tab-update').click()
                page.locator('#source').select_option('test');page.locator('#source-path').fill(str(source))
                page.locator('#target').select_option('out');page.locator('#target-path').fill(str(output/'nx'))
                yield page,store,manager,job,report,path,calls,source,output/'nx'
                assert not errors,errors
            finally:browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


@pytest.mark.parametrize('fault',['duplicate','incomplete'])
def test_bad_selected_report_disarms_old_plan_and_explicit_intact_reselection_recovers(selected_ui,fault):
    from playwright.sync_api import expect
    page,store,manager,job,report,path,calls,source,target=selected_ui;before=store.manifest(source)
    page.locator('#upgrade-check').click()
    expect(page.locator('#upgrade-prerequisites')).to_contain_text('SIDT-Deklarationen: NX2406 · NX2512')
    page.locator('#upgrade-preview').click();expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    raw=(b'{"version":"2406",'+canonical(report).encode()[1:]) if fault=='duplicate' else b'{}'
    replace_report(manager,job,path,raw)
    page.locator('#upgrade-check').click()
    expect(page.locator('#upgrade-prerequisites')).to_contain_text('not a unique' if fault=='duplicate' else 'metadata is incomplete')
    expect(page.locator('#upgrade-build')).to_be_disabled()
    assert not target.exists() and not store.releases() and store.manifest(source)==before
    # No silent fallback to the intact, administrator-configured sn directory.
    replacement=manager.create_extraction('2512','fixture');LIVE[replacement['id']].result(timeout=15)
    assert manager.get(replacement['id'])['status']=='completed'
    page.locator('#upgrade-jobs-refresh').click()
    row=page.locator('#upgrade-jobs tr').filter(has_text=replacement['id'][:8])
    row.get_by_role('button',name='Als SIDT-Eingabe wählen',exact=True).click()
    expect(page.locator('#message')).to_contain_text('NX2512: SIDT-Eingabe ausgewählt')
    page.locator('#upgrade-check').click()
    expect(page.locator('#upgrade-prerequisites')).to_contain_text('SIDT-Deklarationen: NX2406 · NX2512')
    page.locator('#upgrade-preview').click();expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    page.locator('#upgrade-build').click()
    expect(page.locator('#message')).to_contain_text('Versionswechsel als neuer NX-Datenstand erstellt',timeout=15000)
    assert target.is_dir() and len(store.releases())==1 and store.releases()[0]['test_launcher']
    assert path.read_bytes()==raw and store.manifest(source)==before and store.active()['release_id'] is None

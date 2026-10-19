"""Selected SIDT package comparison through isolated GUI, no original CAD/NX."""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from browser_test_support import wait_for_test_server
from test_filepackage_commit import PATHS
from test_selected_sidt_packages import selected_packages

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated selected-SIDT package GUI.')


@pytest.fixture
def selected_package_ui(selected_packages,tmp_path):
    from playwright.sync_api import sync_playwright,expect
    value=selected_packages;store=value['store'];token='isolated-selected-package-'+'q'*40
    settings=tmp_path/'browser.json';settings.write_text(json.dumps({'data_dir':str(store.root),
        'sources':{'test':str(value['source'])},'standards':{'2512':str(value['standard'])}}),encoding='utf-8')
    environment=dict(os.environ)
    for key in ('NX_ADMIN_TOKEN_FILE','NX_ERP_TOKEN','NX_ERP_TOKEN_FILE'):environment.pop(key,None)
    environment['NX_ADMIN_TOKEN']=token
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-m','nxserver','--settings',str(settings),'--port',str(port)],
        cwd=Path(__file__).parents[1],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        wait_for_test_server(process,port)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':1400,'height':1000});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)));page.on('dialog',lambda dialog:dialog.accept())
                page.goto('http://127.0.0.1:'+str(port));page.locator('#token').fill(token);page.locator('#connect').click()
                expect(page.locator('#message')).to_contain_text('Verbunden')
                yield page,value
                assert not errors,errors
            finally:browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


@pytest.mark.parametrize('kind',['template','ui_standard'])
def test_selected_overlay_blocks_stock_then_customer_draft_can_be_qualified_and_exported(selected_package_ui,kind):
    from playwright.sync_api import expect
    page,value=selected_package_ui;store=value['store'];relative=PATHS[kind];stock=store.manifest(value['sidt'])
    def choose(release):
        page.locator('#file-adopt-release').select_option(release['id']);page.locator('#file-adopt-kind').select_option(kind)
        page.locator('#file-adopt-load').click();page.locator('#file-adopt-files input').check()
        page.locator('#file-adopt-preview').click()
    choose(value['release'])
    expect(page.locator('#file-adopt-state')).to_contain_text('Unchanged Siemens/SIDT standard')
    expect(page.locator('#file-adopt-apply')).to_be_disabled()
    assert store.resources()==[] and not store.asset_inventory()['items']
    content=b'<Customer fixture="GUI"/>' if kind=='ui_standard' else b'Own synthetic customer part, not CAD'
    (value['source']/relative).write_bytes(content)
    release=store.import_release('test','2512','Customer GUI difference','fixture');before=store.manifest(store.release_folder(release['id']))
    page.locator('#refresh').click();expect(page.locator('#releases')).to_contain_text(release['id']);choose(release)
    expect(page.locator('#file-adopt-apply')).to_be_enabled();page.locator('#file-adopt-apply').click()
    expect(page.locator('#panel-prepare')).to_be_visible();expect(page.locator('#package-enabled')).to_be_checked()
    resource=store.resources()[0];assert resource['cam_status']=='draft'
    assert str(value['sidt']) in resource['file_package']['provenance']['baseline_inputs'][relative]['baseline_path']
    page.locator('#package-evidence').fill('Synthetic GUI qualification only; not an actual NX test.')
    page.locator('#package-bind').click();expect(page.locator('#package-state')).to_contain_text('Dateihashes gebunden')
    for check in ('paths','dependencies','content','nx_test'):page.locator('#package-'+check).check()
    page.locator('#camstatus').select_option('approved');page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    page.locator('#tab-manage').click()
    page.locator('#releases tr').filter(has_text=release['id']).get_by_role('button',name='Neuen Export ableiten',exact=True).click()
    page.locator('#export-preview').click();expect(page.locator('#export-create')).to_be_enabled()
    page.locator('#export-create').click()
    expect(page.locator('#message')).to_contain_text('Neuer NX-Datenstand aus geprüfter Exportvorschau',timeout=15000)
    exported=next(item for item in store.releases() if item['parent_id']==release['id'])
    assert (store.release_folder(exported['id'])/relative).read_bytes()==content and exported['test_launcher']
    assert store.manifest(store.release_folder(release['id']))==before and store.manifest(value['sidt'])==stock
    assert store.active()['release_id'] is None

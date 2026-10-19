"""Isolated Edge prepared-result workflow. Synthetic bytes, no actual NX proof."""
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

from nxserver.geometry import GeometryJobs
from nxserver.libraries import SPECS
from nxserver.store import Store
from test_geometry import STEP
from test_results import bundle,table
from test_libraries import write


@pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge workflow.')
def test_prepared_result_gui_collector_upload_preview_apply_and_editor_bindings(tmp_path):
    playwright=pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect
    source=tmp_path/'source';write(source,SPECS['tool-metric']['path'],table(['LIBRF','DESCR','HLDREF'],[('STD','Stock','')]))
    write(source,SPECS['holder-metric']['path'],table(['LIBRF','DESCR'],[('STDH','Stock')]))
    data=tmp_path/'managed';store=Store(data,{'test':source});release=store.import_release('test','2512','Target','fixture')
    asset=store.add_asset('input.step',STEP,'fixture');resource=store.save_resource({'kind':'tool','name':'Prepared source',
        'cam':{},'cam_status':'draft','asset_ids':[asset['id']],'erp':None,'origin':None,'geometry':{'mode':'step','status':'needs_preparation'}},'fixture')
    job=GeometryJobs(store).create(resource['id'],1,asset['id'],'drilling_solid','fixture');packet,_=bundle(job)
    before=store.manifest(store.release_folder(release['id']))
    settings=tmp_path/'settings.json';settings.write_text(json.dumps({'data_dir':str(data),'sources':{'test':str(source)}}),encoding='utf-8')
    token='isolated-results-browser-token-'+'r'*40;environment=dict(os.environ)
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
                if process.poll() is not None:raise AssertionError('Isolated result server terminated.')
                time.sleep(0.1)
        else:raise AssertionError('Isolated result server not ready.')
        with playwright.sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':1400,'height':950});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)));page.on('dialog',lambda dialog:dialog.accept())
                page.goto(base);page.locator('#token').fill(token);page.locator('#connect').click()
                expect(page.locator('#message')).to_contain_text('Verbunden')
                page.locator('#tab-prepare').click();page.locator('#step-resource').select_option(resource['id'])
                page.locator('#geometry-jobs').get_by_role('button',name='Ergebnispaket').click()
                page.locator('#result-key').fill('CUSTOM')
                with page.expect_download() as download:page.locator('#result-collector').click()
                assert download.value.failure() is None
                page.locator('#result-file').set_input_files({'name':'prepared-result.zip','mimeType':'application/zip','buffer':packet})
                page.locator('#result-upload').click()
                expect(page.locator('#result-state')).to_contain_text('Paket SHA-geprüft gespeichert',timeout=15000)
                page.locator('#result-target').select_option(release['id']);page.locator('#result-preview').click()
                expect(page.locator('#result-apply')).to_be_enabled(timeout=15000)
                expect(page.locator('#result-actions')).to_contain_text('Zugehörigen Entwurf anlegen')
                assert store.resources()==[resource]
                page.locator('#result-apply').click()
                expect(page.locator('#message')).to_contain_text('neue/aktualisierte Entwürfe gespeichert',timeout=15000)
                assert len(store.resources())==2 and store.manifest(store.release_folder(release['id']))==before
                parent=next(r for r in store.resources() if r['id']==resource['id'])
                assert parent['revision']==2 and parent['cam_status']=='draft'
                page.locator('#tab-manage').click()
                page.locator('#resources tr').filter(has_text='Prepared source').get_by_role('button',name='Bearbeiten').click()
                expect(page.locator('#model-package-info')).to_contain_text('Gebundenes Ergebnis')
                assert not page.locator('#geometry-units').is_checked()
                # A normal name edit preserves the package paths and provenance.
                page.locator('#name').fill('Renamed after prepared import');page.locator('#save').click()
                expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert',timeout=15000)
                saved=next(r for r in store.resources() if r['id']==resource['id'])
                assert saved['revision']==3 and saved['model_package']==parent['model_package'] and saved['file_bindings']==parent['file_bindings']
                page.locator('#tab-manage').click()
                page.locator('#resources tr').filter(has_text='Renamed after prepared import').get_by_role('button',name='Bearbeiten').click()
                page.locator('#model-package-detach').click();expect(page.locator('#model-package-detach')).to_be_hidden()
                page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert',timeout=15000)
                assert next(r for r in store.resources() if r['id']==resource['id'])['model_package'] is None
                page.locator('#disconnect').click();expect(page.locator('#work')).to_be_hidden()
                expect(page.locator('#result-apply')).to_be_disabled();expect(page.locator('#result-select option')).to_have_count(0)
                assert not errors,errors
            finally:browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)

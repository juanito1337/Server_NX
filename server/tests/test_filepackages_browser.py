"""Isolated file-package GUI proof; synthetic files/checks, never real NX."""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from test_filepackages import packages,body
from test_management import ADMIN
from test_requests_browser import hold,release as release_reply,track
from browser_test_support import wait_for_test_server

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge file packages.')


@pytest.mark.parametrize('kind,path,content',[
    ('machine','MACH/resource/library/machine/installed_machines/MACHINE.xml',b'<Machine name="synthetic"/>'),
    ('fixture','MACH/resource/library/fixture_automation/graphics/FIXTURE.prt',b'Synthetic PRT; no actual CAD qualification')])
def test_file_only_machine_fixture_qualify_and_export_through_gui(package_ui,kind,path,content):
    from playwright.sync_api import expect
    page,store,target,source=package_ui;value=body(store,kind,path,status='draft',filename=path.rsplit('/',1)[-1],content=content)
    value['file_package'].update(checks={},asset_hashes={},evidence='')
    resource=store.save_resource(value,'fixture');before=store.manifest(store.release_folder(target['id']))
    page.locator('#refresh').click();expect(page.locator('#resources tr')).to_have_count(1)
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten',exact=True).click()
    expect(page.locator('#cam')).to_have_value('{}');expect(page.locator('#kind')).to_have_value(kind)
    page.locator('#package-evidence').fill('Synthetic file-package confirmations, not actual NX/geometry evidence.')
    page.locator('#package-bind').click();expect(page.locator('#package-state')).to_contain_text('Dateihashes gebunden')
    for check in ('paths','dependencies','content','nx_test'):page.locator('#package-'+check).check()
    page.locator('#camstatus').select_option('approved');page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    assert store.resources()[0]['revision']==2 and store.resources()[0]['cam_status']=='approved' and store.resources()[0]['cam']=={}
    page.locator('#tab-manage').click();page.locator('#releases tr').filter(has_text=target['id']).get_by_role('button',name='Neuen Export ableiten',exact=True).click()
    page.locator('#export-preview').click();expect(page.locator('#export-create')).to_be_enabled()
    page.locator('#export-create').click();expect(page.locator('#message')).to_contain_text('Neuer NX-Datenstand aus geprüfter Exportvorschau',timeout=15000)
    exported=next(item for item in store.releases() if item['parent_id']==target['id'])
    assert (store.release_folder(exported['id'])/path).read_bytes()==content and resource['id'] in exported['catalog']['exported_file_packages']
    assert store.manifest(store.release_folder(target['id']))==before and store.active()['release_id'] is None


@pytest.fixture
def package_ui(packages,tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright,expect
    client,store,initial,source,standard=packages
    file=source/'UGII/templates/CUSTOM.prt';file.parent.mkdir(parents=True);file.write_bytes(b'Synthetic customer PRT')
    role=source/'UGII/menus/roles/FIRM.mtx';role.parent.mkdir(parents=True);role.write_bytes(b'<NX_PROFILES/>')
    target=store.import_release('test','2512','Customer files','fixture')
    settings=tmp_path/'browser-settings.json';settings.write_text(json.dumps({'data_dir':str(store.root),
        'sources':{'test':str(source)},'standards':{'2512':str(standard)}}),encoding='utf-8')
    environment=dict(os.environ)
    for key in ('NX_ADMIN_TOKEN_FILE','NX_ERP_TOKEN','NX_ERP_TOKEN_FILE'):environment.pop(key,None)
    environment['NX_ADMIN_TOKEN']=ADMIN
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-m','nxserver','--settings',str(settings),'--port',str(port)],
        cwd=Path(__file__).resolve().parents[1],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    base=f'http://127.0.0.1:{port}'
    try:
        wait_for_test_server(process, port)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':1400,'height':950});errors=[];script_failures=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('response',lambda response:script_failures.append({'script':response.url.rsplit('/',1)[-1],'status':response.status}) if response.request.resource_type=='script' and response.status!=200 else None)
                page.on('requestfailed',lambda request:script_failures.append({'script':request.url.rsplit('/',1)[-1],'status':'network_failure'}) if request.resource_type=='script' else None)
                page.on('dialog',lambda dialog:dialog.accept())
                page.goto(base)
                page.wait_for_function('()=>globalThis.NXClientBoot?.status().ready||globalThis.NXClientBoot?.status().failed')
                boot=page.evaluate('NXClientBoot.status()')
                assert boot['ready'] and not boot['failed'],{'boot':boot,'script_failures':script_failures,'pageerror_count':len(errors)}
                page.locator('#token').fill(ADMIN);page.locator('#connect').click()
                expect(page.locator('#message')).to_contain_text('Verbunden')
                yield page,store,target,source
                assert not errors,errors
            finally:browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


@pytest.mark.parametrize('kind,path',[('template','UGII/templates/CUSTOM.prt'),('ui_standard','UGII/menus/roles/FIRM.mtx')])
def test_existing_package_adopt_qualify_export_and_retire_preserves_parent(package_ui,kind,path):
    from playwright.sync_api import expect
    page,store,target,source=package_ui;original=(source/path).read_bytes()
    page.locator('#file-adopt-release').select_option(target['id']);page.locator('#file-adopt-kind').select_option(kind)
    page.locator('#file-adopt-load').click();page.locator('#file-adopt-files input').check()
    page.locator('#file-adopt-preview').click();expect(page.locator('#file-adopt-apply')).to_be_enabled()
    page.locator('#file-adopt-apply').click();expect(page.locator('#panel-prepare')).to_be_visible()
    expect(page.locator('#kind')).to_have_value(kind);expect(page.locator('#package-enabled')).to_be_checked()
    assert len(store.resources())==1 and store.resources()[0]['cam_status']=='draft'
    page.locator('#package-evidence').fill('Synthetic UI proof only; no actual NX qualification')
    page.locator('#package-bind').click();expect(page.locator('#package-state')).to_contain_text('Dateihashes gebunden')
    for check in ('paths','dependencies','content','nx_test'):page.locator('#package-'+check).check()
    page.locator('#camstatus').select_option('approved');page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    assert store.resources()[0]['revision']==2
    def derive(parent):
        page.locator('#tab-manage').click()
        page.locator('#releases tr').filter(has_text=parent['id']).get_by_role('button',name='Neuen Export ableiten').click()
        page.locator('#export-preview').click();expect(page.locator('#export-create')).to_be_enabled()
        expect(page.locator('#export-actions')).to_contain_text('NX-Dateipaket')
        page.locator('#export-create').click();expect(page.locator('#message')).to_contain_text('Neuer NX-Datenstand aus geprüfter Exportvorschau',timeout=15000)
        return next(item for item in store.releases() if item['parent_id']==parent['id'])
    first=derive(target);assert (store.release_folder(first['id'])/path).read_bytes()==original
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click()
    page.locator('#camstatus').select_option('retired');page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    second=derive(first)
    assert not (store.release_folder(second['id'])/path).exists()
    assert (store.release_folder(first['id'])/path).read_bytes()==original and (source/path).read_bytes()==original
    assert store.active()['release_id'] is None


def test_pending_hash_reply_cannot_requalify_changed_package_version(package_ui):
    from playwright.sync_api import expect
    page,store,target,source=package_ui;resource=store.save_resource(body(store,status='draft'),'fixture')
    page.locator('#refresh').click();expect(page.locator('#resources tr')).to_have_count(1)
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click()
    hold(page,'/api/v1/assets/'+resource['asset_ids'][0]+'/metadata');track(page,'package-bind')
    page.locator('#package-bind').click();page.wait_for_function('()=>window.__held')
    page.locator('#package-version').fill('2606');release_reply(page)
    assert page.evaluate('NXFilePackages.fields().file_package.asset_hashes')=={}
    expect(page.locator('#package-state')).not_to_contain_text('Dateihashes gebunden')
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    assert store.resources()[0]['file_package']['nx_version']=='2606'


@pytest.mark.parametrize('old_error',[False,True])
def test_changed_adoption_kind_discards_old_preview_and_error(package_ui,old_error):
    from playwright.sync_api import expect
    page,store,target,source=package_ui
    page.locator('#file-adopt-release').select_option(target['id']);page.locator('#file-adopt-load').click()
    page.locator('#file-adopt-files input').check()
    hold(page,'/api/v1/releases/'+target['id']+'/file-packages/preview',old_error);track(page,'file-adopt-preview')
    page.locator('#file-adopt-preview').click();page.wait_for_function('()=>window.__held')
    page.locator('#file-adopt-kind').select_option('ui_standard');release_reply(page)
    expect(page.locator('#file-adopt-apply')).to_be_disabled()
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    assert store.resources()==[]


def large_share(page,store,source,count=505):
    from playwright.sync_api import expect
    for index in range(count):(source/f'UGII/templates/A_{index:04}.prt').write_bytes(b'Synthetic paged PRT')
    value=store.import_release('test','2512','Large paged share','fixture')
    page.locator('#refresh').click();expect(page.locator('#releases')).to_contain_text(value['id'])
    page.locator('#file-adopt-release').select_option(value['id']);page.locator('#file-adopt-load').click()
    expect(page.locator('#file-adopt-file-status')).to_contain_text('1–100')
    return value


def check_file(page,name):page.locator('#file-adopt-files label').filter(has_text=name).locator('input').check()


def test_package_search_and_page_selection_reach_files_beyond_500(package_ui):
    from playwright.sync_api import expect
    page,store,target,source=package_ui;large_share(page,store,source)
    check_file(page,'A_0000.prt');page.locator('#file-adopt-next').click()
    expect(page.locator('#file-adopt-file-status')).to_contain_text('101–200');check_file(page,'A_0100.prt')
    page.locator('#file-adopt-query').fill('a_0504');page.locator('#file-adopt-query').press('Enter')
    expect(page.locator('#file-adopt-files input')).to_have_count(1);check_file(page,'A_0504.prt')
    expect(page.locator('#file-adopt-count')).to_contain_text('3 von maximal 100')
    page.locator('#file-adopt-preview').click();expect(page.locator('#file-adopt-apply')).to_be_enabled()
    report=json.loads(page.locator('#details').text_content())
    assert {item['path'] for item in report['entries']}=={f'UGII/templates/A_{index:04}.prt' for index in (0,100,504)}
    page.locator('#file-adopt-apply').click();expect(page.locator('#panel-prepare')).to_be_visible()
    assert len(store.resources())==1 and len(store.resources()[0]['file_package']['entries'])==3
    assert store.resources()[0]['cam_status']=='draft' and store.active()['release_id'] is None


def test_package_maximum_counts_hidden_selections_too(package_ui):
    from playwright.sync_api import expect
    page,store,target,source=package_ui;large_share(page,store,source,101)
    # Actual handlers, not fabricated selected state.
    page.locator('#file-adopt-files input').evaluate_all('(inputs)=>inputs.forEach(input=>{input.checked=true;input.dispatchEvent(new Event("change"));})')
    expect(page.locator('#file-adopt-count')).to_contain_text('100 von maximal 100')
    page.locator('#file-adopt-next').click();expect(page.locator('#file-adopt-file-status')).to_contain_text('101–102')
    page.locator('#file-adopt-files label').filter(has_text='A_0100.prt').locator('input').click()
    expect(page.locator('#file-adopt-files label').filter(has_text='A_0100.prt').locator('input')).not_to_be_checked()
    expect(page.locator('#message')).to_contain_text('Maximal 100 Paketdateien')
    expect(page.locator('#file-adopt-count')).to_contain_text('100 von maximal 100')
    page.locator('#file-adopt-query').fill('A_0100');page.locator('#file-adopt-load').click()
    expect(page.locator('#file-adopt-files input')).to_have_count(1)
    page.locator('#file-adopt-clear').click();expect(page.locator('#file-adopt-count')).to_contain_text('0 von maximal 100')
    expect(page.locator('#file-adopt-query')).to_have_value('');assert store.resources()==[]


@pytest.mark.parametrize('old_error',[False,True])
def test_old_inventory_query_reply_cannot_replace_latest_page(package_ui,old_error):
    from playwright.sync_api import expect
    page,store,target,source=package_ui
    page.locator('#file-adopt-release').select_option(target['id']);page.locator('#file-adopt-load').click()
    expect(page.locator('#file-adopt-files input')).to_have_count(1);check_file(page,'CUSTOM.prt')
    suffix='/file-packages?kind=template&query=CUSTOM&offset=0&limit=100'
    page.locator('#file-adopt-query').fill('CUSTOM');hold(page,suffix,old_error);track(page,'file-adopt-load')
    page.locator('#file-adopt-load').click();page.wait_for_function('()=>window.__held')
    page.locator('#file-adopt-query').fill('NO_MATCH');page.locator('#file-adopt-load').click()
    expect(page.locator('#file-adopt-file-status')).to_contain_text('0 Treffer');release_reply(page)
    expect(page.locator('#file-adopt-files input')).to_have_count(0)
    expect(page.locator('#file-adopt-count')).to_contain_text('1 von maximal 100')
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    assert store.resources()==[]


def test_owner_change_blocks_paged_selection_until_explicit_reload(package_ui):
    from playwright.sync_api import expect
    page,store,target,source=package_ui
    page.locator('#file-adopt-release').select_option(target['id']);page.locator('#file-adopt-load').click()
    expect(page.locator('#file-adopt-files input')).to_have_count(1);check_file(page,'CUSTOM.prt')
    resource=store.save_resource(body(store,status='draft'),'fixture')
    page.locator('#file-adopt-load').click();expect(page.locator('#message')).to_contain_text('Dateimanifest oder Dateibesitz geändert')
    page.locator('#file-adopt-preview').click();expect(page.locator('#file-adopt-apply')).to_be_disabled()
    page.locator('#file-adopt-clear').click();expect(page.locator('#file-adopt-count')).to_contain_text('0 von maximal 100')
    expect(page.locator('#file-adopt-files')).to_contain_text('verwaltet: '+resource['name'])
    expect(page.locator('#file-adopt-files input')).to_be_disabled()
    assert len(store.resources())==1


def test_native_pages_and_grouped_example_preserve_all_sections(package_ui):
    from playwright.sync_api import expect
    from test_management import TOOL_DATABASE
    page,store,target,source=package_ui
    data='#CLASS GROUPED\nFORMAT LIBRF RTYPE DESCR\n'+''.join(
        f'DATA |GROUP_{index:04}|1|Root\nDATA |GROUP_{index:04}|2|Child\n' for index in range(205))+'#END_DATA\n'
    (source/TOOL_DATABASE).write_text(data,encoding='utf-8');large=store.import_release('test','2512','Grouped native share','fixture')
    page.locator('#refresh').click();expect(page.locator('#releases')).to_contain_text(large['id'])
    page.locator('#library-release').select_option(large['id']);page.locator('#library-inventory').click()
    expect(page.locator('#native-library option')).to_have_count(1);page.locator('#library-read').click()
    expect(page.locator('#library-result-state')).to_contain_text('1–100 von 205')
    page.locator('#library-next').click();expect(page.locator('#library-result-state')).to_contain_text('101–200')
    page.locator('#library-next').click();expect(page.locator('#native-objects tr')).to_have_count(5)
    expect(page.locator('#library-next')).to_be_disabled()
    page.locator('#library-query').fill('GROUP_0204');page.locator('#library-query').press('Enter')
    expect(page.locator('#native-objects tr')).to_have_count(1);expect(page.locator('#native-objects')).to_contain_text('2 Zeilen')
    page.locator('#releases tr').filter(has_text=large['id']).get_by_role('button',name='NX-Werkzeugschema').click()
    expect(page.locator('#message')).to_contain_text('Vollständige native Werkzeuggruppe')
    value=json.loads(page.locator('#cam').input_value())
    assert value['library_id']=='tool-metric' and len(value['records'])==2
    keys={record['values']['LIBRF'] for record in value['records']};assert len(keys)==1 and next(iter(keys)).startswith('CUSTOM_')
    assert {record['values']['RTYPE'] for record in value['records']}=={'1','2'}
    assert store.resources()==[]


def test_selection_manifest_change_blocks_even_an_otherwise_valid_preview(package_ui):
    import hashlib
    from nxserver.store import canonical
    from playwright.sync_api import expect
    page,store,target,source=package_ui
    page.locator('#file-adopt-release').select_option(target['id']);page.locator('#file-adopt-load').click()
    expect(page.locator('#file-adopt-files input')).to_have_count(1);check_file(page,'CUSTOM.prt')
    path='UGII/templates/CUSTOM.prt';payload=b'Synthetic changed bytes';(store.release_folder(target['id'])/path).write_bytes(payload)
    # Intentional fixture tampering: UI must not silently substitute new bytes.
    manifest=target['manifest']
    for item in manifest:
        if item['path']==path:item.update(size=len(payload),sha256=hashlib.sha256(payload).hexdigest())
    with store.db() as db:db.execute('UPDATE releases SET manifest=? WHERE id=?',(canonical(manifest),target['id']))
    page.locator('#file-adopt-preview').click();expect(page.locator('#message')).to_contain_text('Ausgewählte Dateimetadaten geändert')
    expect(page.locator('#file-adopt-apply')).to_be_disabled();expect(page.locator('#file-adopt-files input')).to_be_disabled()
    assert store.resources()==[]


def test_tool_example_reply_does_not_replace_new_editor_typing(package_ui):
    from playwright.sync_api import expect
    page,store,target,source=package_ui
    control=page.locator('#releases tr').filter(has_text=target['id']).get_by_role('button',name='NX-Werkzeugschema')
    control.evaluate('(button)=>button.id="held-example"')
    hold(page,'/libraries/tool-metric?offset=0&limit=1');track(page,'held-example');control.click()
    page.wait_for_function('()=>window.__held');page.locator('#tab-prepare').click();page.locator('#name').fill('New editor typing after example request')
    release_reply(page);expect(page.locator('#name')).to_have_value('New editor typing after example request')
    assert json.loads(page.locator('#cam').input_value())=={}
    assert store.resources()==[]

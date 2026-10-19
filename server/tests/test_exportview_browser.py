"""Native export inspection in temporary Edge/server/data fixtures, never NX."""
import json
import os

import pytest

from nxserver.libraries import SPECS,NativeTable,native_cam
from test_libraries import write
from test_requests_browser import ui,hold,track,release as release_reply
from test_preview_context_browser import open_export
from test_management import tool_payload

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated native export inspection.')


@pytest.fixture
def export_ui(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;identifier='holder-metric'
    fields=['LIBRF','RTYPE','SEQ','DESCR','FUTURE_2512','__proto__']
    template='#CLASS MULTI\nFORMAT '+' '.join(fields)+'\nDATA |STD|1|1|Standard|unchanged|literal\n#END_DATA\n'
    write(store.sources['test'],SPECS[identifier]['path'],template)
    target=store.import_release('test','2512','Native comparison target','fixture')
    text='#CLASS MULTI\nFORMAT '+' '.join(fields)+'\n'+''.join('DATA |constructor|2|'+str(index)+'|Component '+str(index)+'|Explicit future '+str(index)+'|Literal own value\n' for index in range(23))+'#END_DATA\n'
    table=NativeTable(text);resource=store.save_resource({'kind':'holder','name':'Native test group','cam_status':'approved',
        'cam':native_cam(identifier,'2512',table,table.records),'asset_ids':[],'erp':None,'geometry':tool_payload()['geometry']},'fixture')
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text(resource['name'])
    open_export(page,target);page.locator('#export-preview').click();expect(page.locator('#export-create')).to_be_enabled()
    page.locator('#export-native-box').evaluate('(element)=>{element.open=true;}')
    return page,store,target,resource


def objects(page):
    from playwright.sync_api import expect
    page.locator('#export-native-load').click();expect(page.locator('#export-native-objects > tr')).to_have_count(1)
    page.locator('#export-native-objects > tr').get_by_role('button',name='Vorher-/Nachher-Werte').click()


def test_gui_full_native_rows_future_fields_and_absent_before_have_no_writes(export_ui):
    from playwright.sync_api import expect
    page,store,target,resource=export_ui;before=store.manifest(store.release_folder(target['id']));audit=store.audit_entries()
    objects(page);expect(page.locator('#export-native-title')).to_contain_text('constructor')
    page.locator('#export-native-read').click();expect(page.locator('#export-native-rows > details')).to_have_count(20)
    first=page.locator('#export-native-rows > details').first;first.locator('summary').click()
    expect(first).to_contain_text('FUTURE_2512');expect(first).to_contain_text('Explicit future 0');expect(first).to_contain_text('__proto__');expect(first).to_contain_text('Literal own value')
    page.locator('#export-native-row-next').click();expect(page.locator('#export-native-rows > details')).to_have_count(3)
    expect(page.locator('#export-native-rows > details').first.locator('summary')).to_contain_text('Zeile 21')
    page.locator('#export-native-side').select_option('before');page.locator('#export-native-read').click()
    expect(page.locator('#export-native-row-state')).to_contain_text('nicht vorhanden');expect(page.locator('#export-native-rows > details')).to_have_count(0)
    assert store.audit_entries()==audit and store.manifest(store.release_folder(target['id']))==before and store.active()['release_id'] is None


@pytest.mark.parametrize('field',['complete','preview_hash','parent_manifest_sha256','items','libraries','file_sha'])
def test_bad_index_contract_never_presents_partial_native_objects(export_ui,field):
    from playwright.sync_api import expect
    page,store,target,resource=export_ui
    def bad(route):
        response=route.fetch();value=response.json()
        if field=='items':value['items'][0]['after_sha256']='invalid'
        elif field=='libraries':value['libraries']=[]
        elif field=='file_sha':value['libraries'][0]['after_sha256']='0'*64
        else:value[field]=False if field=='complete' else '0'*64
        route.fulfill(response=response,json=value)
    page.route('**/derive-preview/objects',bad);page.locator('#export-native-load').click()
    expect(page.locator('#export-native-state')).to_contain_text('Kein vollständiger gebundener DAT-Objektvergleich')
    expect(page.locator('#export-native-objects > tr')).to_have_count(0)
    assert len(store.releases())==2


def test_main_preview_without_native_contract_never_enables_export(export_ui):
    from playwright.sync_api import expect
    page,store,target,resource=export_ui
    def old(route):
        response=route.fetch();value=response.json();value.pop('native_preview_contract');route.fulfill(response=response,json=value)
    page.route('**/derive-preview',old);page.locator('#export-preview').click()
    expect(page.locator('#message')).to_contain_text('Kein vollständiger gebundener Exportvorschauvertrag')
    expect(page.locator('#export-create')).to_be_disabled();expect(page.locator('#export-native-load')).to_be_disabled()
    assert len(store.releases())==2


@pytest.mark.parametrize('field',['group_sha256','formats','values','row_index'])
def test_bad_row_contract_cannot_render_one_good_row_before_bad_row(export_ui,field):
    from playwright.sync_api import expect
    page,store,target,resource=export_ui;objects(page)
    def bad(route):
        response=route.fetch();value=response.json()
        if field=='group_sha256':value[field]='0'*64
        elif field=='formats':value[field][0]['fields'].append('Missing')
        elif field=='values':value['items'][-1]['values'].pop('__proto__')
        else:value['items'][-1]['row_index']=0
        route.fulfill(response=response,json=value)
    page.route('**/derive-preview/rows',bad);page.locator('#export-native-read').click()
    expect(page.locator('#export-native-row-state')).to_contain_text('nicht bestätigt')
    expect(page.locator('#export-native-rows > details')).to_have_count(0)
    assert len(store.releases())==2


@pytest.mark.parametrize('change',['filter_aba','close','logout'])
def test_late_object_index_does_not_fill_new_context(export_ui,change):
    from playwright.sync_api import expect
    page,store,target,resource=export_ui;hold(page,'/derive-preview/objects');track(page,'export-native-load')
    page.locator('#export-native-load').click();page.wait_for_function('()=>window.__held')
    if change=='filter_aba':page.locator('#export-native-query').fill('changed');page.locator('#export-native-query').fill('')
    elif change=='close':page.locator('#export-close').click()
    else:page.locator('#disconnect').click()
    release_reply(page);expect(page.locator('#export-native-objects > tr')).to_have_count(0)
    assert len(store.releases())==2


def test_side_aba_and_new_preview_discard_late_rows(export_ui):
    from playwright.sync_api import expect
    page,store,target,resource=export_ui;objects(page);hold(page,'/derive-preview/rows');track(page,'export-native-read')
    page.locator('#export-native-read').click();page.wait_for_function('()=>window.__held')
    page.locator('#export-native-side').select_option('before');page.locator('#export-native-side').select_option('after')
    release_reply(page);expect(page.locator('#export-native-rows > details')).to_have_count(0)
    page.locator('#export-preview').click();expect(page.locator('#export-native-load')).to_be_enabled()
    expect(page.locator('#export-native-objects > tr')).to_have_count(0)
    assert len(store.releases())==2

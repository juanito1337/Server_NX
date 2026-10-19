"""Whole-file planning in temporary Edge/server/data fixtures; never real NX."""
import os

import pytest

from nxserver.libraries import SPECS,NativeTable,native_cam
from test_libraries import write
from test_management import tool_payload
from test_requests_browser import ui,hold,track,release as release_reply
from test_preview_context_browser import open_export

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated file planning.')


@pytest.fixture
def files_ui(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;identifier='holder-metric'
    template='#CLASS SAMPLE\nFORMAT LIBRF DESCR\nDATA |STD|Standard\n#END_DATA\n'
    write(store.sources['test'],SPECS[identifier]['path'],template)
    for index in range(53):(store.sources['test']/('Inherited '+str(index)+'.txt')).write_bytes(b'Inherited fixture')
    target=store.import_release('test','2512','File comparison target','fixture')
    table=NativeTable('#CLASS SAMPLE\nFORMAT LIBRF DESCR\nDATA |CUSTOM|New fixture\n#END_DATA\n')
    part=store.add_asset('Custom.prt',b'Fake PRT, not real CAD','fixture');document=store.add_asset('Source.step',b'Fake STEP, not real CAD','fixture')
    resource=store.save_resource({'kind':'holder','name':'File owner fixture','cam_status':'approved',
        'cam':native_cam(identifier,'2512',table,table.records),'asset_ids':[part['id'],document['id']],
        'file_bindings':[{'asset_id':part['id'],'path':SPECS[identifier]['graphics_dir']+'/Custom.prt'}],
        'erp':None,'geometry':tool_payload()['geometry']},'fixture')
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text(resource['name'])
    open_export(page,target);page.locator('#export-preview').click();expect(page.locator('#export-create')).to_be_enabled()
    page.locator('#export-files-box').evaluate('(element)=>{element.open=true;}')
    return page,store,target,resource,part,document


def load(page,count=None):
    from playwright.sync_api import expect
    page.locator('#export-files-load').click()
    expect(page.locator('#export-files-state')).to_contain_text('Vollständiger Dateiplan')
    if count is not None:expect(page.locator('#export-files-items > tr')).to_have_count(count)


def test_gui_files_pages_search_hashes_private_metadata_and_no_writes(files_ui):
    from playwright.sync_api import expect
    page,store,target,resource,part,document=files_ui;audit=store.audit_entries();manifest=store.manifest(store.release_folder(target['id']))
    load(page,50);expect(page.locator('#export-files-totals')).to_contain_text('1 neu · 1 geändert')
    expect(page.locator('#export-files-next')).to_be_enabled();page.locator('#export-files-next').click()
    expect(page.locator('#export-files-items > tr')).to_have_count(6);expect(page.locator('#export-files-page')).to_contain_text('51–56 von 56')
    page.locator('#export-files-query').fill(part['sha256']);load(page,1)
    expect(page.locator('#export-files-items')).to_contain_text('Custom.prt');expect(page.locator('#export-files-items')).to_contain_text(resource['name'])
    page.locator('#export-files-view').select_option('private');page.locator('#export-files-query').fill('');page.locator('#export-files-load').click()
    expect(page.locator('#export-files-state')).to_contain_text('Dateibytes nicht erneut geprüft')
    expect(page.locator('#export-files-change')).to_be_disabled();expect(page.locator('#export-files-items > tr')).to_have_count(1)
    expect(page.locator('#export-files-items')).to_contain_text(document['filename']);expect(page.locator('#export-files-items')).to_contain_text(document['sha256'])
    assert store.audit_entries()==audit and store.manifest(store.release_folder(target['id']))==manifest and len(store.releases())==2


@pytest.mark.parametrize('field',['complete','preview_hash','parent_manifest_sha256','after_manifest_sha256','summary','counts','items','resource_revision','output_hash','private_integrity_checked'])
def test_bad_file_contract_clears_old_rows_and_supports_explicit_retry(files_ui,field):
    from playwright.sync_api import expect
    page,store,*_=files_ui;load(page,50)
    page.locator('#export-files-change').select_option('changed');load(page,2)
    def bad(route):
        response=route.fetch();value=response.json()
        if field=='summary':value['summary']['after_files']+=1
        elif field=='counts':value['counts']['created']+=1
        elif field=='items':value['items'][-1]['after']['size']=-1
        elif field=='resource_revision':
            next(item for item in value['items'] if item['resources'])['resources'][0]['revision']+=1
        elif field=='output_hash':next(item for item in value['items'] if item['kind']=='native_database')['after']['sha256']='0'*64
        else:value[field]=True if field=='private_integrity_checked' else False if field=='complete' else 'bad'
        route.fulfill(response=response,json=value)
    page.route('**/derive-preview/files',bad);page.locator('#export-files-load').click()
    expect(page.locator('#export-files-state')).to_contain_text('Dateiplan nicht bestätigt')
    expect(page.locator('#export-files-items > tr')).to_have_count(0);expect(page.locator('#export-files-next')).to_be_disabled()
    page.unroute('**/derive-preview/files',bad);load(page,2)
    assert len(store.releases())==2


@pytest.mark.parametrize('field',['resource_id','asset_id','reason','size'])
def test_private_metadata_must_match_current_resource_and_explicit_scope(files_ui,field):
    from playwright.sync_api import expect
    page,*_=files_ui;page.locator('#export-files-view').select_option('private')
    def bad(route):
        response=route.fetch();value=response.json();value['items'][0][field]=-1 if field=='size' else 'bad';route.fulfill(response=response,json=value)
    page.route('**/derive-preview/files',bad);page.locator('#export-files-load').click()
    expect(page.locator('#export-files-state')).to_contain_text('Dateiplan nicht bestätigt');expect(page.locator('#export-files-items > tr')).to_have_count(0)


@pytest.mark.parametrize('change',['query_aba','view_aba','new_preview','close','logout'])
def test_late_files_never_fill_new_context(files_ui,change):
    from playwright.sync_api import expect
    page,*_=files_ui;hold(page,'/derive-preview/files');track(page,'export-files-load')
    page.locator('#export-files-load').click();page.wait_for_function('()=>window.__held')
    if change=='query_aba':page.locator('#export-files-query').fill('other');page.locator('#export-files-query').fill('')
    elif change=='view_aba':page.locator('#export-files-view').select_option('private');page.locator('#export-files-view').select_option('files')
    elif change=='new_preview':page.locator('#export-preview').click();expect(page.locator('#export-create')).to_be_enabled()
    elif change=='close':page.locator('#export-close').click()
    else:page.locator('#disconnect').click()
    release_reply(page);expect(page.locator('#export-files-items > tr')).to_have_count(0)


def test_changed_metadata_pin_requires_new_main_preview_and_mobile_stays_bounded(files_ui):
    from playwright.sync_api import expect
    page,*_=files_ui;load(page,50)
    def changed(route):
        response=route.fetch();value=response.json();value['private_snapshot_sha256']='0'*64;route.fulfill(response=response,json=value)
    page.route('**/derive-preview/files',changed);page.locator('#export-files-next').click()
    expect(page.locator('#export-files-state')).to_contain_text('Gesamte Exportvorschau erneut prüfen')
    expect(page.locator('#export-files-load')).to_be_disabled();expect(page.locator('#export-files-items > tr')).to_have_count(0)
    page.unroute('**/derive-preview/files',changed);page.locator('#export-preview').click();expect(page.locator('#export-files-load')).to_be_enabled()
    page.locator('#export-files-box').evaluate('(element)=>{element.open=true;}');page.set_viewport_size({'width':390,'height':844});load(page,50)
    assert page.evaluate('()=>document.documentElement.scrollWidth<=window.innerWidth')

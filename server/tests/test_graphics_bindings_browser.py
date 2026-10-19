"""Graphical PRT placement with fake parts; no real NX/STEP/license access."""
import json
import os

import pytest

from nxserver.libraries import SPECS,NativeTable,native_cam
from test_libraries import write
from test_requests_browser import ui,hold,track,release

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated graphics placement GUI.')


@pytest.fixture
def graphics(ui,request):
    from playwright.sync_api import expect
    page,store,*_=ui;identifier=getattr(request,'param','holder-metric');spec=SPECS[identifier]
    standard='#CLASS SAMPLE\nFORMAT LIBRF DESCR\nDATA |STD|Standard\n#END_DATA\n'
    write(store.sources['test'],spec['path'],standard)
    target=store.import_release('test','2512','Graphics target','fixture')
    table=NativeTable(standard.replace('|STD|Standard','|CUSTOM_GRAPHICS|Customer'))
    first=store.add_asset('Part (1)#.prt',b'Synthetic first PRT; not a model','fixture')
    second=store.add_asset('Holder ü.prt',b'Synthetic second PRT; not a model','fixture')
    source=store.add_asset('Source.step',b'Synthetic source; not a STEP','fixture')
    resource=store.save_resource({'kind':spec['kind'],'name':'Graphics fixture','cam_status':'draft',
        'cam':native_cam(identifier,'2512',table,table.records),'asset_ids':[first['id'],second['id'],source['id']],'erp':None},'fixture')
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text(resource['name'])
    page.locator('#resources tr').filter(has_text=resource['name']).get_by_role('button',name='Bearbeiten',exact=True).click()
    page.locator('#graphics-load').click();expect(page.locator('#graphics-rows tr')).to_have_count(3)
    yield page,store,target,resource,first,second,source


def row(page,asset):return page.locator('[data-graphics-id="'+asset['id']+'"]')
def folder(page,asset):return row(page,asset).locator('[data-graphics-folder]')
def choose(page,asset):return row(page,asset).locator('[data-graphics-select]')


@pytest.mark.parametrize('graphics',['holder-metric','cmm-holder-metric','fixture-metric'],indirect=True)
def test_preview_apply_save_and_new_export_preserve_names_and_private_sources(graphics):
    from playwright.sync_api import expect
    page,store,target,resource,first,second,source=graphics;spec=SPECS[resource['cam']['library_id']]
    before=store.audit_entries();original=store.manifest(store.release_folder(target['id']))
    folder(page,first).fill('assembly/sub');choose(page,second).uncheck()
    page.locator('#graphics-preview').click();expect(page.locator('#graphics-apply')).to_be_enabled()
    expect(page.locator('#model-bindings')).to_have_value('[]')
    assert next(r for r in store.resources() if r['id']==resource['id'])['revision']==1
    page.on('dialog',lambda dialog:dialog.accept());page.locator('#graphics-apply').click()
    expected=[{'asset_id':first['id'],'path':spec['graphics_dir']+'/assembly/sub/'+first['filename']}]
    assert json.loads(page.locator('#model-bindings').input_value())==expected and store.audit_entries()==before
    assert page.evaluate('NXNative.resourceFields().geometry.asset_hashes')=={}
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    saved=next(r for r in store.resources() if r['id']==resource['id']);assert saved['revision']==2 and saved['file_bindings']==expected and saved['cam_status']=='draft'
    # Explicit synthetic approval only, not an assertion of real NX qualification.
    page.locator('#tab-manage').click()
    page.locator('#resources tr').filter(has_text=resource['name']).get_by_role('button',name='Bearbeiten',exact=True).click()
    page.locator('#camstatus').select_option('approved');page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    report=store.preview_export(target['id']);assert report['can_export'],report['conflicts']
    exported=store.import_release('','','Graphics export','fixture',parent=target['id'],preview_hash=report['preview_hash'])
    assert (store.release_folder(exported['id'])/expected[0]['path']).read_bytes()==b'Synthetic first PRT; not a model'
    assert not (store.release_folder(exported['id'])/spec['graphics_dir']/source['filename']).exists()
    assert not (store.release_folder(exported['id'])/spec['graphics_dir']/second['filename']).exists()
    assert store.manifest(store.release_folder(target['id']))==original and store.active()['release_id'] is None


@pytest.mark.parametrize('folder_value',['../outside','/absolute','bad\\folder','sub//nested','sub.'])
def test_unsafe_subfolder_cannot_replace_existing_editor_bindings(graphics,folder_value):
    from playwright.sync_api import expect
    page,store,_,resource,first,*_=graphics;before=store.audit_entries()
    folder(page,first).fill(folder_value);page.locator('#graphics-preview').click()
    expect(page.locator('#graphics-state')).to_contain_text('nicht bestätigt');expect(page.locator('#graphics-apply')).to_be_disabled()
    expect(folder(page,first)).to_have_value(folder_value);expect(page.locator('#model-bindings')).to_have_value('[]')
    assert store.audit_entries()==before and next(r for r in store.resources() if r['id']==resource['id'])==resource


@pytest.mark.parametrize('fault',['path','metadata','missing','private','library','flags','revision'])
def test_inconsistent_preview_cannot_be_applied(graphics,fault):
    from playwright.sync_api import expect
    page,store,_,resource,first,*_=graphics;before=store.audit_entries()
    def invalid(route):
        response=route.fetch();value=response.json()
        if fault=='path':value['file_bindings'][0]['path']='other.prt'
        elif fault=='metadata':value['entries'][0]['sha256']='f'*64
        elif fault=='missing':value['entries'].pop()
        elif fault=='private':value['private_asset_ids']=[]
        elif fault=='library':value['graphics_dir']='different/root'
        elif fault=='flags':value['nx_files_written']=True
        else:value['expected_revision']+=1
        route.fulfill(response=response,json=value)
    page.route('**/graphics-bindings/preview',invalid);page.locator('#graphics-preview').click()
    expect(page.locator('#graphics-state')).to_contain_text('nicht bestätigt');expect(page.locator('#graphics-apply')).to_be_disabled()
    expect(page.locator('#model-bindings')).to_have_value('[]');assert store.audit_entries()==before


@pytest.mark.parametrize('old_error',[False,True])
def test_subfolder_aba_discards_old_preview_and_error(graphics,old_error):
    from playwright.sync_api import expect
    page,store,_,_,first,*_=graphics;before=store.audit_entries()
    hold(page,'/graphics-bindings/preview',old_error);track(page,'graphics-preview')
    page.locator('#graphics-preview').click();page.wait_for_function('()=>window.__held')
    folder(page,first).fill('later');folder(page,first).fill('');release(page)
    expect(page.locator('#graphics-apply')).to_be_disabled();expect(page.locator('#model-bindings')).to_have_value('[]')
    expect(page.locator('#message')).not_to_contain_text('Old delayed error');assert store.audit_entries()==before


def test_editor_switch_and_raw_input_preserve_new_intent(graphics):
    from playwright.sync_api import expect
    page,store,_,resource,*_=graphics;before=store.audit_entries()
    hold(page,'/graphics-bindings/preview');track(page,'graphics-preview');page.locator('#graphics-preview').click();page.wait_for_function('()=>window.__held')
    page.locator('#editor-new').click();page.locator('#name').fill('New editor');release(page)
    expect(page.locator('#graphics-rows tr')).to_have_count(0);expect(page.locator('#graphics-apply')).to_be_disabled()
    expect(page.locator('#name')).to_have_value('New editor');assert store.audit_entries()==before


def test_default_mode_is_explicit_and_mobile_table_does_not_overflow_page(graphics):
    from playwright.sync_api import expect
    page,store,_,_,first,*_=graphics;before=store.audit_entries();page.on('dialog',lambda dialog:dialog.accept())
    folder(page,first).fill('sub');page.locator('#graphics-preview').click();expect(page.locator('#graphics-apply')).to_be_enabled()
    page.locator('#graphics-apply').click();page.locator('#graphics-default').click()
    expect(page.locator('#model-bindings')).to_have_value('[]');expect(page.locator('#graphics-state')).to_contain_text('Standardmodus')
    page.locator('#graphics-load').click();expect(page.locator('#graphics-rows tr')).to_have_count(3)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    assert store.audit_entries()==before


def test_prepared_components_stay_readonly_until_explicit_package_detach(ui):
    from playwright.sync_api import expect
    from nxserver.results import Results
    from test_result_jobs_browser import prepare
    page,store,*_=ui;target,tool,job,packet=prepare(page,store)
    service=Results(store);result=service.upload(job['id'],packet,'fixture')
    preview,_,_=service.preview(result['id'],target['id']);service.apply(result['id'],target['id'],preview['preview_hash'],'fixture')
    parent=next(value for value in store.resources() if value['id']==tool['id']);before=store.audit_entries()
    page.locator('#refresh').click();expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    page.locator('#tab-manage').click()
    page.locator('#resources tr').filter(has_text=parent['name']).get_by_role('button',name='Bearbeiten',exact=True).click()
    original=page.locator('#model-bindings').input_value();page.locator('#graphics-load').click()
    expect(page.locator('#graphics-rows tr')).to_have_count(3)
    expect(page.locator('#graphics-preview')).to_be_disabled();expect(page.locator('#graphics-default')).to_be_disabled()
    for item in page.locator('#graphics-rows input').all():expect(item).to_be_disabled()
    expect(page.locator('#model-bindings')).to_have_value(original)
    page.locator('#model-package-detach').click();page.locator('#graphics-load').click()
    expect(page.locator('#graphics-preview')).to_be_enabled()
    assert store.audit_entries()==before and next(value for value in store.resources() if value['id']==tool['id'])==parent


@pytest.mark.parametrize('graphics',['fixture-metric'],indirect=True)
def test_file_package_mode_and_graphics_paths_cannot_be_edited_at_the_same_time(graphics):
    from playwright.sync_api import expect
    page,store,*_=graphics;before=store.audit_entries();page.locator('#package-enabled').check()
    expect(page.locator('#graphics-preview')).to_be_disabled();expect(page.locator('#graphics-default')).to_be_disabled()
    expect(page.locator('#graphics-state')).to_contain_text('schreibgeschützt')
    assert store.audit_entries()==before


def test_empty_selection_never_silently_enables_default_export(graphics):
    from playwright.sync_api import expect
    page,store,_,_,first,second,_=graphics;before=store.audit_entries()
    page.on('dialog',lambda dialog:dialog.accept());page.locator('#graphics-preview').click();expect(page.locator('#graphics-apply')).to_be_enabled()
    page.locator('#graphics-apply').click();original=page.locator('#model-bindings').input_value()
    page.locator('#graphics-load').click();expect(page.locator('#graphics-rows tr')).to_have_count(3)
    choose(page,first).uncheck();choose(page,second).uncheck()
    expect(page.locator('#graphics-preview')).to_be_disabled();expect(page.locator('#graphics-apply')).to_be_disabled()
    expect(page.locator('#model-bindings')).to_have_value(original);assert original!='[]' and store.audit_entries()==before


def test_incomplete_raw_bindings_survive_load_until_explicit_default_confirmation(graphics):
    from playwright.sync_api import expect
    page,store,*_=graphics;before=store.audit_entries()
    page.locator('#editor-section details.technical').evaluate('(element)=>{element.open=true;}')
    page.locator('#model-bindings').fill('[');page.locator('#graphics-load').click()
    expect(page.locator('#message')).to_contain_text('vollständige JSON-Liste')
    expect(page.locator('#model-bindings')).to_have_value('[')
    page.once('dialog',lambda dialog:dialog.dismiss());page.locator('#graphics-default').click()
    expect(page.locator('#model-bindings')).to_have_value('[')
    page.once('dialog',lambda dialog:dialog.accept());page.locator('#graphics-default').click()
    expect(page.locator('#model-bindings')).to_have_value('[]');assert store.audit_entries()==before


def test_pending_model_load_after_attachment_aba_cannot_restore_old_rows(graphics):
    from playwright.sync_api import expect
    page,store,_,resource,first,*_=graphics;before=store.audit_entries()
    hold(page,'/assets/'+first['id']+'/metadata');track(page,'graphics-load');page.locator('#graphics-load').click()
    page.wait_for_function('()=>window.__held')
    page.locator('#editor-section details.technical').evaluate('(element)=>{element.open=true;}')
    original=page.locator('#assetids').input_value();page.locator('#assetids').fill('[]');page.locator('#assetids').fill(original)
    release(page);expect(page.locator('#graphics-rows tr')).to_have_count(0)
    expect(page.locator('#graphics-apply')).to_be_disabled();assert store.audit_entries()==before


def test_legacy_tool_can_prepare_explicit_paths_without_changing_cam_representation(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;asset=store.add_asset('LEGACY.prt',b'Synthetic legacy PRT','fixture')
    cam={'class_name':'SAMPLE','values':{'LIBRF':'LEGACY','DESCR':'Legacy tool'}}
    resource=store.save_resource({'kind':'tool','name':'Legacy graphics','cam':cam,'asset_ids':[asset['id']],'erp':None,'cam_status':'draft'},'fixture')
    before=store.audit_entries();page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text(resource['name'])
    page.locator('#resources tr').filter(has_text=resource['name']).get_by_role('button',name='Bearbeiten',exact=True).click()
    page.locator('#graphics-load').click();expect(page.locator('#graphics-rows tr')).to_have_count(1)
    folder(page,asset).fill('legacy');page.locator('#graphics-preview').click();expect(page.locator('#graphics-apply')).to_be_enabled()
    page.on('dialog',lambda dialog:dialog.accept());page.locator('#graphics-apply').click()
    assert json.loads(page.locator('#cam').input_value())==cam
    assert json.loads(page.locator('#model-bindings').input_value())==[{'asset_id':asset['id'],'path':SPECS['tool-metric']['graphics_dir']+'/legacy/'+asset['filename']}]
    assert store.audit_entries()==before and next(value for value in store.resources() if value['id']==resource['id'])==resource

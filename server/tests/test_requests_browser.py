"""Isolated delayed-response UI contracts, never a real NX/CAM qualification."""
import copy
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from nxserver.store import Store,TOOL_DATABASE
from test_management import DATABASE
from browser_test_support import wait_for_test_server

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge races.')


@pytest.fixture
def ui(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import sync_playwright,expect
    source=tmp_path/'source';database=source/TOOL_DATABASE;database.parent.mkdir(parents=True)
    database.write_text(DATABASE,encoding='utf-8');store=Store(tmp_path/'managed',{'test':source})
    release=store.import_release('test','2512','Race release','fixture')
    resource=store.save_resource({'kind':'gauge','name':'Initial fixture','cam':{},'erp':None,'asset_ids':[],
        'cam_status':'draft','geometry':{'mode':'none','status':'needs_preparation'}},'fixture')
    settings=tmp_path/'settings.json';settings.write_text(json.dumps({'data_dir':str(store.root),'sources':{'test':str(source)}}),encoding='utf-8')
    environment=dict(os.environ);token='isolated-requests-browser-'+'q'*40
    for key in ('NX_ADMIN_TOKEN_FILE','NX_ERP_TOKEN','NX_ERP_TOKEN_FILE'):environment.pop(key,None)
    environment['NX_ADMIN_TOKEN']=token
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    process=subprocess.Popen([sys.executable,'-m','nxserver','--settings',str(settings),'--port',str(port)],
        cwd=Path(__file__).resolve().parents[1],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    base=f'http://127.0.0.1:{port}'
    try:
        wait_for_test_server(process, port)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':1400,'height':950});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                startup='navigation'
                try:
                    page.goto(base);startup='credential';page.locator('#token').fill(token)
                    startup='connect';page.locator('#connect').click()
                    startup='connected-notice';expect(page.locator('#message')).to_contain_text('Verbunden')
                except Exception as error:
                    try:boot=page.evaluate('()=>globalThis.NXClientBoot?.status?.()??null')
                    except Exception:boot=None
                    raise AssertionError('Isolated browser startup failed: '+json.dumps(
                        {'stage':startup,'boot':boot,'page_error_count':len(errors)})) from error
                yield page,store,release,resource
                assert not errors,errors
            finally:browser.close()
    finally:
        process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


def hold(page,suffix,error=False):
    page.evaluate("""([suffix,error])=>{
        const original=window.fetch;window.__held=false;window.__decoded=false;
        window.fetch=async(...args)=>{
            const value=await original(...args);
            if(String(args[0]).endsWith(suffix)){
                window.fetch=original;
                const response=error?new Response(JSON.stringify({detail:'Old delayed error'}),{status:500}):value;
                const json=response.json.bind(response);
                response.json=async()=>{const result=await json();window.__decoded=true;return result;};
                const observed=new WeakSet();function observeBody(body){if(!body||observed.has(body))return;observed.add(body);
                  const cancelBody=body.cancel.bind(body);body.cancel=(...args)=>{window.__decoded=true;return cancelBody(...args);};
                  const getReader=body.getReader.bind(body);body.getReader=(...options)=>{
                    const reader=getReader(...options),read=reader.read.bind(reader),cancel=reader.cancel.bind(reader);
                    reader.read=async(...args)=>{const result=await read(...args);if(result.done)window.__decoded=true;return result;};
                    reader.cancel=(...args)=>{window.__decoded=true;return cancel(...args);};return reader;};}
                observeBody(response.body);
                // Response.clone replaces the original body with a tee branch.
                // Observe that new original branch too, not a detached old stream.
                const clone=response.clone.bind(response);response.clone=(...args)=>{const result=clone(...args);observeBody(response.body);return result;};
                return new Promise(resolve=>{window.__releaseHeld=()=>resolve(response);window.__held=true;});
            }
            return value;
        };
    }""",[suffix,error])


def release(page):
    page.evaluate('window.__releaseHeld()');page.wait_for_function('()=>window.__decoded')
    page.wait_for_function('()=>window.__oldActionFinished')


def track(page,identifier):
    page.evaluate("""id=>{const element=document.getElementById(id),handler=element.onclick;
        element.onclick=function(...args){element.onclick=handler;window.__oldActionFinished=false;
            const result=handler.apply(this,args);Promise.resolve(result).finally(()=>window.__oldActionFinished=true);return result;};
    }""",identifier)


def payload(resource):return {key:copy.deepcopy(value) for key,value in resource.items() if key not in ('id','revision','updated_at')}


@pytest.mark.parametrize('old_error',[False,True])
def test_latest_refresh_wins_and_stale_errors_do_not_replace_a_newer_view(ui,old_error):
    from playwright.sync_api import expect
    page,store,release_value,resource=ui
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click()
    page.locator('#name').fill('Unsaved editor title')
    hold(page,'/api/v1/workspace',old_error);track(page,'refresh');page.locator('#refresh').click();page.wait_for_function('()=>window.__held')
    value=payload(resource);value['name']='Newer server fixture';store.save_resource(value,'fixture',resource['id'],1)
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text('Newer server fixture')
    release(page)
    expect(page.locator('#resources')).to_contain_text('Newer server fixture')
    expect(page.locator('#resources')).not_to_contain_text('Initial fixture')
    expect(page.locator('#name')).to_have_value('Unsaved editor title')
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('new_resource',[False,True])
def test_edits_typed_during_save_are_preserved_and_the_saved_revision_is_adopted(ui,new_resource):
    from playwright.sync_api import expect
    page,store,release_value,resource=ui
    if new_resource:
        page.locator('#new').click();page.locator('#kind').select_option('gauge')
    else:page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click()
    page.locator('#name').fill('Submitted value')
    endpoint='/api/v1/resources' if new_resource else '/api/v1/resources/'+resource['id']
    hold(page,endpoint);track(page,'save');page.locator('#save').click();page.wait_for_function('()=>window.__held')
    page.locator('#name').fill('Typed after submitting')
    release(page)
    expect(page.locator('#save')).to_be_enabled()
    expect(page.locator('#name')).to_have_value('Typed after submitting')
    expect(page.locator('#message')).to_contain_text('ungespeichert')
    page.locator('#save').click();expect(page.locator('#message')).to_contain_text('Stammdatenrevision gespeichert')
    saved=next(value for value in store.resources() if value['name']=='Typed after submitting')
    assert saved['revision']==(2 if new_resource else 3)
    assert len(store.resources())==(2 if new_resource else 1)


def test_incomplete_json_typed_after_save_is_not_reset(ui):
    from playwright.sync_api import expect
    page,store,release_value,resource=ui
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click()
    page.locator('#editor-section details.technical > summary').click()
    hold(page,'/api/v1/resources/'+resource['id']);track(page,'save');page.locator('#save').click();page.wait_for_function('()=>window.__held')
    page.locator('#cam').fill('{');release(page)
    expect(page.locator('#cam')).to_have_value('{');expect(page.locator('#message')).to_contain_text('ungespeichert')
    assert store.resources()[0]['revision']==2


def test_secondary_module_error_does_not_hide_a_successful_core_refresh(ui):
    from playwright.sync_api import expect
    page,store,release_value,resource=ui
    value=payload(resource);value['name']='Core available';store.save_resource(value,'fixture',resource['id'],1)
    page.evaluate("""()=>{const original=window.fetch;window.fetch=(...args)=>{
        if(String(args[0]).endsWith('/migrations/options'))return Promise.resolve(new Response(JSON.stringify({detail:'Worker temporarily unavailable'}),{status:500}));
        return original(...args);};} """)
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text('Core available')
    expect(page.locator('#refresh-state')).to_contain_text('Versionsupdate: Worker temporarily unavailable')
    expect(page.locator('#resources')).to_have_attribute('aria-busy','false')


def test_native_query_changed_away_and_back_cannot_reuse_an_old_response(ui):
    from playwright.sync_api import expect
    page,store,release_value,resource=ui
    page.locator('#library-inventory').click();expect(page.locator('#native-library option')).to_have_count(1)
    page.locator('#library-query').fill('STD_1')
    hold(page,'/tool-metric?query=STD_1&offset=0&limit=100');track(page,'library-read');page.locator('#library-read').click();page.wait_for_function('()=>window.__held')
    page.locator('#library-query').fill('NO_MATCH');page.locator('#library-read').click()
    expect(page.locator('#native-objects tr')).to_have_count(0)
    page.locator('#library-query').fill('STD_1');release(page)
    expect(page.locator('#native-objects tr')).to_have_count(0)
    page.locator('#library-read').click();expect(page.locator('#native-objects')).to_contain_text('STD_1')


def test_creation_schema_changed_away_and_back_keeps_existing_editor_values(ui):
    from playwright.sync_api import expect
    page,store,release_value,resource=ui
    page.locator('#new').click();page.locator('#name').fill('Unsaved title')
    page.locator('#create-inventory').click();expect(page.locator('#create-library option')).to_have_count(1)
    from nxserver.library_schemas import LibrarySchemas
    pin=LibrarySchemas(store).inventory(release_value['id'])['inventory_sha256']
    hold(page,'/library-schemas/tool-metric?inventory_sha256='+pin);track(page,'create-schema');page.locator('#create-schema').click();page.wait_for_function('()=>window.__held')
    page.locator('#create-library').select_option('tool-metric');release(page)
    expect(page.locator('#native-new')).to_be_disabled();expect(page.locator('#name')).to_have_value('Unsaved title')
    page.locator('#create-schema').click();expect(page.locator('#native-new')).to_be_enabled()
    expect(page.locator('#name')).to_have_value('Unsaved title')


def test_late_geometry_hash_binding_cannot_bind_another_editor(ui):
    from playwright.sync_api import expect
    page,store,release_value,resource=ui
    asset=store.add_asset('test.pdf',b'Synthetic attachment, not a real PDF','fixture')
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click()
    page.locator('#editor-section details.technical > summary').click()
    page.locator('#assetids').fill(json.dumps([asset['id']]))
    hold(page,'/assets/'+asset['id']+'/metadata');track(page,'geometry-bind');page.locator('#geometry-bind').click();page.wait_for_function('()=>window.__held')
    page.locator('#editor-new').click();page.locator('#name').fill('New editor');release(page)
    expect(page.locator('#name')).to_have_value('New editor')
    assert page.evaluate('Object.keys(NXNative.resourceFields().geometry.asset_hashes).length')==0


def test_refresh_preserves_prepared_result_selection_but_new_revision_invalidates_it(ui):
    from playwright.sync_api import expect
    from nxserver.geometry import GeometryJobs
    from test_geometry import STEP
    page,store,release_value,resource=ui
    asset=store.add_asset('input.step',STEP,'fixture')
    tool=store.save_resource({'kind':'tool','name':'STEP source','cam':{},'erp':None,'asset_ids':[asset['id']],
        'cam_status':'draft','geometry':{'mode':'step','status':'needs_preparation'}},'fixture')
    job=GeometryJobs(store).create(tool['id'],1,asset['id'],'drilling_solid','fixture')
    page.locator('#refresh').click();expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    page.locator('#tab-prepare').click();page.locator('#step-resource').select_option(tool['id'])
    page.locator('#geometry-jobs').get_by_role('button',name='Ergebnispaket').click()
    page.locator('#result-key').fill('UNSAVED_TOOL_KEY')
    page.locator('#refresh').click();expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    expect(page.locator('#result-key')).to_have_value('UNSAVED_TOOL_KEY')
    assert len(GeometryJobs(store).list(tool['id']))==1
    value=payload(tool);value['name']='New STEP source revision';store.save_resource(value,'fixture',tool['id'],1)
    page.locator('#refresh').click();expect(page.locator('#result-key')).to_have_value('')
    expect(page.locator('#geometry-jobs')).to_contain_text('ältere Stammdatenrevision')


def test_folder_closed_during_request_does_not_repopulate_another_dialog(ui):
    from playwright.sync_api import expect
    page,store,release_value,resource=ui
    page.locator('#tab-update').click()
    page.evaluate("""()=>{const original=window.fetch;window.__held=false;window.__decoded=false;
        window.fetch=async(...args)=>{const response=await original(...args);if(String(args[0]).includes('/folders?')){
            window.fetch=original;const json=response.json.bind(response);response.json=async()=>{const value=await json();window.__decoded=true;return value;};
            const observed=new WeakSet();function observeBody(body){if(!body||observed.has(body))return;observed.add(body);
              const cancel=body.cancel.bind(body);body.cancel=(...args)=>{window.__decoded=true;return cancel(...args);};}
            observeBody(response.body);const clone=response.clone.bind(response);
            response.clone=(...args)=>{const value=clone(...args);observeBody(response.body);return value;};
            return new Promise(resolve=>{window.__releaseHeld=()=>resolve(response);window.__held=true;});}return response;};}""")
    track(page,'source-browse');page.locator('#source-browse').click();page.wait_for_function('()=>window.__held')
    page.locator('#folder-close').click();page.locator('#target-browse').click()
    expect(page.locator('#folder-title')).to_have_text('Übergeordneten Zielordner auswählen')
    expect(page.locator('#folder-select')).to_be_enabled();target=page.locator('#folder-path').inner_text()
    release(page);expect(page.locator('#folder-path')).to_have_text(target)
    expect(page.locator('#folder-title')).to_have_text('Übergeordneten Zielordner auswählen')
    page.locator('#folder-close').click()


def test_logout_during_save_preserves_a_new_sessions_editor(ui):
    from playwright.sync_api import expect
    page,store,release_value,resource=ui
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click()
    hold(page,'/api/v1/resources/'+resource['id']);track(page,'save');page.locator('#save').click();page.wait_for_function('()=>window.__held')
    page.locator('#disconnect').click();page.locator('#token').fill('isolated-requests-browser-'+'q'*40);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden')
    page.locator('#new').click();page.locator('#kind').select_option('gauge');page.locator('#name').fill('New session unsaved')
    before=page.locator('#message').inner_text()
    expect(page.locator('#save')).to_be_disabled();release(page)
    expect(page.locator('#name')).to_have_value('New session unsaved')
    expect(page.locator('#message')).to_have_text(before)
    page.locator('#save-receipt').click();expect(page.locator('#save')).to_be_enabled()
    expect(page.locator('#name')).to_have_value('New session unsaved')
    assert len(store.resources())==1 and store.resources()[0]['revision']==2


def test_pending_backup_submission_stays_disabled_after_a_core_refresh(ui):
    from playwright.sync_api import expect
    page,store,release_value,resource=ui
    page.on('dialog',lambda dialog:dialog.accept());page.locator('#backup-legacy').check()
    hold(page,'/api/v1/backups');track(page,'backup-create');page.locator('#backup-create').click();page.wait_for_function('()=>window.__held')
    page.locator('#refresh').click();expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    expect(page.locator('#backup-create')).to_be_disabled()
    release(page);expect(page.locator('#backup-state')).to_contain_text('Sicherung vollständig erstellt',timeout=20000)
    assert len(list((store.root/'b/jobs').glob('*/job.json')))==1


def test_old_save_finally_cannot_enable_a_new_sessions_pending_save(ui):
    from playwright.sync_api import expect
    page,store,release_value,resource=ui
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click()
    hold(page,'/api/v1/resources/'+resource['id']);track(page,'save');page.locator('#save').click();page.wait_for_function('()=>window.__held')
    assert page.evaluate('sessionStorage.getItem("nx.pending-resource-write.v1")')
    page.evaluate('()=>{window.__releasePrevious=window.__releaseHeld;}')
    page.locator('#disconnect').click();page.locator('#token').fill('isolated-requests-browser-'+'q'*40);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden')
    page.locator('#new').click();page.locator('#kind').select_option('gauge');page.locator('#name').fill('New session saved')
    assert page.evaluate('sessionStorage.getItem("nx.pending-resource-write.v1")')
    page.locator('#save-receipt').click();expect(page.locator('#save')).to_be_enabled()
    hold(page,'/api/v1/resources');page.locator('#save').click();page.wait_for_function('()=>window.__held')
    page.evaluate('window.__releasePrevious()');page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#save')).to_be_disabled()
    page.evaluate('window.__releaseHeld()');expect(page.locator('#save')).to_be_enabled()
    assert len(store.resources())==2
    assert next(value for value in store.resources() if value['name']=='New session saved')['revision']==1

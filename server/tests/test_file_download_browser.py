"""Bounded ordinary downloads on synthetic servers; no actual NX execution."""
import hashlib
import os

import pytest

from test_requests_browser import ui,hold,track
from test_launcher_refresh_browser import old_launcher

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated ordinary download UI.')


def starter(page,target):
    return page.locator('#releases tr').filter(has_text=target['id']).get_by_role('button',name='Test-Starter herunterladen',exact=True)


def test_starter_streams_without_legacy_blob_or_whole_body_array_buffer(ui,tmp_path):
    from playwright.sync_api import expect
    page,store,target,*_=ui;expected=store.get_release(target['id'])['test_launcher']['package_sha256'];before=store.audit_entries()
    page.evaluate("""()=>{const original=fetch;window.fetch=async(...args)=>{
        const response=await original(...args);
        if(String(args[0]).endsWith('/test-launcher/package')){
            response.blob=async()=>{throw new Error('Forbidden full response blob');};
            response.arrayBuffer=async()=>{throw new Error('Forbidden full response array buffer');};
        }return response;
    };}""")
    with page.expect_download(timeout=8000) as pending:starter(page,target).click()
    download=pending.value;path=tmp_path/'starter.zip';download.save_as(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
    expect(page.locator('#file-download-state')).to_contain_text('Prüfsumme und Größe bestätigt')
    assert store.audit_entries()==before and store.active()['release_id'] is None


def test_starter_download_is_discarded_after_a_new_package_revision_is_refreshed(ui,monkeypatch):
    from playwright.sync_api import expect
    page,store,target,*_=ui;downloads=[];page.on('download',lambda value:downloads.append(value))
    hold(page,'/test-launcher/package');button=starter(page,target)
    button.evaluate('(element)=>{element.id="synthetic-starter-download";}');track(page,'synthetic-starter-download')
    button.click();page.wait_for_function('()=>window.__held')
    old_launcher(store,target,monkeypatch);page.locator('#refresh').click()
    expected=store.get_release(target['id'])['test_launcher']['package_sha256']
    page.wait_for_function('expected=>releases.some(value=>value.test_launcher?.package_sha256===expected)',arg=expected)
    page.evaluate('window.__releaseHeld()');page.wait_for_function('()=>window.__oldActionFinished')
    assert downloads==[]


def held_starter(page,target):
    hold(page,'/test-launcher/package');button=starter(page,target)
    button.evaluate('(element)=>{element.id="synthetic-starter-download";}');track(page,'synthetic-starter-download')
    button.click();page.wait_for_function('()=>window.__held')


def finish_held(page):
    page.evaluate('window.__releaseHeld()');page.wait_for_function('()=>window.__oldActionFinished')


def test_server_only_revision_change_is_checked_again_without_a_workspace_refresh(ui,monkeypatch):
    from playwright.sync_api import expect
    page,store,target,*_=ui;downloads=[];page.on('download',lambda value:downloads.append(value))
    held_starter(page,target);old_launcher(store,target,monkeypatch);finish_held(page)
    expect(page.locator('#file-download-state')).to_contain_text('Testpaketstand hat sich geändert');assert downloads==[]


def test_old_workspace_pin_is_rejected_before_requesting_a_replaced_package(ui,monkeypatch):
    from playwright.sync_api import expect
    page,store,target,*_=ui;requests=[]
    page.on('request',lambda request:requests.append(request.url) if request.url.endswith('/test-launcher/package') else None)
    old_launcher(store,target,monkeypatch);starter(page,target).click()
    expect(page.locator('#file-download-state')).to_contain_text('Testpaketstand hat sich geändert');assert requests==[]


@pytest.mark.parametrize('code',[401,403,409,500])
def test_http_failure_does_not_parse_or_display_arbitrary_error_response_content(ui,code):
    from playwright.sync_api import expect
    page,store,target,*_=ui;before=store.audit_entries();downloads=[];page.on('download',lambda value:downloads.append(value))
    page.route('**/api/v1/releases/'+target['id']+'/test-launcher/package',lambda route:route.fulfill(status=code,json={'detail':'Synthetic private response, must not display.'}))
    starter(page,target).click();expect(page.locator('#file-download-state')).to_contain_text('HTTP '+str(code))
    expect(page.locator('body')).not_to_contain_text('Synthetic private response');assert downloads==[] and store.audit_entries()==before


@pytest.mark.parametrize('damage',['same_size','short','long'])
def test_corrupt_starter_bytes_never_reach_a_browser_download(ui,damage):
    from playwright.sync_api import expect
    page,store,target,*_=ui;before=store.audit_entries();downloads=[];page.on('download',lambda value:downloads.append(value))
    def tamper(route):
        response=route.fetch();raw=response.body()
        raw=bytes([raw[0]^1])+raw[1:] if damage=='same_size' else raw[:-1] if damage=='short' else raw+b'x'
        route.fulfill(status=200,body=raw,headers={'Content-Type':'application/zip','Content-Length':str(len(raw))})
    page.route('**/api/v1/releases/'+target['id']+'/test-launcher/package',tamper)
    starter(page,target).click();expect(page.locator('#file-download-state')).to_contain_text('Datei nicht übergeben')
    assert downloads==[] and store.audit_entries()==before


@pytest.mark.parametrize('length',[None,'0','01','-1','invalid','9007199254740993','67108865'])
def test_unsafe_unknown_launcher_size_is_rejected_before_opening_a_reader(ui,length):
    from playwright.sync_api import expect
    page,store,target,*_=ui;before=store.audit_entries();downloads=[];page.on('download',lambda value:downloads.append(value))
    page.evaluate("""length=>{const original=fetch;window.__unsafeReaders=0;window.fetch=async(...args)=>{
        const response=await original(...args);if(!String(args[0]).endsWith('/test-launcher/package'))return response;
        return {ok:true,status:200,headers:{get:key=>key==='content-length'?length:'application/zip'},
            body:{getReader(){window.__unsafeReaders++;throw new Error('Must reject before opening a reader');}}};
    };}""",length)
    starter(page,target).click();expect(page.locator('#file-download-state')).to_contain_text('Datei nicht übergeben')
    assert page.evaluate('window.__unsafeReaders')==0 and downloads==[] and store.audit_entries()==before


def test_repeated_click_is_one_file_request_and_the_first_download_still_completes(ui):
    from playwright.sync_api import expect
    page,store,target,*_=ui;before=store.audit_entries();requests=[]
    page.on('request',lambda request:requests.append(request.url) if request.url.endswith('/test-launcher/package') else None)
    held_starter(page,target);starter(page,target).click()
    expect(page.locator('#message')).to_contain_text('Dateidownload läuft bereits');assert len(requests)==1
    with page.expect_download():finish_held(page)
    expect(page.locator('#file-download-cancel')).to_be_disabled();assert store.audit_entries()==before


@pytest.mark.parametrize('action',['cancel','logout'])
def test_cancelled_old_download_cannot_publish_or_overwrite_a_new_success(ui,action):
    from playwright.sync_api import expect
    page,store,target,*_=ui;before=store.audit_entries();downloads=[];page.on('download',lambda value:downloads.append(value))
    held_starter(page,target)
    if action=='cancel':page.locator('#file-download-cancel').click()
    else:
        page.locator('#disconnect').click();expect(page.locator('#file-download-box')).to_be_hidden()
        page.locator('#token').fill('isolated-requests-browser-'+'q'*40);page.locator('#connect').click()
        expect(page.locator('#message')).to_contain_text('Verbunden')
    with page.expect_download():starter(page,target).click()
    expect(page.locator('#file-download-state')).to_contain_text('Prüfsumme und Größe bestätigt')
    finish_held(page)
    expect(page.locator('#file-download-state')).to_contain_text('Prüfsumme und Größe bestätigt')
    assert len(downloads)==1 and store.audit_entries()==before


def test_missing_hash_component_is_a_clear_failure_without_publication(ui):
    from playwright.sync_api import expect
    page,store,target,*_=ui;downloads=[];page.on('download',lambda value:downloads.append(value))
    page.evaluate('window.jsSHA=undefined');starter(page,target).click()
    expect(page.locator('#file-download-state')).to_contain_text('Komponente fehlt');assert downloads==[]


def attached(page,store):
    from playwright.sync_api import expect
    content=b'Synthetic attachment bytes; not a real drawing.'
    asset=store.add_asset('Zeichnung ü.pdf',content,'fixture')
    resource=store.save_resource({'kind':'gauge','name':'Synthetic download resource','cam':{},'erp':None,
        'cam_status':'draft','asset_ids':[asset['id']]},'fixture')
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text(resource['name'])
    page.locator('#resources tr').filter(has_text=resource['name']).get_by_role('button',name='Bearbeiten',exact=True).click()
    expect(page.locator('#attachments tr')).to_have_count(1)
    return asset,content


def test_unicode_attachment_download_preserves_exact_bytes_and_name(ui,tmp_path):
    from playwright.sync_api import expect
    page,store,*_=ui;asset,content=attached(page,store);before=store.audit_entries()
    with page.expect_download() as pending:page.locator('#attachments').get_by_role('button',name='Herunterladen',exact=True).click()
    download=pending.value;assert download.suggested_filename==asset['filename']
    path=tmp_path/'download.pdf';download.save_as(path);assert path.read_bytes()==content
    expect(page.locator('#file-download-state')).to_contain_text('Prüfsumme und Größe bestätigt');assert store.audit_entries()==before


def test_old_attachment_download_is_cancelled_when_the_editor_changes(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;asset,_=attached(page,store);before=store.audit_entries();downloads=[];page.on('download',lambda value:downloads.append(value))
    hold(page,'/assets/'+asset['id']);button=page.locator('#attachments').get_by_role('button',name='Herunterladen',exact=True)
    button.evaluate('(element)=>{element.id="synthetic-asset-download";}');track(page,'synthetic-asset-download')
    button.click();page.wait_for_function('()=>window.__held');page.locator('#editor-new').click();finish_held(page)
    expect(page.locator('#cam')).to_have_value('{}');assert downloads==[] and store.audit_entries()==before


def test_step_import_download_uses_the_same_verified_receiver_without_starting_nx(ui,tmp_path):
    from playwright.sync_api import expect
    from test_geometry_recovery_browser import prepare
    from nxserver.geometry import GeometryJobs
    page,store,*_=ui;tool,_=prepare(page,store);page.locator('#step-job-create').click()
    expect(page.locator('#message')).to_contain_text('Auftrag registriert');job=GeometryJobs(store).list(tool['id'])[0];before=store.audit_entries()
    with page.expect_download() as pending:page.locator('#geometry-jobs').get_by_role('button',name='Importpaket herunterladen',exact=True).click()
    download=pending.value;assert download.suggested_filename=='NX_STEP_'+job['id']+'.zip'
    path=tmp_path/'step.zip';download.save_as(path);assert hashlib.sha256(path.read_bytes()).hexdigest()==job['package_sha256']
    expect(page.locator('#file-download-state')).to_contain_text('Prüfsumme und Größe bestätigt');assert store.audit_entries()==before


def test_manifest_metadata_drift_after_receipt_does_not_publish_an_old_starter(ui):
    import json
    from playwright.sync_api import expect
    page,store,target,*_=ui;downloads=[];page.on('download',lambda value:downloads.append(value))
    held_starter(page,target)
    manifest=store.get_release(target['id'])['manifest']+[{'path':'synthetic-drift.txt','size':1,'sha256':'f'*64}]
    with store.db() as db:
        db.execute('UPDATE releases SET manifest=? WHERE id=?',(json.dumps(manifest),target['id']))
        store.audit(db,'fixture','synthetic.manifest-drift',{'id':target['id']})
    before=store.audit_entries();finish_held(page)
    expect(page.locator('#file-download-state')).to_contain_text('Quellmanifest hat sich geändert')
    assert downloads==[] and store.audit_entries()==before


@pytest.mark.parametrize('action',['cancel','logout'])
def test_mid_stream_cancel_closes_reader_and_never_publishes_a_partial_download(ui,action):
    from playwright.sync_api import expect
    page,store,target,*_=ui;before=store.audit_entries();downloads=[];page.on('download',lambda value:downloads.append(value))
    page.evaluate("""()=>{const original=fetch;window.__streamState={reads:0,cancelled:0,released:0};
      window.fetch=async(...args)=>{
        const response=await original(...args);if(!String(args[0]).endsWith('/test-launcher/package'))return response;
        window.__streamSignal=args[1].signal;const reader=response.body.getReader();let first=true;
        return {ok:true,status:200,headers:response.headers,body:{getReader(){return {
          async read(){window.__streamState.reads++;const chunk=await reader.read();if(first&&!chunk.done){first=false;window.__streamHeld=true;await new Promise(resolve=>{window.__releaseStream=resolve;});}return chunk;},
          async cancel(){window.__streamState.cancelled++;await reader.cancel().catch(()=>{});},
          releaseLock(){window.__streamState.released++;reader.releaseLock();}
        };}}};
      };
    }""")
    button=starter(page,target);button.evaluate('(element)=>{element.id="synthetic-stream-download";}');track(page,'synthetic-stream-download')
    button.click();page.wait_for_function('()=>window.__streamHeld')
    if action=='cancel':page.locator('#file-download-cancel').click()
    else:page.locator('#disconnect').click()
    assert page.evaluate('window.__streamSignal.aborted')
    page.evaluate('window.__releaseStream()');page.wait_for_function('()=>window.__oldActionFinished')
    assert page.evaluate('window.__streamState')=={'reads':1,'cancelled':1,'released':1}
    assert downloads==[] and store.audit_entries()==before
    if action=='cancel':expect(page.locator('#file-download-state')).to_contain_text('Download abgebrochen')
    else:expect(page.locator('#file-download-box')).to_be_hidden()

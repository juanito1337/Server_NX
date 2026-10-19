"""Lost raw result uploads/application replies: fake models in isolated Edge."""
import json
import os

import pytest

from nxserver.geometry import GeometryJobs
from nxserver.libraries import SPECS
from nxserver.results import Results
from test_geometry import STEP
from test_libraries import write
from test_results import table,bundle
from test_requests_browser import ui,hold,track,release
from test_saves_browser import lose

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated result jobs.')
TOKEN='isolated-requests-browser-'+'q'*40


def prepare(page,store):
    from playwright.sync_api import expect
    source=store.sources['test']
    write(source,SPECS['tool-metric']['path'],table(['LIBRF','DESCR','HLDREF'],[('STD','Stock','')]))
    write(source,SPECS['holder-metric']['path'],table(['LIBRF','DESCR'],[('STDH','Stock')]))
    target=store.import_release('test','2512','Prepared target','fixture');asset=store.add_asset('input.step',STEP,'fixture')
    tool=store.save_resource({'kind':'tool','name':'Isolated preparation','cam':{},'erp':None,'asset_ids':[asset['id']],
        'cam_status':'draft','geometry':{'mode':'step','status':'needs_preparation'}},'fixture')
    job=GeometryJobs(store).create(tool['id'],1,asset['id'],'drilling_solid','fixture');packet,_=bundle(job)
    page.on('dialog',lambda dialog:dialog.accept())
    page.locator('#refresh').click();expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    page.locator('#tab-prepare').click();page.locator('#step-resource').select_option(tool['id'])
    page.locator('#geometry-jobs').get_by_role('button',name='Ergebnispaket').click()
    page.locator('#result-key').fill('CUSTOM');page.locator('#result-file').set_input_files({'name':'prepared-result.zip','mimeType':'application/zip','buffer':packet})
    return target,tool,job,packet
def upload_ready(page,target):
    from playwright.sync_api import expect
    page.locator('#result-upload').click();expect(page.locator('#result-state')).to_contain_text('Paket SHA-geprüft gespeichert',timeout=20000)
    page.locator('#result-target').select_option(target['id']);page.locator('#result-preview').click();expect(page.locator('#result-apply')).to_be_enabled(timeout=20000)


def test_lost_upload_repeat_is_one_archive_and_preserves_later_fields(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;target,tool,job,packet=prepare(page,store)
    lose(page,'/api/v1/release-jobs/result_upload?geometry_job_id='+job['id']);page.locator('#result-upload').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost');expect(page.locator('#result-upload')).to_be_disabled()
    page.locator('#name').fill('Later unsaved title');page.locator('#result-key').fill('Later local LIBRF')
    page.locator('#tab-manage').click();page.locator('#release-job-repeat').click()
    expect(page.locator('#message')).to_contain_text('Paket SHA-geprüft gespeichert',timeout=20000)
    page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('Later unsaved title');expect(page.locator('#result-key')).to_have_value('Later local LIBRF')
    assert len(Results(store).list(job['id']))==1 and page.locator('#result-select').input_value()==''
    assert page.evaluate('sessionStorage.getItem("nx.pending-release-job.v1")') is None


def test_reload_ticket_contains_no_binary_or_cam_fields_and_recovers_upload_without_selection(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;target,tool,job,packet=prepare(page,store)
    lose(page,'/api/v1/release-jobs/result_upload?geometry_job_id='+job['id']);page.locator('#result-upload').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    raw=page.evaluate('sessionStorage.getItem("nx.pending-release-job.v1")');hint=json.loads(raw)
    assert set(hint)=={'contract_version','key','mode','created_at'} and hint['mode']=='result_upload'
    assert TOKEN not in raw and job['id'] not in raw and 'CUSTOM' not in raw and 'content' not in raw
    page.reload();page.locator('#token').fill(TOKEN);page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden')
    page.locator('#tab-manage').click()
    expect(page.locator('#release-job-repeat')).to_be_disabled();page.locator('#release-job-receipt').click()
    expect(page.locator('#message')).to_contain_text('Paket SHA-geprüft gespeichert',timeout=20000)
    page.locator('#tab-prepare').click();assert page.locator('#result-select').input_value()==''
    assert len(Results(store).list(job['id']))==1


def test_never_sent_upload_fence_prevents_a_late_binary_request(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;target,tool,job,packet=prepare(page,store)
    lose(page,'/api/v1/release-jobs/result_upload?geometry_job_id='+job['id'],before_send=True);page.locator('#result-upload').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost');page.locator('#tab-manage').click()
    key=page.locator('#release-job-key').inner_text();page.locator('#release-job-receipt').click()
    expect(page.locator('#message')).to_contain_text('in-flight request may still be accepted');page.locator('#release-job-fence').click()
    status=page.evaluate("""async([key,id,bytes,token])=>{const response=await fetch('/api/v1/release-jobs/result_upload?geometry_job_id='+id,{method:'POST',
      headers:{Authorization:'Bearer '+token,'Idempotency-Key':key,'Content-Type':'application/zip'},body:new Uint8Array(bytes)});return response.status;}""",[key,job['id'],list(packet),TOKEN])
    assert status==409 and Results(store).list(job['id'])==[]


def test_lost_adoption_reply_replay_keeps_two_drafts_and_later_editor_values(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;target,tool,job,packet=prepare(page,store);upload_ready(page,target)
    lose(page,'/api/v1/release-jobs/result_adopt');page.locator('#result-apply').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    page.locator('#name').fill('New editor after uncertain application');page.locator('#tab-manage').click();page.locator('#release-job-repeat').click()
    expect(page.locator('#message')).to_contain_text('neue/aktualisierte Entwürfe gespeichert',timeout=20000)
    page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('New editor after uncertain application')
    assert len(store.resources())==3 and next(r for r in store.resources() if r['id']==tool['id'])['revision']==2
    assert Results(store).list(job['id'])[0]['applied']


def test_old_upload_reply_after_new_session_never_overwrites_new_inputs(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;target,tool,job,packet=prepare(page,store)
    hold(page,'/api/v1/release-jobs/result_upload?geometry_job_id='+job['id']);track(page,'result-upload');page.locator('#result-upload').click();page.wait_for_function('()=>window.__held')
    page.locator('#disconnect').click();page.locator('#token').fill(TOKEN);page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden')
    page.locator('#release-job-receipt').click();expect(page.locator('#message')).to_contain_text('Paket SHA-geprüft gespeichert',timeout=20000)
    page.locator('#tab-prepare').click();page.locator('#name').fill('New session model input');release(page)
    expect(page.locator('#name')).to_have_value('New session model input');assert len(Results(store).list(job['id']))==1

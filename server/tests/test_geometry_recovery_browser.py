"""STEP job recovery uses isolated Edge, fake STEP text, never an NX session."""
import json
import os

import pytest

from nxserver.geometry import GeometryJobs
from test_geometry import STEP
from test_requests_browser import ui,hold,track,release
from test_saves_browser import lose

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated STEP request recovery.')
TOKEN='isolated-requests-browser-'+'q'*40


def prepare(page,store):
    from playwright.sync_api import expect
    asset=store.add_asset('Synthetic engineering input.step',STEP,'fixture')
    tool=store.save_resource({'kind':'tool','name':'Isolated STEP source','cam':{},'erp':None,'asset_ids':[asset['id']],
        'cam_status':'draft','geometry':{'mode':'step','status':'needs_preparation'}},'fixture')
    page.locator('#refresh').click();expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    page.locator('#tab-prepare').click();page.locator('#step-resource').select_option(tool['id'])
    expect(page.locator('#step-job-create')).to_be_enabled();return tool,asset


def test_lost_reply_exact_retry_keeps_single_job_and_later_selections(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;tool,_=prepare(page,store);lose(page,'/api/v1/geometry-jobs')
    page.locator('#step-job-create').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    expect(page.locator('#step-job-create')).to_be_disabled()
    page.locator('#step-tool-type').select_option('milling_square');page.locator('#name').fill('Typed after STEP reply lost')
    page.locator('#step-repeat').click();expect(page.locator('#message')).to_contain_text('Auftrag registriert')
    expect(page.locator('#step-tool-type')).to_have_value('milling_square');expect(page.locator('#name')).to_have_value('Typed after STEP reply lost')
    assert len(GeometryJobs(store).list(tool['id']))==1 and len(list((store.root/'geometry-packages').iterdir()))==1
    assert page.evaluate('sessionStorage.getItem("nx.pending-geometry-request.v1")') is None


def test_reload_minimal_hint_recovers_without_resetting_new_inputs(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;tool,asset=prepare(page,store);lose(page,'/api/v1/geometry-jobs')
    page.locator('#step-job-create').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    raw=page.evaluate('sessionStorage.getItem("nx.pending-geometry-request.v1")');ticket=json.loads(raw)
    assert set(ticket)=={'contract_version','key','created_at'}
    assert tool['id'] not in raw and asset['id'] not in raw and TOKEN not in raw and asset['filename'] not in raw
    page.reload();page.locator('#token').fill(TOKEN);page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden')
    page.locator('#tab-prepare').click();page.locator('#step-tool-type').select_option('milling_square');page.locator('#name').fill('Fresh reload editor')
    expect(page.locator('#step-repeat')).to_be_disabled();page.locator('#step-receipt').click()
    expect(page.locator('#message')).to_contain_text('Aktuelle Auswahl und Eingaben bleiben erhalten')
    expect(page.locator('#name')).to_have_value('Fresh reload editor');expect(page.locator('#step-tool-type')).to_have_value('milling_square')
    assert len(GeometryJobs(store).list(tool['id']))==1


def test_missing_receipt_then_fence_blocks_late_create_without_a_package(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;page.on('dialog',lambda dialog:dialog.accept());prepare(page,store);lose(page,'/api/v1/geometry-jobs',before_send=True)
    page.locator('#step-job-create').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    key=page.locator('#step-request-key').inner_text();body=json.loads(page.evaluate('window.__sentBody'))
    page.locator('#step-receipt').click();expect(page.locator('#message')).to_contain_text('in-flight request may still reserve')
    expect(page.locator('#step-job-create')).to_be_disabled();page.locator('#step-fence').click()
    expect(page.locator('#step-job-create')).to_be_enabled();assert GeometryJobs(store).list()==[]
    status=page.evaluate("""async([key,body,token])=>{const response=await fetch('/api/v1/geometry-jobs',{method:'POST',
      headers:{Authorization:'Bearer '+token,'Idempotency-Key':key,'Content-Type':'application/json'},body:JSON.stringify(body)});return response.status;}""",[key,body,TOKEN])
    assert status==409 and not (store.root/'geometry-packages').exists()


def test_old_create_reply_after_reconnect_does_not_restore_old_context(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;tool,_=prepare(page,store);hold(page,'/api/v1/geometry-jobs');track(page,'step-job-create')
    page.locator('#step-job-create').click();page.wait_for_function('()=>window.__held')
    page.locator('#disconnect').click();page.locator('#token').fill(TOKEN);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden');page.locator('#tab-prepare').click()
    page.locator('#step-tool-type').select_option('milling_square');page.locator('#name').fill('New session STEP editor')
    page.locator('#step-receipt').click();expect(page.locator('#message')).to_contain_text('Auftrag registriert')
    release(page);expect(page.locator('#name')).to_have_value('New session STEP editor');expect(page.locator('#step-tool-type')).to_have_value('milling_square')
    assert len(GeometryJobs(store).list(tool['id']))==1


def test_committed_but_changed_package_does_not_trigger_another_job_or_nx_approval(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;page.on('dialog',lambda dialog:dialog.accept());tool,_=prepare(page,store);lose(page,'/api/v1/geometry-jobs')
    page.locator('#step-job-create').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    job=GeometryJobs(store).list(tool['id'])[0];path=store.root/'geometry-packages'/job['id']/'NX_STEP.zip';path.write_bytes(b'Changed synthetic package')
    page.locator('#step-receipt').click();expect(page.locator('#message')).to_contain_text('package size changed')
    expect(page.locator('#step-ack')).to_be_enabled();expect(page.locator('#step-job-create')).to_be_disabled()
    page.locator('#step-ack').click();expect(page.locator('#step-job-create')).to_be_enabled()
    expect(page.locator('#message')).to_contain_text('keine Paket-/NX-Freigabe')
    assert len(GeometryJobs(store).list(tool['id']))==1 and path.read_bytes()==b'Changed synthetic package'
    assert next(r for r in store.resources() if r['id']==tool['id'])['cam_status']=='draft'

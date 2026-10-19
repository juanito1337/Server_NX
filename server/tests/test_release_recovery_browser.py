"""Lost approval/local-switch replies on disposable data and synthetic logs."""
import json
import os

import pytest

from nxserver.release_requests import get
from test_requests_browser import ui,hold,track,release
from test_saves_browser import lose

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated release receipt recovery.')
TOKEN='isolated-requests-browser-'+'q'*40


def ready(page,initial):
    from playwright.sync_api import expect
    page.locator('#releases tr').filter(has_text=initial['id']).get_by_role('button',name='Abnehmen',exact=True).click()
    raw=('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\n'+initial['folder']).encode()
    page.locator('#syslog-file').set_input_files({'name':'synthetic.syslog','mimeType':'text/plain','buffer':raw})
    page.locator('#syslog-upload').click();expect(page.locator('#syslog option:checked')).to_contain_text('synthetic.syslog')
    page.locator('#scan').click();expect(page.locator('#syslog-state')).to_contain_text('NX-Version: passend')
    for key in ('tools','machines','posts','simulation','fixtures','inspection'):page.locator('#'+key).check()
    page.locator('#evidence').fill('Synthetic private-ish statement; not real CAM qualification.')
    expect(page.locator('#approve')).to_be_enabled()


def local_button(page,initial):return page.locator('#releases tr').filter(has_text=initial['id']).get_by_role('button',name='Lokal aktivieren',exact=True)


def test_lost_approval_retry_is_original_commit_and_preserves_newer_text(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;ready(page,initial);lose(page,'/review')
    page.locator('#approve').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    assert store.get_release(initial['id'])['status']=='approved';before=store.audit_entries()
    expect(page.locator('#approve')).to_be_disabled();expect(local_button(page,initial)).to_be_disabled()
    page.locator('#evidence').fill('New unsubmitted notes typed after the lost reply')
    page.locator('#transition-repeat').click();expect(page.locator('#message')).to_contain_text('Historische Freigabe bestätigt')
    assert store.audit_entries()==before and store.active()['release_id'] is None
    expect(page.locator('#evidence')).to_have_value('New unsubmitted notes typed after the lost reply')
    expect(page.locator('#approve')).to_be_disabled()
    assert page.evaluate('sessionStorage.getItem("nx.pending-release-transition.v1")') is None


def test_reload_recovers_historical_approval_without_caching_evidence_or_activation(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;ready(page,initial);lose(page,'/review')
    page.locator('#approve').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    raw=page.evaluate('sessionStorage.getItem("nx.pending-release-transition.v1")');ticket=json.loads(raw)
    assert set(ticket)=={'contract_version','key','action','created_at'} and ticket['action']=='review'
    assert 'private-ish' not in raw and 'evidence' not in raw and TOKEN not in raw and initial['folder'] not in raw
    before=store.audit_entries();page.reload();page.locator('#token').fill(TOKEN);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden');expect(page.locator('#transition-repeat')).to_be_disabled()
    page.locator('#transition-receipt').click();expect(page.locator('#message')).to_contain_text('Heutigen Bestand separat neu geladen')
    assert store.audit_entries()==before and store.active()['release_id'] is None
    expect(page.locator('#review-box')).to_be_hidden()


def test_lost_activation_receipt_never_reactivates_a_newer_generation(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;page.on('dialog',lambda dialog:dialog.accept());ready(page,initial)
    page.locator('#approve').click();expect(page.locator('#message')).to_contain_text('Historische Freigabe bestätigt')
    lose(page,'/api/v1/active');local_button(page,initial).click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    assert store.active()['generation']==1;key=page.locator('#transition-key').inner_text()
    store.activate(initial['id'],1,'fixture');before=store.audit_entries()
    page.locator('#transition-repeat').click();expect(page.locator('#message')).to_contain_text('Historische lokale Aktivierung bestätigt')
    assert get(store,'admin',key)['result']['generation']==1 and store.active()['generation']==2 and store.audit_entries()==before
    assert page.evaluate('active.generation')==2  # Fresh workspace, not old receipt.


def test_no_receipt_is_not_stop_proof_and_fence_blocks_late_approval(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;page.on('dialog',lambda dialog:dialog.accept());ready(page,initial);lose(page,'/review',before_send=True)
    page.locator('#approve').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    key=page.locator('#transition-key').inner_text();payload=json.loads(page.evaluate('window.__sentBody'))
    page.locator('#transition-receipt').click();expect(page.locator('#message')).to_contain_text('in-flight request may still reserve')
    expect(page.locator('#approve')).to_be_disabled();page.locator('#transition-fence').click()
    expect(page.locator('#message')).to_contain_text('Noch offener finaler Commit gesperrt')
    status=page.evaluate('''async([key,payload,target,token])=>{const response=await fetch('/api/v1/releases/'+target+'/review',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json','Idempotency-Key':key},body:JSON.stringify(payload)});return response.status;}''',[key,payload,initial['id'],TOKEN])
    assert status==409 and store.get_release(initial['id'])['status']=='draft' and store.active()['release_id'] is None


def test_committed_receipt_can_be_acknowledged_if_current_workspace_read_fails(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;page.on('dialog',lambda dialog:dialog.accept());ready(page,initial);lose(page,'/review')
    page.locator('#approve').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    page.route('**/api/v1/workspace*',lambda route:route.fulfill(status=503,json={'detail':'Synthetic current workspace unavailable'}))
    page.locator('#transition-receipt').click();expect(page.locator('#message')).to_contain_text('Synthetic current workspace unavailable')
    expect(page.locator('#transition-ack')).to_be_enabled();expect(local_button(page,initial)).to_be_disabled()
    before=store.audit_entries();page.locator('#transition-ack').click()
    expect(page.locator('#message')).to_contain_text('Nur historischen Commit anerkannt')
    assert store.audit_entries()==before and store.active()['release_id'] is None
    expect(page.locator('#approve')).to_be_disabled()


def test_old_approval_reply_after_reconnect_cannot_overwrite_new_review(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;ready(page,initial);hold(page,'/review');track(page,'approve')
    page.locator('#approve').click();page.wait_for_function('()=>window.__held')
    page.locator('#disconnect').click();page.locator('#token').fill(TOKEN);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden')
    page.locator('#transition-receipt').click();expect(page.locator('#message')).to_contain_text('Historische Freigabe bestätigt')
    page.locator('#releases tr').filter(has_text=initial['id']).get_by_role('button',name='Abnehmen',exact=True).click()
    page.locator('#evidence').fill('New unsaved review after reconnect, not CAM proof');release(page)
    expect(page.locator('#evidence')).to_have_value('New unsaved review after reconnect, not CAM proof')
    expect(page.locator('#approve')).to_be_disabled();assert store.active()['release_id'] is None


def test_corrupt_ticket_blocks_approval_and_activation_without_mutation(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;before=store.audit_entries()
    page.evaluate('sessionStorage.setItem("nx.pending-release-transition.v1","not-json")')
    page.reload();page.locator('#token').fill(TOKEN);page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden')
    expect(local_button(page,initial)).to_be_disabled();expect(page.locator('#transition-state')).to_contain_text('unlesbar')
    assert store.audit_entries()==before and store.active()['release_id'] is None


@pytest.mark.parametrize('old_error',[False,True])
def test_late_transition_notice_never_overwrites_a_newer_operation_notice(ui,old_error):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;ready(page,initial);hold(page,'/review',error=old_error);track(page,'approve')
    page.locator('#approve').click();page.wait_for_function('()=>window.__held')
    page.evaluate('message("Later operation notice")');release(page)
    expect(page.locator('#message')).to_have_text('Later operation notice')
    assert store.get_release(initial['id'])['status']=='approved' and store.active()['release_id'] is None

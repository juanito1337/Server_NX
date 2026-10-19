"""System-log lost replies/recovery in isolated Edge; synthetic logs only."""
import json
import os

import pytest

from test_requests_browser import ui,hold,track,release
from test_saves_browser import lose

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated log upload recovery.')
TOKEN='isolated-requests-browser-'+'q'*40


def choose(page,initial,name='test.syslog'):
    page.locator('#releases tr').filter(has_text=initial['id']).get_by_role('button',name='Abnehmen',exact=True).click()
    raw=('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\n'+initial['folder']).encode()
    page.locator('#syslog-file').set_input_files({'name':name,'mimeType':'text/plain','buffer':raw})


def logs(store):
    with store.db() as db:return [dict(row) for row in db.execute('SELECT * FROM syslogs')]


def test_lost_upload_retry_is_one_protocol_and_no_approval(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;choose(page,initial);lose(page,'/api/v1/logs')
    page.locator('#syslog-upload').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    expect(page.locator('#syslog-upload')).to_be_disabled();expect(page.locator('#scan')).to_be_disabled()
    assert len(logs(store))==1
    page.locator('#evidence').fill('New unsaved human explanation, not CAM proof')
    before=store.audit_entries();page.locator('#log-repeat').click();expect(page.locator('#message')).to_contain_text('Datei-SHA geprüft')
    assert len(logs(store))==1 and store.audit_entries()==before and store.get_release(initial['id'])['status']=='draft'
    expect(page.locator('#evidence')).to_have_value('New unsaved human explanation, not CAM proof')
    assert page.locator('#syslog').input_value()==logs(store)[0]['id']
    assert page.evaluate('sessionStorage.getItem("nx.pending-log-upload.v1")') is None
    expect(page.locator('#approve')).to_be_disabled()


def test_reload_retains_only_ticket_and_requires_explicit_assignment(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;choose(page,initial,'Private-ish engineering label.syslog');lose(page,'/api/v1/logs')
    page.locator('#syslog-upload').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    raw=page.evaluate('sessionStorage.getItem("nx.pending-log-upload.v1")');ticket=json.loads(raw)
    assert set(ticket)=={'contract_version','key','created_at'} and 'engineering' not in raw and TOKEN not in raw and 'base64' not in raw
    page.reload();page.locator('#token').fill(TOKEN);page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden')
    expect(page.locator('#log-repeat')).to_be_disabled();page.locator('#log-receipt').click()
    expect(page.locator('#message')).to_contain_text('Nicht automatisch');expect(page.locator('#review-box')).to_be_hidden()
    assert len(logs(store))==1 and store.get_release(initial['id'])['status']=='draft'


def test_no_receipt_does_not_prove_stop_and_fence_blocks_late_upload(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;page.on('dialog',lambda dialog:dialog.accept());choose(page,initial);lose(page,'/api/v1/logs',before_send=True)
    page.locator('#syslog-upload').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    key=page.locator('#log-request-key').inner_text();payload=json.loads(page.evaluate('window.__sentBody'))
    page.locator('#log-receipt').click();expect(page.locator('#message')).to_contain_text('in-flight request may still reserve')
    expect(page.locator('#syslog-upload')).to_be_disabled();page.locator('#log-fence').click();expect(page.locator('#syslog-upload')).to_be_enabled()
    status=page.evaluate('''async([key,payload,token])=>{const response=await fetch('/api/v1/logs',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json','Idempotency-Key':key},body:JSON.stringify(payload)});return response.status;}''',[key,payload,TOKEN])
    assert status==409 and logs(store)==[]


def test_late_upload_confirmation_does_not_assign_to_new_review(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;choose(page,initial);hold(page,'/api/v1/logs');track(page,'syslog-upload')
    page.locator('#syslog-upload').click();page.wait_for_function('()=>window.__held')
    page.locator('#review-close').click();choose(page,initial,'newer-unsent.syslog')
    page.locator('#evidence').fill('Later review intent, not real CAM qualification')
    release(page);expect(page.locator('#message')).to_contain_text('Nicht automatisch')
    expect(page.locator('#syslog')).to_have_value('')
    expect(page.locator('#evidence')).to_have_value('Later review intent, not real CAM qualification')
    assert page.locator('#syslog-file').evaluate('(element)=>element.files[0].name')=='newer-unsent.syslog'
    assert len(logs(store))==1 and store.get_release(initial['id'])['status']=='draft'


def test_file_tampering_after_commit_is_not_qualified_and_can_acknowledge_metadata(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;page.on('dialog',lambda dialog:dialog.accept());choose(page,initial);lose(page,'/api/v1/logs')
    page.locator('#syslog-upload').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    log=logs(store)[0];path=store.root/'evidence'/log['id']/'nx.syslog';path.write_bytes(b'Externally changed synthetic protocol')
    page.locator('#log-receipt').click();expect(page.locator('#message')).to_contain_text('changed outside the API')
    expect(page.locator('#log-ack')).to_be_enabled();expect(page.locator('#approve')).to_be_disabled()
    page.locator('#log-ack').click();expect(page.locator('#message')).to_contain_text('Metadatencommit anerkannt')
    assert len(logs(store))==1 and path.read_bytes()==b'Externally changed synthetic protocol' and store.get_release(initial['id'])['status']=='draft'


def test_corrupt_ticket_blocks_upload_and_scan_without_server_mutation(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;before=store.audit_entries()
    page.evaluate('sessionStorage.setItem("nx.pending-log-upload.v1","not-json")')
    page.reload();page.locator('#token').fill(TOKEN);page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden')
    choose(page,initial);expect(page.locator('#syslog-upload')).to_be_disabled();expect(page.locator('#scan')).to_be_disabled()
    assert logs(store)==[] and store.audit_entries()==before


def test_old_upload_response_after_reconnect_cannot_overwrite_new_recovery(ui):
    from playwright.sync_api import expect
    page,store,initial,*_=ui;choose(page,initial);hold(page,'/api/v1/logs');track(page,'syslog-upload')
    page.locator('#syslog-upload').click();page.wait_for_function('()=>window.__held')
    page.locator('#disconnect').click();page.locator('#token').fill(TOKEN);page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden')
    page.locator('#log-receipt').click();expect(page.locator('#message')).to_contain_text('Nicht automatisch')
    choose(page,initial,'later-unsent.syslog');page.locator('#evidence').fill('Unsubmitted new review after reconnect')
    release(page);expect(page.locator('#evidence')).to_have_value('Unsubmitted new review after reconnect')
    assert len(logs(store))==1 and store.get_release(initial['id'])['status']=='draft'

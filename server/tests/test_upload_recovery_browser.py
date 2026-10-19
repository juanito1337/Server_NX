"""File-upload lost replies/partial integrity; fake bytes and isolated Edge only."""
import json
import os

import pytest

from test_requests_browser import ui,hold,track,release
from test_saves_browser import lose

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated upload recovery.')
TOKEN='isolated-requests-browser-'+'q'*40


def choose(page,name='Synthetic tool model.prt'):
    page.locator('#new').click();page.locator('#assetfile').set_input_files({'name':name,'mimeType':'application/octet-stream','buffer':b'Synthetic upload bytes; not a real model'})


def assets(store):
    with store.db() as db:return [dict(row) for row in db.execute('SELECT * FROM assets')]


def test_lost_upload_retry_is_one_asset_and_assigns_only_in_same_editor(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;choose(page);lose(page,'/api/v1/assets')
    page.locator('#upload').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    expect(page.locator('#upload')).to_be_disabled();page.locator('#name').fill('Typed after lost upload')
    before=store.audit_entries();page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Datei-Upload läuft oder ist noch ungeklärt')
    assert store.audit_entries()==before
    page.locator('#upload-repeat').click();expect(page.locator('#message')).to_contain_text('Datei gespeichert')
    expect(page.locator('#name')).to_have_value('Typed after lost upload');assert len(assets(store))==1
    assert json.loads(page.locator('#assetids').input_value())==[assets(store)[0]['id']]
    assert page.evaluate('sessionStorage.getItem("nx.pending-asset-upload.v1")') is None


def test_reload_minimal_ticket_recovers_without_assignment_to_new_editor(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;choose(page,'Engineering secret-ish label.prt');lose(page,'/api/v1/assets')
    page.locator('#upload').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    raw=page.evaluate('sessionStorage.getItem("nx.pending-asset-upload.v1")');ticket=json.loads(raw)
    assert set(ticket)=={'contract_version','key','created_at'}
    assert 'Engineering' not in raw and TOKEN not in raw and 'base64' not in raw
    page.reload();page.locator('#token').fill(TOKEN);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden');page.locator('#tab-prepare').click()
    page.locator('#name').fill('New unsaved editor after reload');expect(page.locator('#upload-repeat')).to_be_disabled()
    page.locator('#upload-receipt').click();expect(page.locator('#message')).to_contain_text('Nicht automatisch')
    expect(page.locator('#name')).to_have_value('New unsaved editor after reload')
    assert page.locator('#assetids').input_value()=='[]' and len(assets(store))==1


def test_missing_upload_receipt_is_not_stop_proof_and_fence_blocks_late_bytes(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;page.on('dialog',lambda dialog:dialog.accept());choose(page);lose(page,'/api/v1/assets',before_send=True)
    page.locator('#upload').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    key=page.locator('#upload-request-key').inner_text();payload=json.loads(page.evaluate('window.__sentBody'))
    page.locator('#upload-receipt').click();expect(page.locator('#message')).to_contain_text('in-flight request may still reserve')
    expect(page.locator('#upload')).to_be_disabled();page.locator('#upload-fence').click()
    expect(page.locator('#upload')).to_be_enabled();assert assets(store)==[]
    status=page.evaluate("""async([key,payload,token])=>{const response=await fetch('/api/v1/assets',{method:'POST',
      headers:{Authorization:'Bearer '+token,'Idempotency-Key':key,'Content-Type':'application/json'},body:JSON.stringify(payload)});return response.status;}""",[key,payload,TOKEN])
    assert status==409 and assets(store)==[]


def test_old_upload_response_after_reconnect_never_assigns_over_new_editor(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;choose(page);hold(page,'/api/v1/assets');track(page,'upload');page.locator('#upload').click()
    page.wait_for_function('()=>window.__held');page.locator('#disconnect').click();page.locator('#token').fill(TOKEN)
    page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden');page.locator('#tab-prepare').click()
    page.locator('#name').fill('New session unsaved name');page.locator('#upload-receipt').click()
    expect(page.locator('#message')).to_contain_text('Nicht automatisch');release(page)
    expect(page.locator('#name')).to_have_value('New session unsaved name')
    assert page.locator('#assetids').input_value()=='[]' and len(assets(store))==1


def test_known_commit_with_tampered_file_does_not_assign_or_delete_and_can_acknowledge_metadata(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;page.on('dialog',lambda dialog:dialog.accept());choose(page);lose(page,'/api/v1/assets')
    page.locator('#upload').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    asset=assets(store)[0];path=store.asset(asset['id'])[1];path.write_bytes(b'Changed isolated bytes')
    page.locator('#upload-receipt').click();expect(page.locator('#message')).to_contain_text('changed outside')
    expect(page.locator('#upload-ack')).to_be_enabled();expect(page.locator('#upload')).to_be_disabled()
    assert page.locator('#assetids').input_value()=='[]'
    page.locator('#upload-ack').click();expect(page.locator('#upload')).to_be_enabled()
    expect(page.locator('#message')).to_contain_text('keine Editorzuweisung');assert len(assets(store))==1 and path.read_bytes()==b'Changed isolated bytes'

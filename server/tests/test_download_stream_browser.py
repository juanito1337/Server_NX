"""Actual backup download UI with fake receivers and own synthetic archives."""
import json
import os

import pytest

from test_requests_browser import ui

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated streaming download Edge tests.')


@pytest.fixture
def download_ui(ui):
    from playwright.sync_api import expect
    page,store,release,resource=ui
    page.on('dialog',lambda dialog:dialog.accept())
    page.locator('#backup-create').click();expect(page.locator('#backup-state')).to_contain_text('Sicherung vollständig erstellt',timeout=20000)
    expect(page.locator('#backup-snapshots tr')).to_have_count(1)
    receipt=next(path for path in (store.root/'b').glob('*/receipt.json'))
    value=json.loads(receipt.read_text(encoding='utf-8'))
    page.evaluate('''()=>{window.__receiver={writes:0,bytes:0,closed:0,aborted:0,pickers:0,created:0};
      window.showSaveFilePicker=async()=>{window.__receiver.pickers++;return {async createWritable(){window.__receiver.created++;return {
        async write(value){window.__receiver.writes++;window.__receiver.bytes+=value.length;},
        async close(){window.__receiver.closed++;},async abort(){window.__receiver.aborted++;}
      };}};};}''')
    yield page,store,value


def button(page):return page.locator('#backup-snapshots tr').get_by_role('button',name='Archiv herunterladen')


def test_streamed_receiver_closes_only_after_sha_and_size_match(download_ui):
    from playwright.sync_api import expect
    page,store,receipt=download_ui;audits=store.audit_entries()
    button(page).click();expect(page.locator('#backup-state')).to_contain_text('Archiv gespeichert. Empfangs-SHA und Größe bestätigt')
    state=page.evaluate('window.__receiver')
    assert state['bytes']==receipt['archive_size'] and state['writes']>0 and state['closed']==1 and state['aborted']==0
    assert store.audit_entries()==audits and store.active()['release_id'] is None


@pytest.mark.parametrize('corrupt',['same_size','short','long'])
def test_corrupt_received_bytes_abort_without_publishing(download_ui,corrupt):
    from playwright.sync_api import expect
    page,store,receipt=download_ui
    def tamper(route):
        response=route.fetch();raw=response.body()
        raw=bytes([raw[0]^1])+raw[1:] if corrupt=='same_size' else raw[:-1] if corrupt=='short' else raw+b'x'
        route.fulfill(status=200,body=raw,headers={'Content-Type':'application/zip'})
    page.route('**/api/v1/backups/'+receipt['id']+'/download',tamper)
    button(page).click();expect(page.locator('#message')).to_have_class('error')
    expect(page.locator('#backup-state')).not_to_contain_text('Empfangs-SHA und Größe bestätigt')
    state=page.evaluate('window.__receiver');assert state['closed']==0 and state['aborted']==1
    assert store.active()['release_id'] is None


def test_network_or_disk_failure_never_closes_receiver(download_ui):
    from playwright.sync_api import expect
    page,store,receipt=download_ui
    page.evaluate('''()=>{window.showSaveFilePicker=async()=>({async createWritable(){return {
      async write(){throw new Error('Synthetic disk failure');},async close(){window.__receiver.closed++;},async abort(){window.__receiver.aborted++;}
    };}});}''')
    button(page).click();expect(page.locator('#message')).to_contain_text('Synthetic disk failure')
    assert page.evaluate('window.__receiver.closed')==0 and page.evaluate('window.__receiver.aborted')==1
    assert store.active()['release_id'] is None


def test_picker_is_single_owner_and_late_old_dialog_cannot_open_a_writer(download_ui):
    from playwright.sync_api import expect
    page,store,receipt=download_ui
    page.evaluate('''()=>{window.__pickerHandle={async createWritable(){window.__receiver.created++;return {async write(){},async close(){window.__receiver.closed++;},async abort(){window.__receiver.aborted++;}};}};
      window.showSaveFilePicker=()=>{window.__receiver.pickers++;return new Promise(resolve=>{window.__resolvePicker=()=>resolve(window.__pickerHandle);});};}''')
    button(page).click();page.wait_for_function('()=>typeof window.__resolvePicker==="function"')
    button(page).click();expect(page.locator('#message')).to_contain_text('Dateidialog läuft bereits')
    assert page.evaluate('window.__receiver.pickers')==1
    page.locator('#disconnect').click();page.evaluate('window.__resolvePicker()')
    expect(page.locator('#work')).to_be_hidden();assert page.evaluate('window.__receiver.created')==0
    assert store.active()['release_id'] is None


def test_logout_during_receiver_write_aborts_and_does_not_publish(download_ui):
    from playwright.sync_api import expect
    page,store,receipt=download_ui
    page.evaluate('''()=>{window.showSaveFilePicker=async()=>({async createWritable(){return {
      write(){return new Promise(resolve=>{window.__releaseWrite=resolve;});},async close(){window.__receiver.closed++;},async abort(){window.__receiver.aborted++;}
    };}});}''')
    button(page).click();page.wait_for_function('()=>typeof window.__releaseWrite==="function"')
    page.locator('#disconnect').click();page.evaluate('window.__releaseWrite()')
    expect(page.locator('#work')).to_be_hidden();assert page.evaluate('window.__receiver.closed')==0
    assert page.evaluate('window.__receiver.aborted')>=1
    assert store.active()['release_id'] is None


def test_missing_hash_component_does_not_publish_received_data(download_ui):
    from playwright.sync_api import expect
    page,store,receipt=download_ui;page.evaluate('window.jsSHA=undefined')
    button(page).click();expect(page.locator('#message')).to_contain_text('Komponente fehlt')
    assert page.evaluate('window.__receiver.closed')==0 and page.evaluate('window.__receiver.aborted')==1


def test_network_failure_never_opens_receiver(download_ui):
    from playwright.sync_api import expect
    page,store,receipt=download_ui
    page.route('**/api/v1/backups/'+receipt['id']+'/download',lambda route:route.abort('connectionfailed'))
    button(page).click();expect(page.locator('#message')).to_have_class('error')
    state=page.evaluate('window.__receiver');assert state['created']==state['closed']==0
    assert store.active()['release_id'] is None


def test_cancelled_save_dialog_does_not_request_or_open_archive(download_ui):
    from playwright.sync_api import expect
    page,store,receipt=download_ui;requests=[]
    page.on('request',lambda request:requests.append(request.url) if request.url.endswith('/download') else None)
    page.evaluate('()=>{window.showSaveFilePicker=async()=>{throw new DOMException("Synthetic user cancellation","AbortError");};}')
    button(page).click();expect(page.locator('#message')).to_contain_text('Synthetic user cancellation')
    assert not requests and page.evaluate('window.__receiver.created')==0


def test_close_failure_is_not_reported_as_saved(download_ui):
    from playwright.sync_api import expect
    page,store,receipt=download_ui
    page.evaluate('''()=>{window.showSaveFilePicker=async()=>({async createWritable(){return {
      async write(value){window.__receiver.bytes+=value.length;},async close(){throw new Error('Synthetic close failure');},async abort(){window.__receiver.aborted++;}
    };}});}''')
    button(page).click();expect(page.locator('#message')).to_contain_text('Synthetic close failure')
    expect(page.locator('#backup-state')).not_to_contain_text('Archiv gespeichert.')
    assert page.evaluate('window.__receiver.bytes')==receipt['archive_size'] and page.evaluate('window.__receiver.aborted')==1

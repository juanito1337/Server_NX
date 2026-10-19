"""Lost backup/restore acceptance uses isolated Edge and synthetic tiny data."""
import json
import os

import pytest

from nxserver.backups import Backups
from nxserver.store import Store
from test_requests_browser import ui,hold,track,release
from test_saves_browser import lose

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated backup request recovery.')
TOKEN='isolated-requests-browser-'+'q'*40


def setup(page):page.on('dialog',lambda dialog:dialog.accept())
def wait_snapshot(page):
    from playwright.sync_api import expect
    page.locator('#backup-create').click();expect(page.locator('#backup-state')).to_contain_text('Sicherung vollständig erstellt',timeout=20000)


def test_lost_backup_reply_exact_retry_has_one_worker_archive_and_keeps_new_target_name(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;setup(page);lose(page,'/api/v1/backups');page.locator('#backup-create').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    expect(page.locator('#backup-create')).to_be_disabled();page.locator('#recovery-name').fill('Typed after backup reply lost')
    page.locator('#backup-request-repeat').click();expect(page.locator('#backup-state')).to_contain_text('Sicherung vollständig erstellt',timeout=20000)
    expect(page.locator('#recovery-name')).to_have_value('Typed after backup reply lost')
    manager=Backups(Store(store.root));assert len(manager.jobs())==1 and len(manager.list())==1
    assert page.evaluate('sessionStorage.getItem("nx.pending-backup-request.v1")') is None


def test_reload_minimal_hint_then_receipt_never_recreates_backup(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;setup(page);lose(page,'/api/v1/backups');page.locator('#backup-create').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    raw=page.evaluate('sessionStorage.getItem("nx.pending-backup-request.v1")');ticket=json.loads(raw)
    assert set(ticket)=={'contract_version','key','mode','created_at'} and ticket['mode']=='backup'
    assert TOKEN not in raw and 'accept_legacy' not in raw and str(store.root) not in raw
    page.reload();page.locator('#token').fill(TOKEN);page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden')
    page.locator('#recovery-name').fill('Fresh reload target');expect(page.locator('#backup-request-repeat')).to_be_disabled()
    page.locator('#backup-request-receipt').click();expect(page.locator('#backup-state')).to_contain_text('Sicherung vollständig erstellt',timeout=20000)
    expect(page.locator('#recovery-name')).to_have_value('Fresh reload target')
    assert len(Backups(Store(store.root)).jobs())==1


def test_missing_acceptance_then_fence_blocks_late_worker(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;setup(page);lose(page,'/api/v1/backups',before_send=True);page.locator('#backup-create').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    key=page.locator('#backup-request-key').inner_text();payload=json.loads(page.evaluate('window.__sentBody'))
    page.locator('#backup-request-receipt').click();expect(page.locator('#message')).to_contain_text('in-flight request may still be accepted')
    expect(page.locator('#backup-create')).to_be_disabled();page.locator('#backup-request-fence').click()
    expect(page.locator('#backup-create')).to_be_enabled()
    status=page.evaluate("""async([key,payload,token])=>{const response=await fetch('/api/v1/backups',{method:'POST',
      headers:{Authorization:'Bearer '+token,'Idempotency-Key':key,'Content-Type':'application/json'},body:JSON.stringify(payload)});return response.status;}""",[key,payload,TOKEN])
    assert status==409 and Backups(Store(store.root)).jobs()==[]


def test_lost_restore_reply_retry_uses_same_target_without_second_copy(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;setup(page);wait_snapshot(page)
    manager=Backups(Store(store.root));backup=manager.list()[0]
    page.locator('#recovery-backup').select_option(backup['id']);page.locator('#recovery-name').fill('R')
    page.locator('#recovery-preview').click();expect(page.locator('#recovery-state')).to_contain_text('Geprüftes neues Ziel',timeout=20000)
    page.locator('#recovery-unsigned').check();lose(page,'/api/v1/backups/'+backup['id']+'/restore');page.locator('#recovery-create').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    page.locator('#recovery-name').fill('Later target choice');page.locator('#backup-request-repeat').click()
    expect(page.locator('#recovery-state')).to_contain_text('Isolierter Bestand erstellt',timeout=20000)
    expect(page.locator('#recovery-name')).to_have_value('Later target choice')
    assert (store.root.parent/'Recovered/R/management.sqlite3').is_file()
    assert not (store.root.parent/'Recovered/Later target choice').exists()
    assert len(Backups(Store(store.root)).jobs())==3


def test_old_acceptance_reply_after_reconnect_cannot_reset_new_session(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;setup(page);hold(page,'/api/v1/backups');track(page,'backup-create');page.locator('#backup-create').click()
    page.wait_for_function('()=>window.__held');page.locator('#disconnect').click();page.locator('#token').fill(TOKEN);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden');page.locator('#recovery-name').fill('New session target')
    page.locator('#backup-request-receipt').click();expect(page.locator('#backup-state')).to_contain_text('Sicherung vollständig erstellt',timeout=20000)
    release(page);expect(page.locator('#recovery-name')).to_have_value('New session target')
    assert len(Backups(Store(store.root)).jobs())==1


def test_lost_preview_with_away_and_back_inputs_never_arms_historical_restore(ui):
    from playwright.sync_api import expect
    page,store,*_=ui;setup(page);wait_snapshot(page);backup=Backups(Store(store.root)).list()[0]
    page.locator('#recovery-backup').select_option(backup['id']);page.locator('#recovery-name').fill('OriginalPlan')
    lose(page,'/api/v1/backups/'+backup['id']+'/restore-preview');page.locator('#recovery-preview').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    page.locator('#recovery-name').fill('OtherPlan');page.locator('#recovery-name').fill('OriginalPlan')
    page.locator('#backup-request-receipt').click();expect(page.locator('#recovery-state')).to_contain_text('Historischer Bericht',timeout=20000)
    page.locator('#recovery-unsigned').check();expect(page.locator('#recovery-create')).to_be_disabled()

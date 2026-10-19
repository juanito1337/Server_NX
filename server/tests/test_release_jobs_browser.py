"""Lost/background daily-export/import UI replies, synthetic installations only."""
import json
import os

import pytest

from test_upgrade_recovery_browser import wizard,TOKEN,jobs
from test_saves_browser import lose
from test_requests_browser import hold,track,release

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge folder jobs.')


def prepare_import(page):
    from playwright.sync_api import expect
    page.locator('#upgrade-mode').select_option('prepared');page.locator('#import-preview').click()
    expect(page.locator('#import-state')).to_contain_text('konfliktfrei',timeout=15000)
    expect(page.locator('#import')).to_be_enabled()


def initial_import(page):
    from playwright.sync_api import expect
    prepare_import(page);page.locator('#import').click()
    expect(page.locator('#message')).to_contain_text('Neuer Ordner erstellt',timeout=15000)
    expect(page.locator('#releases tr')).to_have_count(1)


def export_preview(page):
    from playwright.sync_api import expect
    page.locator('#releases').get_by_role('button',name='Neuen Export ableiten',exact=True).first.click()
    page.locator('#export-preview').click();expect(page.locator('#export-create')).to_be_enabled(timeout=15000)


@pytest.mark.parametrize('mode',['import','derive'])
def test_lost_folder_reply_reload_recovers_one_result_and_no_new_target(wizard,mode):
    from playwright.sync_api import expect
    page,store,source,target=wizard;before=store.manifest(source)
    if mode=='import':prepare_import(page);button='import';expected=1
    else:initial_import(page);export_preview(page);button='export-create';expected=2
    lose(page,'/api/v1/release-jobs/'+mode);page.locator('#'+button).click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    expect(page.locator('#upgrade-preview')).to_be_disabled()
    raw=page.evaluate('sessionStorage.getItem("nx.pending-release-job.v1")');hint=json.loads(raw)
    assert set(hint)=={'contract_version','key','mode','created_at'} and hint['mode']==mode
    assert str(source) not in raw and TOKEN not in raw and 'preview_hash' not in raw
    page.reload();page.locator('#token').fill(TOKEN);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden')
    page.locator('#tab-manage').click()
    expect(page.locator('#release-job-repeat')).to_be_disabled()
    page.locator('#release-job-receipt').click()
    expect(page.locator('#message')).to_contain_text('Neuer Ordner erstellt' if mode=='import' else 'Neuer NX-Datenstand aus geprüfter Exportvorschau',timeout=15000)
    assert len(store.releases())==expected and len(jobs(store))==expected and target.exists()
    assert store.manifest(source)==before and store.active()['release_id'] is None
    expect(page.locator('#release-job-list tr')).to_have_count(expected)
    page.locator('#tab-update').click();expect(page.locator('#upgrade-jobs tr')).to_have_count(0)
    assert page.evaluate('sessionStorage.getItem("nx.pending-release-job.v1")') is None


def test_exact_daily_export_retry_keeps_later_name_and_incomplete_defaults(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard;initial_import(page);export_preview(page)
    original=page.locator('#export-name').input_value();lose(page,'/api/v1/release-jobs/derive')
    page.locator('#export-create').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    page.locator('#export-name').fill('Not submitted new export name')
    page.locator('#export-box details.technical > summary').click();page.locator('#export-defaults').fill('{')
    page.locator('#release-job-repeat').click()
    expect(page.locator('#message')).to_contain_text('Neuer NX-Datenstand aus geprüfter Exportvorschau',timeout=15000)
    expect(page.locator('#export-box')).to_be_visible();expect(page.locator('#export-name')).to_have_value('Not submitted new export name')
    expect(page.locator('#export-defaults')).to_have_value('{');expect(page.locator('#export-create')).to_be_disabled()
    assert len(jobs(store))==2 and len(store.releases())==2 and any(r['name']==original for r in store.releases())


def test_missing_folder_acceptance_is_not_stop_proof_and_fence_blocks_late_post(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard;prepare_import(page);lose(page,'/api/v1/release-jobs/import',before_send=True)
    page.locator('#import').click();expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    key=page.locator('#release-job-key').inner_text();body=json.loads(page.evaluate('window.__sentBody'))
    page.locator('#tab-manage').click();page.locator('#release-job-receipt').click()
    expect(page.locator('#message')).to_contain_text('in-flight request may still be accepted')
    expect(page.locator('#release-job-recovery')).to_be_visible();assert not jobs(store) and not target.exists()
    page.locator('#release-job-fence').click();expect(page.locator('#release-job-recovery')).to_be_hidden()
    status=page.evaluate("""async([key,body,token])=>{const response=await fetch('/api/v1/release-jobs/import',{method:'POST',
      headers:{Authorization:'Bearer '+token,'Idempotency-Key':key,'Content-Type':'application/json'},body:JSON.stringify(body)});return response.status;}""",[key,body,TOKEN])
    assert status==409 and not jobs(store) and not store.releases() and not target.exists()


def test_old_export_acceptance_after_reconnect_does_not_reset_new_editor(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard;initial_import(page);export_preview(page)
    hold(page,'/api/v1/release-jobs/derive');track(page,'export-create');page.locator('#export-create').click()
    page.wait_for_function('()=>window.__held');page.locator('#disconnect').click();page.locator('#token').fill(TOKEN)
    page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden')
    page.locator('#new').click();page.locator('#name').fill('Unsaved new editor after reconnect')
    page.locator('#tab-manage').click();page.locator('#release-job-receipt').click()
    expect(page.locator('#message')).to_contain_text('Neuer NX-Datenstand aus geprüfter Exportvorschau',timeout=15000)
    release(page);page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('Unsaved new editor after reconnect')
    assert len(jobs(store))==2 and len(store.releases())==2 and store.resources()==[]

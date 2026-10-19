"""Recovery of native/file adoptions; only fake CAD/installation bytes and tokens."""
import json
import os

import pytest

from test_filepackages_browser import package_ui
from test_filepackages import packages
from test_upgrade_recovery_browser import wizard,TOKEN,jobs
from test_release_jobs_browser import initial_import
from test_management import ADMIN
from test_saves_browser import lose
from test_requests_browser import hold,track,release

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated adoption recovery.')


def package_preview(page,target):
    from playwright.sync_api import expect
    page.locator('#file-adopt-release').select_option(target['id']);page.locator('#file-adopt-kind').select_option('template')
    page.locator('#file-adopt-load').click();page.locator('#file-adopt-files input').check()
    page.locator('#file-adopt-preview').click();expect(page.locator('#file-adopt-apply')).to_be_enabled()


def catalog_preview(page):
    from playwright.sync_api import expect
    page.locator('#library-inventory').click()
    expect(page.locator('#catalog-libraries input')).to_have_count(1)
    page.locator('#include-standard').uncheck()
    page.locator('#catalog-preview').click();expect(page.locator('#catalog-import')).to_be_enabled()


def test_lost_catalog_reply_reload_recovers_committed_drafts_without_duplicate(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard;initial_import(page);catalog_preview(page)
    proposal=json.loads(page.locator('#details').inner_text());expected={item['id'] for item in proposal['objects']}
    assert len(expected)==proposal['count'] and len(expected)==2  # CUSTOM and the fixture's changed stock description.
    lose(page,'/api/v1/release-jobs/catalog');page.locator('#catalog-import').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    hint=json.loads(page.evaluate('sessionStorage.getItem("nx.pending-release-job.v1")'))
    assert set(hint)=={'contract_version','key','mode','created_at'} and hint['mode']=='catalog'
    page.reload();page.locator('#token').fill(TOKEN);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden');page.locator('#tab-manage').click()
    expect(page.locator('#release-job-repeat')).to_be_disabled();page.locator('#release-job-receipt').click()
    expect(page.locator('#message')).to_contain_text('Stammdaten übernommen',timeout=15000)
    assert {item['id'] for item in store.resources()}==expected
    assert all(item['cam_status']=='draft' and item['revision']==1 for item in store.resources()) and len(jobs(store))==2
    assert store.active()['release_id'] is None


def test_lost_file_adoption_exact_retry_does_not_duplicate_assets_or_replace_typed_editor(package_ui):
    from playwright.sync_api import expect
    page,store,target,source=package_ui;before=store.manifest(store.release_folder(target['id']))
    package_preview(page,target);lose(page,'/api/v1/release-jobs/file_adopt');page.locator('#file-adopt-apply').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    page.locator('#tab-prepare').click();page.locator('#name').fill('Typed while adoption outcome unknown')
    page.locator('#tab-manage').click();page.locator('#release-job-repeat').click()
    expect(page.locator('#message')).to_contain_text('Dateipaket als Entwurf übernommen',timeout=15000)
    page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('Typed while adoption outcome unknown')
    assert len(store.resources())==1 and store.resources()[0]['revision']==1
    with store.db() as db:assert db.execute('SELECT count(*) FROM assets').fetchone()[0]==1
    assert store.manifest(store.release_folder(target['id']))==before
    page.locator('#tab-manage').click();page.locator('#release-job-list').get_by_role('button',name='Aktuellen Datensatz öffnen').click()
    expect(page.locator('#kind')).to_have_value('template');expect(page.locator('#package-enabled')).to_be_checked()


def test_file_adoption_reload_can_recover_by_fence_without_cancelling_committed_resource(package_ui):
    from playwright.sync_api import expect
    page,store,target,source=package_ui;package_preview(page,target)
    lose(page,'/api/v1/release-jobs/file_adopt');page.locator('#file-adopt-apply').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    raw=page.evaluate('sessionStorage.getItem("nx.pending-release-job.v1")')
    assert ADMIN not in raw and str(source) not in raw and 'CUSTOM.prt' not in raw
    page.reload();page.locator('#token').fill(ADMIN);page.locator('#connect').click()
    expect(page.locator('#message')).to_contain_text('Verbunden');page.locator('#tab-manage').click()
    page.locator('#release-job-fence').click();expect(page.locator('#message')).to_contain_text('Dateipaket als Entwurf übernommen',timeout=15000)
    assert len(store.resources())==1 and store.resources()[0]['cam_status']=='draft'
    with store.db() as db:assert db.execute('SELECT count(*) FROM assets').fetchone()[0]==1
    assert page.evaluate('sessionStorage.getItem("nx.pending-release-job.v1")') is None


def test_never_sent_adoption_fence_blocks_late_post_with_no_assets_or_drafts(package_ui):
    from playwright.sync_api import expect
    page,store,target,source=package_ui;package_preview(page,target)
    lose(page,'/api/v1/release-jobs/file_adopt',before_send=True);page.locator('#file-adopt-apply').click()
    expect(page.locator('#message')).to_contain_text('Synthetic network reply lost')
    key=page.locator('#release-job-key').inner_text();body=json.loads(page.evaluate('window.__sentBody'))
    page.locator('#release-job-receipt').click();expect(page.locator('#message')).to_contain_text('in-flight request may still be accepted')
    page.locator('#release-job-fence').click();expect(page.locator('#release-job-recovery')).to_be_hidden()
    status=page.evaluate("""async([key,body,token])=>{const response=await fetch('/api/v1/release-jobs/file_adopt',{method:'POST',
      headers:{Authorization:'Bearer '+token,'Idempotency-Key':key,'Content-Type':'application/json'},body:JSON.stringify(body)});return response.status;}""",[key,body,ADMIN])
    assert status==409 and store.resources()==[]
    with store.db() as db:assert db.execute('SELECT count(*) FROM assets').fetchone()[0]==0


def test_old_adoption_reply_after_reconnect_cannot_open_over_new_editor(package_ui):
    from playwright.sync_api import expect
    page,store,target,source=package_ui;package_preview(page,target)
    hold(page,'/api/v1/release-jobs/file_adopt');track(page,'file-adopt-apply');page.locator('#file-adopt-apply').click()
    page.wait_for_function('()=>window.__held');page.locator('#disconnect').click();page.locator('#token').fill(ADMIN)
    page.locator('#connect').click();expect(page.locator('#message')).to_contain_text('Verbunden')
    page.locator('#new').click();page.locator('#name').fill('Unsaved editor in new session')
    page.locator('#tab-manage').click();page.locator('#release-job-receipt').click()
    expect(page.locator('#message')).to_contain_text('Dateipaket als Entwurf übernommen',timeout=15000)
    release(page);page.locator('#tab-prepare').click();expect(page.locator('#name')).to_have_value('Unsaved editor in new session')
    assert len(store.resources())==1 and store.resources()[0]['revision']==1

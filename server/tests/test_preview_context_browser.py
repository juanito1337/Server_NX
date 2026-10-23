"""Preview contexts, not byte-identical later inputs, control current GUI gates."""
import os

import pytest

from test_requests_browser import ui,hold,track,release as release_reply,payload
from test_upgrade_recovery_browser import wizard,jobs

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge preview context tests.')


def open_export(page,release):
    page.locator('#releases tr').filter(has_text=release['id']).get_by_role('button',name='Neuen Export ableiten',exact=True).click()
    page.locator('#export-box details.technical').evaluate('(element)=>{element.open=true;}')


@pytest.mark.parametrize('old_error',[False,True])
def test_export_defaults_aba_discards_old_preview_and_error(ui,old_error):
    from playwright.sync_api import expect
    page,store,initial,resource=ui;open_export(page,initial);before=store.audit_entries()
    hold(page,'/api/v1/releases/'+initial['id']+'/derive-preview',old_error);track(page,'export-preview')
    page.locator('#export-preview').click();page.wait_for_function('()=>window.__held')
    page.locator('#export-defaults').fill('{');page.locator('#export-defaults').fill('{}')
    release_reply(page)
    expect(page.locator('#export-create')).to_be_disabled()
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    expect(page.locator('#export-preview')).to_be_enabled()
    assert store.audit_entries()==before and len(store.releases())==1


def test_catalog_refresh_during_export_preview_does_not_arm_old_catalog(ui):
    from playwright.sync_api import expect
    page,store,initial,resource=ui;open_export(page,initial)
    hold(page,'/api/v1/releases/'+initial['id']+'/derive-preview');track(page,'export-preview')
    page.locator('#export-preview').click();page.wait_for_function('()=>window.__held')
    changed=payload(resource);changed['name']='New catalog revision during planning'
    store.save_resource(changed,'fixture',resource['id'],resource['revision'])
    page.locator('#refresh').click();expect(page.locator('#resources')).to_contain_text(changed['name'])
    release_reply(page)
    expect(page.locator('#export-create')).to_be_disabled()
    page.locator('#export-preview').click();expect(page.locator('#export-create')).to_be_enabled()
    assert store.resources()[0]['revision']==2 and len(store.releases())==1


@pytest.mark.parametrize('field',['defaults_aba','name_aba'])
def test_completed_export_does_not_close_a_later_export_context(ui,field):
    from playwright.sync_api import expect
    page,store,initial,resource=ui;page.on('dialog',lambda dialog:dialog.accept());open_export(page,initial)
    page.locator('#export-preview').click();expect(page.locator('#export-create')).to_be_enabled()
    hold(page,'/api/v1/release-jobs/derive');track(page,'export-create')
    page.locator('#export-create').click();page.wait_for_function('()=>window.__held')
    if field=='defaults_aba':
        page.locator('#export-defaults').fill('{');page.locator('#export-defaults').fill('{}')
    else:
        value=page.locator('#export-name').input_value();page.locator('#export-name').fill('Unsubmitted later name');page.locator('#export-name').fill(value)
    release_reply(page)
    expect(page.locator('#releases tr')).to_have_count(2,timeout=15000)
    expect(page.locator('#export-box')).to_be_visible();expect(page.locator('#export-create')).to_be_disabled()
    expect(page.locator('#export-preview')).to_be_enabled()
    assert store.resources()[0]['revision']==1 and store.active()['release_id'] is None


@pytest.mark.parametrize('change',['target_aba','mode_aba'])
def test_old_upgrade_report_cannot_rearm_changed_context(wizard,change):
    from playwright.sync_api import expect
    page,store,source,target=wizard
    hold(page,'/report');page.locator('#upgrade-preview').click();page.wait_for_function('()=>window.__held')
    if change=='target_aba':
        page.locator('#target-path').fill(str(target)+'_different');page.locator('#target-path').fill(str(target))
    else:
        page.locator('#upgrade-mode').select_option('prepared');page.locator('#upgrade-mode').select_option('upgrade')
    page.evaluate('window.__releaseHeld()');page.wait_for_function('()=>window.__decoded')
    expect(page.locator('#upgrade-state')).to_contain_text('Auswahl passt nicht mehr',timeout=15000)
    expect(page.locator('#upgrade-build')).to_be_disabled();expect(page.locator('#upgrade-preview')).to_be_enabled()
    assert len(jobs(store))==1 and not target.exists() and store.releases()==[]


def test_upgrade_completion_preserves_later_editor_and_workspace(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard
    page.locator('#upgrade-preview').click();expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    hold(page,'/api/v1/migrations/build');track(page,'upgrade-build')
    page.locator('#upgrade-build').click();page.wait_for_function('()=>window.__held')
    page.locator('#tab-prepare').click();page.locator('#editor-new').click();page.locator('#name').fill('Unsubmitted later editor')
    release_reply(page)
    expect(page.locator('#releases tr')).to_have_count(1,timeout=15000)
    expect(page.locator('#panel-prepare')).to_be_visible()
    expect(page.locator('#name')).to_have_value('Unsubmitted later editor')
    assert len(jobs(store))==2 and len(store.releases())==1 and store.resources()==[]
    assert store.active()['release_id'] is None
    expect(page.locator('#upgrade-build')).to_be_disabled()


def test_repeated_upgrade_preview_does_not_reuse_old_ready_report(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard
    page.locator('#upgrade-preview').click();expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    hold(page,'/report');page.locator('#upgrade-preview').click();page.wait_for_function('()=>window.__held')
    expect(page.locator('#upgrade-build')).to_be_disabled()
    page.evaluate('window.__releaseHeld()');page.wait_for_function('()=>window.__decoded')
    expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    assert len(jobs(store))==2 and not target.exists()


@pytest.mark.parametrize('old_error',[False,True])
def test_prerequisite_version_aba_discards_old_options_and_error(wizard,old_error):
    from playwright.sync_api import expect
    page,store,source,target=wizard
    hold(page,'/api/v1/migrations/options',old_error);track(page,'upgrade-check')
    page.locator('#upgrade-check').click();page.wait_for_function('()=>window.__held')
    page.locator('#nxversion').fill('2606');page.locator('#nxversion').fill('2512')
    release_reply(page)
    expect(page.locator('#upgrade-prerequisites')).to_contain_text('erneut prüfen')
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    page.locator('#upgrade-check').click()
    expect(page.locator('#upgrade-prerequisites')).to_contain_text('SIDT-Kopierprofile vorhanden')
    expect(page.locator('#upgrade-prerequisites')).to_contain_text('Installationsmetadaten: NX2406')
    expect(page.locator('#upgrade-prerequisites')).to_contain_text('NX2512')
    assert jobs(store)==[] and not target.exists()

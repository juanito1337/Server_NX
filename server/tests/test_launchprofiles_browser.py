"""Explicit UI-role wizard against disposable Edge/server fixtures."""
import json
import os

import pytest

from test_filepackages import packages,body,derive
from test_filepackages_browser import package_ui
from test_requests_browser import hold,release as release_reply,track

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge UI-role selection.')


def choose(package_ui):
    from playwright.sync_api import expect
    page,store,target,source=package_ui;path='UGII/menus/roles/COMPANY.mtx'
    resource=store.save_resource(body(store,'ui_standard',path,filename='COMPANY.mtx',content=b'<Role name="synthetic"/>'),'fixture')
    exported=derive(store,target['id']);page.locator('#refresh').click()
    expect(page.locator('#launch-profile-release option[value="'+exported['id']+'"]')).to_have_count(1)
    page.locator('#launch-profile-release').select_option(exported['id'])
    page.locator('#launch-profile-resource').select_option(resource['id'])
    expect(page.locator('#launch-profile-path')).to_have_value(path)
    return page,store,exported,resource,path


def test_gui_previews_prepares_and_downloads_only_the_selected_role(package_ui):
    from playwright.sync_api import expect
    page,store,target,resource,path=choose(package_ui);before=store.manifest(store.release_folder(target['id']))
    page.locator('#launch-profile-preview').click();expect(page.locator('#launch-profile-apply')).to_be_enabled()
    report=json.loads(page.locator('#launch-profile-report').inner_text());assert report['ui_profile']['path']==path
    page.locator('#launch-profile-apply').click();expect(page.locator('#launch-profile-download')).to_be_enabled()
    expect(page.locator('#launch-profile-state')).to_contain_text('UI-Testpaket erstellt')
    assert store.get_release(target['id'])['test_launcher']['ui_profile']['resource_id']==resource['id']
    with page.expect_download() as download:page.locator('#launch-profile-download').click()
    assert download.value.suggested_filename.startswith('NX2512_UI_Test_')
    assert store.manifest(store.release_folder(target['id']))==before and store.active()['release_id'] is None


@pytest.mark.parametrize('field',['contract_version','ui_profile','context_sha256','automatic_nx_start'])
def test_malformed_profile_contract_cannot_enable_apply(package_ui,field):
    from playwright.sync_api import expect
    page,store,target,resource,path=choose(package_ui);audit=len(store.audit_entries())
    def bad(route):
        response=route.fetch();value=response.json()
        if field=='automatic_nx_start':value[field]=True
        else:value.pop(field)
        route.fulfill(response=response,json=value)
    page.route('**/ui-profile-preview',bad);page.locator('#launch-profile-preview').click()
    expect(page.locator('#launch-profile-state')).to_contain_text('Kein vollständiger gebundener UI-Rollenvertrag')
    expect(page.locator('#launch-profile-apply')).to_be_disabled();assert len(store.audit_entries())==audit


def test_selection_change_and_logout_discard_late_preview(package_ui):
    from playwright.sync_api import expect
    page,store,target,resource,path=choose(package_ui)
    hold(page,'/ui-profile-preview')
    track(page,'launch-profile-preview')
    page.locator('#launch-profile-preview').click();page.wait_for_function('()=>window.__held')
    page.locator('#launch-profile-resource').select_option('')
    release_reply(page);expect(page.locator('#launch-profile-apply')).to_be_disabled()
    expect(page.locator('#launch-profile-report')).to_have_text('')
    page.locator('#launch-profile-resource').select_option(resource['id']);hold(page,'/ui-profile-preview')
    track(page,'launch-profile-preview')
    page.locator('#launch-profile-preview').click();page.wait_for_function('()=>window.__held')
    page.locator('#disconnect').click();release_reply(page)
    expect(page.locator('#launch-profile-apply')).to_be_disabled();expect(page.locator('#launch-profile-report')).to_have_text('')
    assert store.get_release(target['id'])['test_launcher'].get('ui_profile') is None


def test_same_selection_aba_does_not_accept_old_preview(package_ui):
    from playwright.sync_api import expect
    page,store,target,resource,path=choose(package_ui)
    hold(page,'/ui-profile-preview');track(page,'launch-profile-preview')
    page.locator('#launch-profile-preview').click();page.wait_for_function('()=>window.__held')
    page.locator('#launch-profile-resource').select_option('');page.locator('#launch-profile-resource').select_option(resource['id'])
    release_reply(page);expect(page.locator('#launch-profile-apply')).to_be_disabled()
    page.locator('#launch-profile-preview').click();expect(page.locator('#launch-profile-apply')).to_be_enabled()


@pytest.mark.parametrize('change',['selection','logout'])
def test_late_success_does_not_bind_package_to_a_new_selection_or_session(package_ui,change):
    from playwright.sync_api import expect
    page,store,target,resource,path=choose(package_ui)
    page.locator('#launch-profile-preview').click();expect(page.locator('#launch-profile-apply')).to_be_enabled()
    hold(page,'/ui-profile');track(page,'launch-profile-apply')
    page.locator('#launch-profile-apply').click();page.wait_for_function('()=>window.__held')
    if change=='selection':page.locator('#launch-profile-resource').select_option('')
    else:page.locator('#disconnect').click()
    release_reply(page);expect(page.locator('#launch-profile-download')).to_be_disabled()
    assert store.get_release(target['id'])['test_launcher']['ui_profile']['resource_id']==resource['id']
    assert store.active()['release_id'] is None


def prepared(package_ui):
    from playwright.sync_api import expect
    page,store,target,resource,path=choose(package_ui)
    page.locator('#launch-profile-preview').click();expect(page.locator('#launch-profile-apply')).to_be_enabled()
    page.locator('#launch-profile-apply').click();expect(page.locator('#launch-profile-download')).to_be_enabled()
    return page,store,target,resource,path


def test_catalog_revision_change_sperrs_old_role_download(package_ui):
    from playwright.sync_api import expect
    page,store,target,resource,path=prepared(package_ui)
    value={key:resource[key] for key in ('kind','name','cam','erp','cam_status','asset_ids','file_package')};value['name']='Newer UI revision'
    store.save_resource(value,'fixture',resource['id'],1);page.locator('#refresh').click()
    expect(page.locator('#launch-profile-state')).to_contain_text('Bestand geändert')
    expect(page.locator('#launch-profile-download')).to_be_disabled()
    assert store.get_release(target['id'])['test_launcher']['ui_profile']['revision']==1


@pytest.mark.parametrize('change',['selection','logout'])
def test_late_package_download_does_not_save_after_context_change(package_ui,change):
    from playwright.sync_api import expect
    page,store,target,resource,path=prepared(package_ui);downloads=[];page.on('download',lambda value:downloads.append(value))
    hold(page,'/test-launcher/package');track(page,'launch-profile-download')
    page.locator('#launch-profile-download').click();page.wait_for_function('()=>window.__held')
    if change=='selection':page.locator('#launch-profile-resource').select_option('')
    else:page.locator('#disconnect').click()
    page.evaluate('window.__releaseHeld()');page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#launch-profile-download')).to_be_disabled();assert downloads==[]

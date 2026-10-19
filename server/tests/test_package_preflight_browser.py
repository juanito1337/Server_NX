"""Technical package preview and hash binding on disposable fixtures only."""
import json
import os

import pytest

from nxserver.integrity import snapshot
from nxserver.libraries import digest
from test_package_attachments_browser import editor
from test_filepackages import packages
from test_filepackages_browser import package_ui
from test_requests_browser import hold,track,release as release_reply

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated technical package preview.')


def test_technical_preview_never_assigns_qualification_but_explicit_binding_does(package_ui):
    from playwright.sync_api import expect
    page,store,*_=package_ui;resource=editor(page,store);before=snapshot(store)['sha256'];original=page.evaluate('NXFilePackages.fields()')
    page.locator('#package-preflight').click();expect(page.locator('#package-preflight-state')).to_contain_text('Technische Paketprüfung bestanden')
    expect(page.locator('#package-preflight-files tr')).to_have_count(1)
    assert page.evaluate('NXFilePackages.fields()')==original and snapshot(store)['sha256']==before
    page.locator('#package-bind').click();expect(page.locator('#package-state')).to_contain_text('Dateihashes gebunden')
    assert page.evaluate('NXFilePackages.fields().file_package.asset_hashes')==resource['file_package']['asset_hashes']
    for key in ('paths','content','dependencies','nx_test'):expect(page.locator('#package-'+key)).not_to_be_checked()
    assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('path',['UGII/templates/RENAMED.prt','W:/SiemensShare/nx2406/CUSTOM.prt'])
def test_bad_path_has_visible_errors_and_cannot_bind_hashes(package_ui,path):
    from playwright.sync_api import expect
    page,store,*_=package_ui;editor(page,store);before=snapshot(store)['sha256']
    page.locator('#package-entries input').fill(path);page.locator('#package-preflight').click()
    expect(page.locator('#package-preflight-state')).to_contain_text('technische Fehler')
    expect(page.locator('#package-preflight-errors')).not_to_be_empty()
    page.locator('#package-bind').click();expect(page.locator('#message')).to_contain_text('Technische Paketprüfung enthält Fehler')
    assert page.evaluate('NXFilePackages.fields().file_package.asset_hashes')=={} and snapshot(store)['sha256']==before


@pytest.mark.parametrize('fault',['flags','hash','input','file','bytes','xml','summary','errors'])
def test_invalid_technical_reply_cannot_bind_or_keep_verified_rows(package_ui,fault):
    from playwright.sync_api import expect
    page,store,*_=package_ui;editor(page,store);before=snapshot(store)['sha256']
    page.locator('#package-preflight').click();expect(page.locator('#package-preflight-state')).to_contain_text('Technische Paketprüfung bestanden')
    def invalid(route):
        response=route.fetch();value=response.json()
        if fault=='flags':value['nx_validation']=True
        elif fault=='hash':value['preview_hash']='0'*64
        elif fault=='input':value['request_sha256']='0'*64
        elif fault=='file':value['files'][0]['asset_id']='00000000-0000-0000-0000-000000000000'
        elif fault=='bytes':value['files'][0]['bytes_verified']=False
        elif fault=='xml':value['files'][0]['xml_checked']=True
        elif fault=='summary':value['total_bytes']+=1
        else:value['errors']=[{'index':None,'path':None,'code':'open','reason':'Not safe'}]
        if fault in ('bytes','xml','summary','errors'):
            unsigned=dict(value);unsigned.pop('preview_hash');value['preview_hash']=digest(unsigned)
        route.fulfill(response=response,json=value)
    page.route('**/file-packages/preflight',invalid);page.locator('#package-bind').click()
    expect(page.locator('#message')).to_contain_text('Keine vollständige gebundene technische Paketprüfung')
    expect(page.locator('#package-preflight-files tr')).to_have_count(0)
    assert page.evaluate('NXFilePackages.fields().file_package.asset_hashes')=={} and snapshot(store)['sha256']==before


@pytest.mark.parametrize('change',['path_aba','version_aba','new_editor','logout'])
def test_delayed_technical_check_cannot_return_to_changed_editor(package_ui,change):
    from playwright.sync_api import expect
    page,store,*_=package_ui;resource=editor(page,store);before=snapshot(store)['sha256']
    hold(page,'/file-packages/preflight');track(page,'package-preflight');page.locator('#package-preflight').click();page.wait_for_function('()=>window.__held')
    if change=='path_aba':
        field=page.locator('#package-entries input');value=field.input_value();field.fill('UGII/templates/OTHER.prt');field.fill(value)
    elif change=='version_aba':page.locator('#package-version').fill('2406');page.locator('#package-version').fill('2512')
    elif change=='new_editor':page.locator('#editor-new').click();page.locator('#name').fill('Keep later draft')
    else:page.locator('#disconnect').click()
    release_reply(page);expect(page.locator('#package-preflight-files tr')).to_have_count(0)
    expect(page.locator('#package-preflight-state')).not_to_contain_text('Technische Paketprüfung bestanden')
    assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('change',['workspace','details'])
def test_late_technical_reply_keeps_a_later_detail_view(package_ui,change):
    from playwright.sync_api import expect
    page,store,*_=package_ui;editor(page,store);before=snapshot(store)['sha256']
    hold(page,'/file-packages/preflight');track(page,'package-preflight');page.locator('#package-preflight').click();page.wait_for_function('()=>window.__held')
    if change=='workspace':page.locator('#tab-manage').click()
    page.evaluate("details({module:'later_user_inspection',value:'keep this view'})")
    release_reply(page);expect(page.locator('#details')).to_contain_text('later_user_inspection')
    expect(page.locator('#package-preflight-state')).to_contain_text('Technische Paketprüfung bestanden')
    assert snapshot(store)['sha256']==before

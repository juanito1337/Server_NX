"""Attachment changes must not leave stale file-package qualification in the UI."""
import json
import os

import pytest

from test_filepackages import packages,body
from test_filepackages_browser import package_ui
from test_requests_browser import hold,track,release as release_reply

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge package attachment tests.')


def editor(page,store):
    from playwright.sync_api import expect
    resource=store.save_resource(body(store,status='draft'),'fixture')
    page.locator('#refresh').click();expect(page.locator('#resources tr')).to_have_count(1)
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten').click()
    expect(page.locator('#attachments tr')).to_have_count(1)
    page.locator('#editor-section details.technical').evaluate('(element)=>{element.open=true;}')
    return resource


@pytest.mark.parametrize('restore_ids',[False,True])
@pytest.mark.parametrize('old_error',[False,True])
def test_pending_hash_cannot_rebind_after_attachment_input_or_aba(package_ui,restore_ids,old_error):
    from playwright.sync_api import expect
    page,store,*_=package_ui;resource=editor(page,store);before=store.audit_entries()
    entries=page.evaluate('NXFilePackages.fields().file_package.entries')
    hold(page,'/api/v1/assets/'+resource['asset_ids'][0]+'/metadata',old_error);track(page,'package-bind')
    page.locator('#package-bind').click();page.wait_for_function('()=>window.__held')
    page.locator('#assetids').fill('[]')
    if restore_ids:page.locator('#assetids').fill(json.dumps(resource['asset_ids']))
    release_reply(page)
    assert page.evaluate('NXFilePackages.fields().file_package.asset_hashes')=={}
    assert page.evaluate('NXFilePackages.fields().file_package.entries')==entries
    for check in ('paths','dependencies','content','nx_test'):expect(page.locator('#package-'+check)).not_to_be_checked()
    expect(page.locator('#package-state')).not_to_contain_text('Dateihashes gebunden')
    expect(page.locator('#message')).not_to_contain_text('Old delayed error')
    assert store.audit_entries()==before and store.resources()[0]['revision']==1


def test_pending_asset_list_cannot_return_after_attachment_aba(package_ui):
    from playwright.sync_api import expect
    page,store,*_=package_ui;resource=editor(page,store)
    hold(page,'/api/v1/assets/'+resource['asset_ids'][0]+'/metadata');track(page,'package-load-assets')
    page.locator('#package-load-assets').click();page.wait_for_function('()=>window.__held')
    page.locator('#assetids').fill('[]');page.locator('#assetids').fill(json.dumps(resource['asset_ids']))
    release_reply(page)
    expect(page.locator('#package-asset option')).to_have_count(0)
    assert store.resources()[0]['revision']==1


def test_hash_binding_requires_every_package_entry_still_attached(package_ui):
    from playwright.sync_api import expect
    page,store,*_=package_ui;resource=editor(page,store);before=store.audit_entries();requests=[]
    page.locator('#assetids').fill('[]')
    page.on('request',lambda request:requests.append(request.url) if request.url.endswith('/metadata') else None)
    page.locator('#package-bind').click()
    expect(page.locator('#message')).to_contain_text('Paketdateien sind nicht mehr zugeordnet')
    assert not requests and page.evaluate('NXFilePackages.fields().file_package.asset_hashes')=={}
    assert page.evaluate('NXFilePackages.fields().file_package.entries')[0]['asset_id']==resource['asset_ids'][0]
    assert store.audit_entries()==before


@pytest.mark.parametrize('raw',['[','{}','["not-a-uuid"]'])
def test_incomplete_or_invalid_ids_are_preserved_and_cannot_bind(package_ui,raw):
    from playwright.sync_api import expect
    page,store,*_=package_ui;editor(page,store);before=store.audit_entries();requests=[]
    page.locator('#assetids').fill(raw)
    page.on('request',lambda request:requests.append(request.url) if request.url.endswith('/metadata') else None)
    page.locator('#package-bind').click();expect(page.locator('#message')).to_contain_text('Dateianhang-IDs')
    expect(page.locator('#assetids')).to_have_value(raw)
    assert not requests and page.evaluate('NXFilePackages.fields().file_package.asset_hashes')=={}
    expect(page.locator('#package-entries')).to_contain_text('Anhangsliste unlesbar')
    assert store.audit_entries()==before


def test_remove_and_reassign_preserve_package_paths_but_require_new_binding(package_ui):
    from playwright.sync_api import expect
    page,store,*_=package_ui;resource=editor(page,store);before=store.audit_entries()
    entries=page.evaluate('NXFilePackages.fields().file_package.entries')
    page.locator('#package-load-assets').click();expect(page.locator('#package-asset option')).to_have_count(1)
    page.locator('#attachments tr').get_by_role('button',name='Zuordnung entfernen').click()
    expect(page.locator('#package-entries')).to_contain_text('Nicht mehr im Editor zugeordnet')
    expect(page.locator('#package-asset option')).to_have_count(0)
    assert page.evaluate('NXFilePackages.fields().file_package.entries')==entries
    page.locator('#asset-library').evaluate('(element)=>{element.open=true;}')
    page.locator('#asset-library-load').click();expect(page.locator('#asset-library-items tr')).to_have_count(1)
    page.locator('#asset-library-items tr').get_by_role('button',name='Zuordnen',exact=True).click()
    expect(page.locator('#attachments tr')).to_have_count(1)
    assert json.loads(page.locator('#assetids').input_value())==resource['asset_ids']
    assert page.evaluate('NXFilePackages.fields().file_package.asset_hashes')=={}
    page.locator('#package-bind').click();expect(page.locator('#package-state')).to_contain_text('Dateihashes gebunden')
    for check in ('paths','dependencies','content','nx_test'):expect(page.locator('#package-'+check)).not_to_be_checked()
    assert page.evaluate('NXFilePackages.fields().file_package.entries')==entries
    assert store.audit_entries()==before and store.resources()[0]['revision']==1


def test_upload_during_hash_check_cannot_restore_old_package_qualification(package_ui):
    from playwright.sync_api import expect
    page,store,*_=package_ui;resource=editor(page,store)
    entries=page.evaluate('NXFilePackages.fields().file_package.entries')
    hold(page,'/api/v1/assets/'+resource['asset_ids'][0]+'/metadata');track(page,'package-bind')
    page.locator('#package-bind').click();page.wait_for_function('()=>window.__held')
    page.locator('#assetfile').set_input_files({'name':'EXTRA.prt','mimeType':'application/octet-stream','buffer':b'Synthetic attachment; not a CAD model'})
    page.locator('#upload').click();expect(page.locator('#message')).to_contain_text('Datei gespeichert')
    release_reply(page)
    assert len(json.loads(page.locator('#assetids').input_value()))==2
    assert page.evaluate('NXFilePackages.fields().file_package.asset_hashes')=={}
    assert page.evaluate('NXFilePackages.fields().file_package.entries')==entries
    for check in ('paths','dependencies','content','nx_test'):expect(page.locator('#package-'+check)).not_to_be_checked()
    assert store.resources()[0]['revision']==1 and len(store.asset_inventory()['items'])==2


def test_package_rows_scroll_locally_without_mobile_page_overflow(package_ui):
    from playwright.sync_api import expect
    page,store,*_=package_ui;editor(page,store)
    page.set_viewport_size({'width':390,'height':844})
    expect(page.locator('#file-package-editor')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'),page.evaluate('''()=>Array.from(document.querySelectorAll('fieldset,.scroll,.grid')).filter(element=>element.getBoundingClientRect().right>innerWidth).map(element=>({id:element.id,tag:element.tagName,right:element.getBoundingClientRect().right,width:element.getBoundingClientRect().width}))''')
    expect(page.locator('#package-entries tr')).to_have_count(1)

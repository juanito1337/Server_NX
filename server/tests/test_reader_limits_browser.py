"""Visible package XML rejection/correction, synthetic isolated Edge fixtures."""
import os

import pytest

from test_filepackages_browser import package_ui
from test_filepackages import body,packages

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated package reader limits.')


@pytest.mark.parametrize('case',['internal_dtd','external_dtd','nodes','depth'])
def test_bad_existing_role_xml_is_a_visible_adoption_conflict(package_ui,case):
    from playwright.sync_api import expect
    page,store,_,source=package_ui;path='UGII/menus/roles/FIRM.mtx'
    raw={'internal_dtd':b'<!DOCTYPE Role [<!ENTITY test "synthetic">]><Role>&test;</Role>',
         'external_dtd':b'<!DOCTYPE Role SYSTEM "https://invalid.example/no-fetch.dtd"><Role/>',
         'nodes':b'<Role>'+b'<Entry/>'*50000+b'</Role>',
         'depth':b'<Role>'+b'<Entry>'*65+b'</Entry>'*65+b'</Role>'}[case]
    (source/path).write_bytes(raw)
    target=store.import_release('test','2512','Synthetic bad role XML','fixture')
    before=store.audit_entries();manifest=store.manifest(store.release_folder(target['id']))
    page.locator('#refresh').click()
    expect(page.locator('#file-adopt-release option[value="'+target['id']+'"]')).to_have_count(1)
    page.locator('#file-adopt-release').select_option(target['id']);page.locator('#file-adopt-kind').select_option('ui_standard')
    page.locator('#file-adopt-load').click();page.locator('#file-adopt-files input').check()
    page.locator('#file-adopt-preview').click()
    expect(page.locator('#file-adopt-state')).to_contain_text('Package XML')
    expect(page.locator('#file-adopt-apply')).to_be_disabled()
    assert store.audit_entries()==before and store.resources()==[] and store.active()['release_id'] is None
    assert store.manifest(store.release_folder(target['id']))==manifest


def test_invalid_package_save_preserves_inputs_and_corrected_asset_can_be_saved(package_ui):
    from playwright.sync_api import expect
    page,store,target,_=package_ui;path='UGII/menus/roles/CUSTOM.mtx'
    value=body(store,'ui_standard',path,status='draft',filename='CUSTOM.mtx',
        content=b'<!DOCTYPE Role [<!ENTITY test "synthetic">]><Role>&test;</Role>')
    value['file_package']=None
    resource=store.save_resource(value,'fixture');before=store.manifest(store.release_folder(target['id']))
    page.locator('#refresh').click();expect(page.locator('#resources tr')).to_have_count(1)
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten',exact=True).click()
    page.locator('#package-enabled').check();page.locator('#package-load-assets').click()
    expect(page.locator('#package-asset option')).to_have_count(1)
    page.locator('#package-root').select_option('UGII/menus/roles/');page.locator('#package-add').click()
    page.locator('#name').fill('Unsaved role package correction');page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Package XML')
    expect(page.locator('#save')).to_be_enabled()
    expect(page.locator('#name')).to_have_value('Unsaved role package correction')
    expect(page.locator('#package-entries')).to_contain_text('CUSTOM.mtx')
    assert next(item for item in store.resources() if item['id']==resource['id'])['revision']==1
    assert page.evaluate('sessionStorage.getItem("nx.pending-resource-write.v1")') is None
    corrected=store.add_asset('CUSTOM.mtx',b'<Role name="synthetic corrected"/>','fixture')
    page.locator('#package-entries').get_by_role('button',name='Zuordnung entfernen').click()
    page.locator('#asset-library > summary').click();page.locator('#asset-query').fill('CUSTOM.mtx')
    page.locator('#asset-library-load').click();expect(page.locator('#asset-library-items tr')).to_have_count(2)
    page.locator('#asset-library-items').get_by_role('button',name='Zuordnen',exact=True).click()
    expect(page.locator('#message')).to_contain_text('Datei zugeordnet')
    page.locator('#package-load-assets').click();expect(page.locator('#package-asset option')).to_have_count(2)
    page.locator('#package-asset').select_option(corrected['id'])
    page.locator('#package-add').click();page.locator('#save').click()
    expect(page.locator('#message')).to_contain_text('Neue Stammdatenrevision gespeichert')
    saved=next(item for item in store.resources() if item['id']==resource['id'])
    assert saved['revision']==2 and saved['cam_status']=='draft' and saved['file_package']['entries'][0]['asset_id']==corrected['id']
    assert store.manifest(store.release_folder(target['id']))==before and store.active()['release_id'] is None
    assert store.asset(resource['asset_ids'][0])[0]['id']==resource['asset_ids'][0]  # Bad original is retained, never deleted.

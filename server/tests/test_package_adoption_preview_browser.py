"""Adoption preview failures remain visible before any copy or writer acceptance."""
import os

import pytest

from nxserver.integrity import snapshot
from test_filepackages import packages
from test_filepackages_browser import package_ui

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated early file-adoption conflicts.')


def test_baseline_parent_file_is_visible_conflict_without_job_or_asset(package_ui):
    from playwright.sync_api import expect
    page,store,_,source=package_ui;relative='UGII/templates/Sub/CUSTOM.prt'
    path=source/relative;path.parent.mkdir();path.write_bytes(b'Synthetic nested customer fixture')
    (store.standards['2512']/'UGII/templates/Sub').write_bytes(b'Stock file, not folder')
    release=store.import_release('test','2512','Baseline conflict fixture','fixture');page.locator('#refresh').click()
    expect(page.locator('#file-adopt-release option[value="'+release['id']+'"]')).to_have_count(1)
    page.locator('#file-adopt-release').select_option(release['id']);page.locator('#file-adopt-kind').select_option('template')
    before=snapshot(store)['sha256'];page.locator('#file-adopt-query').fill('Sub/CUSTOM.prt');page.locator('#file-adopt-load').click()
    page.locator('#file-adopt-files input').check();page.locator('#file-adopt-preview').click()
    expect(page.locator('#file-adopt-state')).to_contain_text('non-directory ancestor')
    expect(page.locator('#file-adopt-apply')).to_be_disabled()
    assert snapshot(store)['sha256']==before and not store.asset_inventory()['items'] and not store.resources()


def test_valid_nested_customer_file_still_adopts_as_draft_without_source_change(package_ui):
    from playwright.sync_api import expect
    page,store,_,source=package_ui;relative='UGII/templates/Sub/CUSTOM.prt'
    path=source/relative;path.parent.mkdir();path.write_bytes(b'Synthetic nested customer fixture')
    release=store.import_release('test','2512','Nested customer fixture','fixture');before=store.manifest(store.release_folder(release['id']))
    page.locator('#refresh').click();page.locator('#file-adopt-release').select_option(release['id']);page.locator('#file-adopt-kind').select_option('template')
    page.locator('#file-adopt-query').fill('Sub/CUSTOM.prt');page.locator('#file-adopt-load').click();page.locator('#file-adopt-files input').check()
    page.locator('#file-adopt-preview').click();expect(page.locator('#file-adopt-apply')).to_be_enabled()
    page.locator('#file-adopt-apply').click();expect(page.locator('#panel-prepare')).to_be_visible(timeout=15000)
    resource=store.resources()[0];assert resource['cam_status']=='draft' and resource['file_package']['entries'][0]['path']==relative
    assert store.manifest(store.release_folder(release['id']))==before and store.active()['release_id'] is None

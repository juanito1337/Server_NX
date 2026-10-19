"""Bound file adoption preview acceptance, only disposable synthetic fixtures."""
import os

import pytest

from test_filepackages_browser import package_ui
from test_filepackages import packages

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated Edge file ownership gates.')


@pytest.mark.parametrize('field',['commit_contract_version','ownership_sha256','parent_sha256','source_folder','kind','resource_id'])
def test_bad_final_commit_contract_never_enables_file_adoption(package_ui,field):
    from playwright.sync_api import expect
    page,store,target,*_=package_ui;audit=store.audit_entries()
    page.locator('#file-adopt-release').select_option(target['id']);page.locator('#file-adopt-kind').select_option('template')
    page.locator('#file-adopt-load').click();page.locator('#file-adopt-files input').check()
    def broken(route):
        response=route.fetch();value=response.json()
        if field=='commit_contract_version':value.pop(field)
        elif field=='source_folder':value['source_state']['folder']='unapproved-source'
        elif field=='kind':value['kind']='ui_standard'
        elif field=='resource_id':value['resource_id']='different-resource'
        else:value[field]='unbound'
        route.fulfill(response=response,json=value)
    pattern='**/file-packages/preview';page.route(pattern,broken);page.locator('#file-adopt-preview').click()
    expect(page.locator('#message')).to_contain_text('Kein vollständiger quell-/besitzgebundener Dateiübernahmevertrag')
    expect(page.locator('#file-adopt-apply')).to_be_disabled();assert store.audit_entries()==audit and store.resources()==[]
    page.unroute(pattern,broken);page.locator('#file-adopt-preview').click();expect(page.locator('#file-adopt-apply')).to_be_enabled()
    expect(page.locator('#file-adopt-state')).to_contain_text('im finalen Commit erneut geprüft')

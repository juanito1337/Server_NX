"""Exact whole-group clone contracts and value inspection, using synthetic data."""
import os

import pytest

from test_filepackages import packages
from test_filepackages_browser import package_ui
from test_cloning_browser import clone_ui, open_clone, preview
from test_requests_browser import track

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated clone response contracts.')


@pytest.mark.parametrize('defect',['preview_hash','source_release','source_library','source_key','source_sha','apply_type',
    'row_count','copied_approval','resource_kind','identifier','conflicts','inherited_geometry','shortened_group','changed_source_value'])
def test_incomplete_preview_never_enables_or_changes_editor(clone_ui,defect):
    from playwright.sync_api import expect
    page,store,target=clone_ui;open_clone(page);page.locator('#name').fill('Preserved editor')
    before=store.audit_entries()
    def tamper(route):
        response=route.fetch();value=response.json()
        if defect=='preview_hash':value.pop('preview_hash')
        elif defect=='source_release':value['source_release_id']='00000000-0000-0000-0000-000000000000'
        elif defect=='source_library':value['source_library']='tool-metric'
        elif defect=='source_key':value['source_key']='OTHER'
        elif defect=='source_sha':value['source_sha256']='0'*64
        elif defect=='apply_type':value['can_apply']='false'
        elif defect=='row_count':value['rows']+=1
        elif defect=='copied_approval':value['approval_copied']=True
        elif defect=='resource_kind':value['resource']['kind']='tool'
        elif defect=='identifier':value['resource']['cam']['records'][0]['values']['LIBRF']='OTHER'
        elif defect=='conflicts':value['conflicts']=['Unresolved collision']
        elif defect=='inherited_geometry':value['resource']['geometry']['status']='validated'
        elif defect=='shortened_group':value['resource']['cam']['records'].pop();value['rows']-=1
        else:value['resource']['cam']['records'][0]['values']['DESCR']='Not in the selected source group'
        route.fulfill(response=response,json=value)
    page.route('**/library-clone/preview',tamper);track(page,'clone-preview');page.locator('#clone-preview').click()
    page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#clone-apply')).to_be_disabled()
    expect(page.locator('#clone-state')).to_contain_text('Keine vollständige aktuelle Kopiervorschau')
    expect(page.locator('#name')).to_have_value('Preserved editor')
    assert store.resources()==[] and store.audit_entries()==before


@pytest.mark.parametrize('defect',['hash','cam_value','missing_row','extra_row','kind','name','file_binding','model_package','geometry','draft_flag'])
def test_apply_must_match_reviewed_resource_exactly_before_editor_replacement(clone_ui,defect):
    from playwright.sync_api import expect
    page,store,*_=clone_ui;open_clone(page);preview(page)
    page.locator('#name').fill('Old editor value');old=page.locator('#cam').input_value();before=store.audit_entries()
    def tamper(route):
        response=route.fetch();value=response.json();resource=value['resource']
        if defect=='hash':value['preview_hash']='0'*64
        elif defect=='cam_value':resource['cam']['records'][0]['values']['DESCR']='Not reviewed'
        elif defect=='missing_row':resource['cam']['records'].pop()
        elif defect=='extra_row':resource['cam']['records'].append(resource['cam']['records'][0])
        elif defect=='kind':resource['kind']='tool'
        elif defect=='name':resource['name']='Not reviewed'
        elif defect=='file_binding':resource['file_bindings']=[{'asset_id':'unreviewed','path':'unreviewed.prt'}]
        elif defect=='model_package':resource['model_package']={'result_id':'unreviewed'}
        elif defect=='geometry':resource['geometry']['evidence']='Unreviewed inherited qualification'
        else:value['draft_required']=False
        route.fulfill(response=response,json=value)
    page.route('**/library-clone/apply',tamper);track(page,'clone-apply');page.locator('#clone-apply').click()
    page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#message')).to_contain_text('Vorlagenentwurf')
    expect(page.locator('#name')).to_have_value('Old editor value');expect(page.locator('#cam')).to_have_value(old)
    expect(page.locator('#clone-apply')).to_be_disabled()
    assert store.resources()==[] and store.audit_entries()==before


@pytest.mark.parametrize('clone_ui',[{'rows':8},{'rows':8,'key_field':'NAME'}],indirect=True)
def test_group_values_are_paged_and_only_identifier_changes_are_displayed(clone_ui):
    from playwright.sync_api import expect
    page,store,*_=clone_ui;open_clone(page);before=store.audit_entries();preview(page)
    expect(page.locator('#clone-values tr')).to_have_count(25)
    expect(page.locator('#clone-values-page')).to_contain_text('von 32')
    first=page.locator('#clone-values tr').first
    expect(first.locator('td').nth(2)).to_have_text('STD');expect(first.locator('td').nth(3)).to_have_text('NEW_CUSTOM')
    descr=page.locator('#clone-values tr').filter(has_text='DESCR').first
    assert descr.locator('td').nth(2).inner_text()==descr.locator('td').nth(3).inner_text()=='First template section'
    page.locator('#clone-values-next').click();expect(page.locator('#clone-values tr')).to_have_count(7)
    page.locator('#clone-values-prev').click();expect(page.locator('#clone-values tr')).to_have_count(25)
    page.locator('#clone-key').fill('OTHER')
    expect(page.locator('#clone-values-box')).to_be_hidden();expect(page.locator('#clone-apply')).to_be_disabled()
    assert store.resources()==[] and store.audit_entries()==before

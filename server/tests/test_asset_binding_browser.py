"""Immutable attachment identity and current-editor bindings; disposable files only."""
import json
import os

import pytest

from test_filepackages import packages,body
from test_filepackages_browser import package_ui
from test_requests_browser import ui,hold,track,release

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated attachment contracts.')


@pytest.fixture
def binding(package_ui):
    from playwright.sync_api import expect
    page,store,*_=package_ui
    resource=store.save_resource(body(store,status='draft'),'fixture')
    other=store.add_asset('OTHER.prt',b'Other synthetic bytes; not NX geometry','fixture')
    page.locator('#refresh').click();expect(page.locator('#resources tr')).to_have_count(1)
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten',exact=True).click()
    expect(page.locator('#attachments tr')).to_have_count(1)
    before=store.audit_entries();saved=store.resources()
    yield page,store,resource,other
    assert store.audit_entries()==before and store.resources()==saved


def substituted(page,identifier,other):
    def replace(route):
        response=route.fetch();value=response.json();value.update(other)
        route.fulfill(response=response,json=value)
    page.route('**/assets/'+identifier+'/metadata',replace)


def library(page):
    from playwright.sync_api import expect
    page.locator('#asset-library').evaluate('(element)=>{element.open=true;}')
    page.locator('#asset-library-load').click();expect(page.locator('#asset-library-items tr')).to_have_count(2)


def test_other_file_cannot_be_bound_as_package_hashes(binding):
    from playwright.sync_api import expect
    page,_,resource,other=binding;identifier=resource['asset_ids'][0]
    substituted(page,identifier,other);track(page,'package-bind');page.locator('#package-bind').click()
    page.wait_for_function('()=>window.__oldActionFinished')
    assert page.evaluate('NXFilePackages.fields().file_package.asset_hashes')=={}
    expect(page.locator('#message')).to_contain_text('Dateimetadaten')
    expect(page.locator('#package-state')).not_to_contain_text('Dateihashes gebunden')


@pytest.mark.parametrize('restore',[False,True])
def test_pending_assignment_cannot_override_later_attachment_input_even_aba(binding,restore):
    from playwright.sync_api import expect
    page,_,resource,other=binding;library(page)
    target=page.locator('#asset-library-items tr').filter(has_text=other['filename'])
    hold(page,'/assets/'+other['id']+'/metadata')
    target.get_by_role('button',name='Zuordnen',exact=True).evaluate("""button=>{
      const original=button.onclick;window.__oldActionFinished=false;
      button.onclick=async(...args)=>{try{return await original(...args);}finally{window.__oldActionFinished=true;}};
    }""")
    target.get_by_role('button',name='Zuordnen',exact=True).click();page.wait_for_function('()=>window.__held')
    page.locator('#editor-section details.technical').evaluate('(element)=>{element.open=true;}')
    page.locator('#assetids').fill('[]')
    if restore:page.locator('#assetids').fill(json.dumps(resource['asset_ids']))
    current=page.locator('#assetids').input_value();release(page)
    expect(page.locator('#assetids')).to_have_value(current)
    expect(page.locator('#message')).not_to_contain_text('Datei zugeordnet')


@pytest.mark.parametrize('consumer',['geometry','package','options'])
@pytest.mark.parametrize('fault',['id','sha256','filename','size'])
def test_metadata_identity_and_immutable_fields_are_checked_for_every_binding(binding,consumer,fault):
    from playwright.sync_api import expect
    page,_,resource,other=binding;identifier=resource['asset_ids'][0]
    wrong={'id':other['id'],'sha256':'f'*64,'filename':'RENAMED.prt','size':1}[fault]
    if consumer=='options':
        page.locator('#package-load-assets').click();expect(page.locator('#package-asset option')).to_have_count(1)
    substituted(page,identifier,{fault:wrong})
    button={'geometry':'geometry-bind','package':'package-bind','options':'package-load-assets'}[consumer]
    track(page,button);page.locator('#'+button).click();page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#message')).to_contain_text('Dateimetadaten')
    if consumer=='geometry':
        assert page.evaluate('NXNative.resourceFields().geometry.asset_hashes')=={}
        expect(page.locator('#geometry-status')).to_have_value('needs_preparation')
    elif consumer=='package':
        assert page.evaluate('NXFilePackages.fields().file_package.asset_hashes')=={}
        for name in ('paths','dependencies','content','nx_test'):expect(page.locator('#package-'+name)).not_to_be_checked()
    else:expect(page.locator('#package-asset option')).to_have_count(0)


def test_unavailable_attachment_keeps_requested_identity_and_cannot_download_another_file(binding):
    from playwright.sync_api import expect
    page,_,resource,other=binding;identifier=resource['asset_ids'][0]
    substituted(page,identifier,other);page.evaluate('NXAssets.open()')
    expect(page.locator('#attachments')).to_contain_text('Nicht verfügbar · '+identifier)
    expect(page.locator('#attachments')).not_to_contain_text(other['filename'])
    expect(page.locator('#attachments').get_by_role('button',name='Herunterladen',exact=True)).to_have_count(0)
    page.locator('#attachments').get_by_role('button',name='Zuordnung entfernen',exact=True).click()
    expect(page.locator('#assetids')).to_have_value('[]')


@pytest.mark.parametrize('fault',['id','sha256','filename','size'])
def test_library_assignment_must_verify_exactly_the_listed_immutable_file(binding,fault):
    from playwright.sync_api import expect
    page,_,resource,other=binding;library(page)
    substituted(page,other['id'],{fault:resource['asset_ids'][0] if fault=='id' else 'f'*64 if fault=='sha256' else 'RENAMED.prt' if fault=='filename' else 1})
    page.locator('#asset-library-items tr').filter(has_text=other['filename']).get_by_role('button',name='Zuordnen',exact=True).click()
    expect(page.locator('#message')).to_contain_text('Dateimetadaten')
    assert json.loads(page.locator('#assetids').input_value())==resource['asset_ids']


@pytest.mark.parametrize('fault',['offset','count','duplicate','missing','size','hash','integrity','usage'])
def test_invalid_inventory_clears_old_assignable_rows_without_losing_editor_input(binding,fault):
    from playwright.sync_api import expect
    page,_,resource,_=binding;library(page)
    def invalid(route):
        response=route.fetch();value=response.json()
        if fault=='offset':value['offset']=100
        elif fault=='count':value['filtered']+=1
        elif fault=='duplicate':value['items'][1]=value['items'][0]
        elif fault=='missing':value['items'].pop()
        elif fault=='size':value['items'][0]['size']='12'
        elif fault=='hash':value['items'][0]['sha256']='not a hash'
        elif fault=='integrity':value['integrity_checked']=True
        else:value['items'][0]['current_resource_count']=-1
        route.fulfill(response=response,json=value)
    page.route('**/api/v1/assets?*',invalid);page.locator('#asset-library-load').click()
    expect(page.locator('#asset-library-state')).to_contain_text('nicht bestätigt')
    expect(page.locator('#asset-library-items tr')).to_have_count(0)
    expect(page.locator('#asset-library-prev')).to_be_disabled();expect(page.locator('#asset-library-next')).to_be_disabled()
    expect(page.locator('#asset-library-load')).to_be_enabled()
    assert json.loads(page.locator('#assetids').input_value())==resource['asset_ids']


def test_valid_hash_binding_resets_checks_but_only_explicit_save_creates_revision(binding):
    from playwright.sync_api import expect
    page,store,resource,_=binding;identifier=resource['asset_ids'][0];metadata=store.asset(identifier)[0]
    page.locator('#package-bind').click();expect(page.locator('#package-state')).to_contain_text('Dateihashes gebunden')
    assert page.evaluate('NXFilePackages.fields().file_package.asset_hashes')=={identifier:metadata['sha256']}
    for name in ('paths','dependencies','content','nx_test'):expect(page.locator('#package-'+name)).not_to_be_checked()
    for name in ('units','orientation','mount_point','cutting_geometry','non_cutting_geometry','collision_geometry'):page.locator('#geometry-'+name).check()
    page.locator('#geometry-status').select_option('validated');page.locator('#geometry-bind').click()
    expect(page.locator('#message')).to_contain_text('Dateiprüfsummen gebunden')
    assert page.evaluate('NXNative.resourceFields().geometry.asset_hashes')=={identifier:metadata['sha256']}
    for name in ('units','orientation','mount_point','cutting_geometry','non_cutting_geometry','collision_geometry'):expect(page.locator('#geometry-'+name)).not_to_be_checked()
    expect(page.locator('#geometry-status')).to_have_value('needs_preparation')


@pytest.mark.parametrize('old_error',[False,True])
def test_replaced_inventory_cancels_pending_assignment_even_after_query_aba(binding,old_error):
    from playwright.sync_api import expect
    page,_,resource,other=binding;library(page)
    hold(page,'/assets/'+other['id']+'/metadata',old_error)
    target=page.locator('#asset-library-items tr').filter(has_text=other['filename']).get_by_role('button',name='Zuordnen',exact=True)
    target.evaluate("""button=>{const original=button.onclick;window.__oldActionFinished=false;
      button.onclick=async(...args)=>{try{return await original(...args);}finally{window.__oldActionFinished=true;}};}""")
    target.click();page.wait_for_function('()=>window.__held')
    expect(page.locator('#asset-library-items tr').filter(has_text=other['filename']).get_by_role('button',name='Zuordnen',exact=True)).to_be_disabled()
    page.locator('#asset-query').fill('OTHER');page.locator('#asset-query').fill('');library(page)
    notice=page.locator('#message').inner_text();release(page)
    expect(page.locator('#message')).to_have_text(notice)
    assert json.loads(page.locator('#assetids').input_value())==resource['asset_ids']


@pytest.mark.parametrize('restore',[False,True])
def test_verified_upload_remains_saved_but_not_assigned_after_later_attachment_input(ui,restore):
    from playwright.sync_api import expect
    page,store,*_=ui;old=store.add_asset('EXISTING.prt',b'Synthetic old file','fixture')
    page.locator('#new').click();page.locator('#assetfile').set_input_files({'name':'NEW.prt','mimeType':'application/octet-stream','buffer':b'Synthetic new upload'})
    hold(page,'/api/v1/assets');track(page,'upload');page.locator('#upload').click();page.wait_for_function('()=>window.__held')
    page.locator('#editor-section details.technical').evaluate('(element)=>{element.open=true;}')
    page.locator('#assetids').fill(json.dumps([old['id']]))
    if restore:page.locator('#assetids').fill('[]')
    expected=page.locator('#assetids').input_value();release(page)
    expect(page.locator('#message')).to_contain_text('Nicht automatisch dem aktuellen Editor zugeordnet')
    expect(page.locator('#assetids')).to_have_value(expected)
    assert len(store.asset_inventory()['items'])==2
    assert page.evaluate('sessionStorage.getItem("nx.pending-asset-upload.v1")') is None


def test_multi_file_binding_is_all_or_nothing_when_one_metadata_reply_is_invalid(binding):
    from playwright.sync_api import expect
    page,_,_,other=binding;library(page)
    page.locator('#asset-library-items tr').filter(has_text=other['filename']).get_by_role('button',name='Zuordnen',exact=True).click()
    expect(page.locator('#attachments tr')).to_have_count(2)
    page.locator('#package-load-assets').click();expect(page.locator('#package-asset option')).to_have_count(2)
    page.locator('#package-asset').select_option(other['id']);page.locator('#package-add').click()
    substituted(page,other['id'],{'sha256':'invalid'})
    page.locator('#package-bind').click();expect(page.locator('#message')).to_contain_text('Dateimetadaten')
    assert page.evaluate('NXFilePackages.fields().file_package.asset_hashes')=={}
    assert len(page.evaluate('NXFilePackages.fields().file_package.entries'))==2


def test_valid_inventory_pages_unicode_search_and_reuse_do_not_save_a_resource(ui):
    from playwright.sync_api import expect
    page,store,*_=ui
    for index in range(101):store.add_asset('Tool_'+str(index).zfill(3)+'.prt',b'Synthetic '+str(index).encode(),'fixture')
    selected=store.add_asset('Werkzeug ü.prt',b'Synthetic Unicode file, not a model','fixture')
    before=store.audit_entries();saved=store.resources()
    page.locator('#new').click();page.locator('#asset-library').evaluate('(element)=>{element.open=true;}')
    page.locator('#asset-library-load').click();expect(page.locator('#asset-library-items tr')).to_have_count(100)
    expect(page.locator('#asset-library-state')).to_contain_text('1–100 von 102')
    page.locator('#asset-library-next').click();expect(page.locator('#asset-library-items tr')).to_have_count(2)
    expect(page.locator('#asset-library-state')).to_contain_text('101–102 von 102')
    expect(page.locator('#asset-library-next')).to_be_disabled();expect(page.locator('#asset-library-prev')).to_be_enabled()
    page.locator('#asset-query').fill('WERKZEUG Ü');page.locator('#asset-library-load').click()
    expect(page.locator('#asset-library-items tr')).to_have_count(1)
    expect(page.locator('#asset-library-state')).to_contain_text('1–1 von 1')
    page.locator('#asset-library-items').get_by_role('button',name='Zuordnen',exact=True).click()
    expect(page.locator('#attachments')).to_contain_text('Werkzeug ü.prt')
    assert json.loads(page.locator('#assetids').input_value())==[selected['id']]
    assert store.audit_entries()==before and store.resources()==saved

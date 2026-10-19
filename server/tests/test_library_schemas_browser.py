"""Schema-only creation on disposable NX-like data, never a real NX session."""
import json
import os

import pytest

from nxserver.library_schemas import LibrarySchemas
from nxserver.libraries import digest
from test_native_editor_browser import editor,field,cam
from test_requests_browser import ui,hold,track,release

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated schema-only creation UI.')


def inventory(page,target):
    from playwright.sync_api import expect
    page.locator('#create-release').select_option(target['id']);page.locator('#create-inventory').click()
    expect(page.locator('#create-library option')).to_have_count(2);page.locator('#create-library').select_option('holder-metric')


def schema(page):
    from playwright.sync_api import expect
    page.locator('#create-schema').click();expect(page.locator('#native-new')).to_be_enabled()


def test_creation_only_requests_metadata_and_selected_schema_without_inheriting_object_values(editor):
    from playwright.sync_api import expect
    page,store,target,resource,*_=editor;before=store.audit_entries();requests=[]
    page.on('request',lambda request:requests.append(request.url) if '/api/v1/' in request.url else None)
    original=page.locator('#cam').input_value();inventory(page,target)
    expect(page.locator('#cam')).to_have_value(original);schema(page)
    assert len(requests)==2 and all('/library-schemas' in url for url in requests) and not any('/libraries' in url for url in requests)
    page.locator('#native-new').click();expect(page.locator('#message')).to_contain_text('Neuer Schema-Entwurf')
    value=cam(page);assert value['library_id']=='holder-metric' and len(value['records'])==1
    values=value['records'][0]['values'];assert values['LIBRF'].startswith('CUSTOM_') and values['F70']==values['__proto__']==''
    assert 'CUSTOM_GROUP' not in page.locator('#cam').input_value() and store.audit_entries()==before
    expect(page.locator('#camstatus')).to_have_value('draft');expect(page.locator('#assetids')).to_have_value('[]')
    assert next(value for value in store.resources() if value['id']==resource['id'])['revision']==1


def test_late_schema_load_preserves_existing_field_node_focus_and_new_typed_value(editor):
    from playwright.sync_api import expect
    page,store,target,*_=editor;inventory(page,target);before=store.audit_entries()
    pin=LibrarySchemas(store).inventory(target['id'])['inventory_sha256']
    hold(page,'/library-schemas/holder-metric?inventory_sha256='+pin);track(page,'create-schema')
    page.locator('#create-schema').click();page.wait_for_function('()=>window.__held')
    field(page,0,'F01').fill('Typed while schema loads');field(page,0,'F01').evaluate('(element)=>{window.__keptSchemaField=element;}')
    release(page);expect(page.locator('#native-new')).to_be_enabled()
    expect(field(page,0,'F01')).to_have_value('Typed while schema loads')
    assert page.evaluate('window.__keptSchemaField===document.activeElement&&window.__keptSchemaField.isConnected')
    assert store.audit_entries()==before


@pytest.mark.parametrize('fault',['hash','scope','duplicate','path','objects'])
def test_invalid_inventory_never_enables_old_or_partial_schema_choices(editor,fault):
    from playwright.sync_api import expect
    page,store,target,*_=editor;before=store.audit_entries();original=page.locator('#cam').input_value()
    def invalid(route):
        response=route.fetch();value=response.json()
        if fault=='hash':value['report_sha256']='0'*64
        elif fault=='scope':value['files_verified']=True
        elif fault=='duplicate':value['libraries'].append(value['libraries'][0])
        elif fault=='path':value['libraries'][0]['path']='wrong.dat'
        else:value['libraries'][0]['schemas']=[{'unexpected':'values'}]
        if fault!='hash':body=dict(value);body.pop('report_sha256');value['report_sha256']=digest(body)
        route.fulfill(response=response,json=value)
    page.route('**/api/v1/releases/'+target['id']+'/library-schemas',invalid)
    page.locator('#create-release').select_option(target['id']);page.locator('#create-inventory').click()
    expect(page.locator('#create-schema-state')).to_contain_text('nicht bestätigt')
    expect(page.locator('#create-library option')).to_have_count(0);expect(page.locator('#native-new')).to_be_disabled()
    expect(page.locator('#cam')).to_have_value(original);assert store.audit_entries()==before


@pytest.mark.parametrize('fault',['hash','scope','input','duplicate_field','definition_hash','block_sequence','objects','source'])
def test_invalid_selected_schema_clears_previous_ready_state_without_touching_editor(editor,fault):
    from playwright.sync_api import expect
    page,store,target,*_=editor;inventory(page,target);schema(page);before=store.audit_entries();original=page.locator('#cam').input_value()
    def invalid(route):
        response=route.fetch();value=response.json()
        if fault=='hash':value['report_sha256']='0'*64
        elif fault=='scope':value['object_values_included']=True
        elif fault=='input':value['input_sha256']='0'*64
        elif fault=='duplicate_field':value['schemas'][0]['fields'].append('LIBRF')
        elif fault=='definition_hash':value['schemas'][0]['schema_hash']='0'*64
        elif fault=='block_sequence':value['schemas'][0]['id']='A:999'
        elif fault=='objects':value['items']=[{'unexpected':'native object'}]
        else:value['release_state']['folder']='wrong-source-folder'
        if fault in ('duplicate_field','block_sequence'):
            for block in value['schemas']:block['schema_hash']=digest([block['class_name'],block['fields']])
            value['schema_sha256']=digest(value['schemas'])
        if fault!='hash':body=dict(value);body.pop('report_sha256');value['report_sha256']=digest(body)
        route.fulfill(response=response,json=value)
    page.route('**/api/v1/releases/'+target['id']+'/library-schemas/holder-metric?*',invalid)
    page.locator('#create-schema').click();expect(page.locator('#create-schema-state')).to_contain_text('nicht bestätigt')
    expect(page.locator('#native-new')).to_be_disabled();expect(page.locator('#native-block option')).to_have_count(0)
    page.locator('#native-new').evaluate('async(button)=>{button.disabled=false;await button.onclick();}')
    expect(page.locator('#cam')).to_have_value(original);assert store.audit_entries()==before


@pytest.mark.parametrize('old_error',[False,True])
def test_schema_selection_aba_discards_late_success_or_error(editor,old_error):
    from playwright.sync_api import expect
    page,store,target,*_=editor;inventory(page,target);before=store.audit_entries();original=page.locator('#cam').input_value()
    pin=LibrarySchemas(store).inventory(target['id'])['inventory_sha256']
    hold(page,'/library-schemas/holder-metric?inventory_sha256='+pin,error=old_error);track(page,'create-schema')
    page.locator('#create-schema').click();page.wait_for_function('()=>window.__held')
    page.locator('#create-library').select_option('tool-metric');page.locator('#create-library').select_option('holder-metric');release(page)
    expect(page.locator('#native-new')).to_be_disabled();expect(page.locator('#native-block option')).to_have_count(0)
    expect(page.locator('#message')).not_to_contain_text('Old delayed error');expect(page.locator('#cam')).to_have_value(original)
    schema(page);assert store.audit_entries()==before


def test_schema_source_context_change_on_refresh_invalidates_a_previously_loaded_definition(editor):
    from playwright.sync_api import expect
    page,store,target,*_=editor;inventory(page,target);schema(page);original=page.locator('#cam').input_value()
    with store.db() as db:db.execute("UPDATE releases SET status='blocked' WHERE id=?",(target['id'],))
    before=store.audit_entries();page.locator('#refresh').click()
    expect(page.locator('#native-new')).to_be_disabled();expect(page.locator('#create-library option')).to_have_count(0)
    expect(page.locator('#cam')).to_have_value(original);assert store.audit_entries()==before

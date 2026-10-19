"""Atomic workspace handoff on isolated databases/browser profiles, never NX."""
import os
import json
import hashlib
import copy

import pytest

from nxserver.integrity import snapshot
from test_requests_browser import ui, track, payload

pytestmark = pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST') != '1',
    reason='Opt-in isolated workspace Edge tests.')


@pytest.mark.parametrize('failure', ['hash', 'null_resource'])
def test_bad_workspace_never_partially_replaces_confirmed_state_or_editor(ui, failure):
    from playwright.sync_api import expect
    page, store, _, resource = ui
    page.locator('#resources tr').get_by_role('button', name='Bearbeiten', exact=True).click()
    page.locator('#name').fill('Unsaved editor must remain')
    editor = page.evaluate('editorSignature()')
    page.locator('#tab-manage').click()
    previous = page.evaluate('JSON.stringify([resources,releases,active])')
    summary = page.locator('#summary').inner_text()
    rows = page.locator('#resources').inner_text();before = snapshot(store)['sha256']

    def corrupt(route):
        response = route.fetch();data = response.json()
        if failure == 'hash': data['resources'][0]['name'] = 'Unverified changed resource'
        else: data['resources'].append(None)
        route.fulfill(response=response, json=data)

    page.route('**/api/v1/workspace', corrupt)
    track(page, 'refresh');page.locator('#refresh').click()
    page.wait_for_function('()=>window.__oldActionFinished')
    assert page.evaluate('JSON.stringify([resources,releases,active])') == previous
    expect(page.locator('#summary')).to_have_text(summary)
    assert page.locator('#resources').inner_text() == rows
    expect(page.locator('#refresh-state')).to_contain_text('nicht vollständig')
    assert page.evaluate('editorSignature()') == editor
    expect(page.locator('#resources')).to_have_attribute('aria-busy', 'false')
    assert snapshot(store)['sha256'] == before


def seal(route, response, data):
    raw=json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
    headers={**response.headers,'x-nx-workspace-contract':'1','x-nx-workspace-bytes':str(len(raw)),
        'x-nx-workspace-sha256':hashlib.sha256(raw).hexdigest(),'content-length':str(len(raw))}
    route.fulfill(response=response,headers=headers,body=raw)


@pytest.mark.parametrize('failure', ['duplicate_resource','duplicate_release','kind_array','cam_null','revision',
    'geometry','package_entries','signed','audit_head','generation','active_dangling','active_usable',
    'active_status','folder','version','launcher_profile','catalog_map','created_at'])
def test_even_byte_sealed_inconsistent_metadata_is_rejected_before_publication(ui, failure):
    from playwright.sync_api import expect
    page,store,_,_=ui
    previous=page.evaluate('JSON.stringify([resources,releases,active])');before=snapshot(store)['sha256']

    def corrupt(route):
        response=route.fetch();data=response.json();resource=data['resources'][0];release=data['releases'][0]
        if failure=='duplicate_resource':data['resources'].append(copy.deepcopy(resource))
        elif failure=='duplicate_release':data['releases'].append(copy.deepcopy(release))
        elif failure=='kind_array':resource['kind']=['gauge']
        elif failure=='cam_null':resource['cam']=None
        elif failure=='revision':resource['revision']=True
        elif failure=='geometry':resource['geometry']=True
        elif failure=='package_entries':resource['file_package']={'entries':{}}
        elif failure=='signed':data['snapshot']['signed']=True
        elif failure=='audit_head':data['snapshot']['audit_head']['sequence']=1.5
        elif failure=='generation':data['active']['generation']='1'
        elif failure=='active_dangling':data['active']['release_id']='00000000-0000-4000-8000-000000000001'
        elif failure=='active_usable':data['active']['usable']=True
        elif failure=='active_status':data['active']['status']='approved'
        elif failure=='folder':release['folder']=42
        elif failure=='version':release['nx_version']='25x2'
        elif failure=='launcher_profile':release['test_launcher']['ui_profile']='wrong'
        elif failure=='catalog_map':release['catalog']['exported_native']=[]
        else:release['created_at']='not a timestamp'
        seal(route,response,data)

    page.route('**/api/v1/workspace',corrupt);track(page,'refresh');page.locator('#refresh').click()
    page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#refresh-state')).to_contain_text('kein vollständiger',ignore_case=True)
    assert page.evaluate('JSON.stringify([resources,releases,active])')==previous
    expect(page.locator('#resources')).to_have_count(1)
    expect(page.locator('#resources tr')).to_have_count(1)
    assert snapshot(store)['sha256']==before


@pytest.mark.parametrize('module', ['sync','promise','review'])
def test_one_subscriber_failure_does_not_hide_new_core_or_skip_other_modules(ui, module):
    from playwright.sync_api import expect
    page,store,_,resource=ui
    changed=payload(resource);changed['name']='New confirmed core despite subscriber fault'
    store.save_resource(changed,'fixture',resource['id'],resource['revision'])
    before=snapshot(store)['sha256']
    page.evaluate("""kind=>{window.__laterSubscriber=0;window.__laterRefresh=0;
        const later=NXHistory.catalogChanged;NXHistory.catalogChanged=()=>{window.__laterSubscriber++;return later();};
        const refresh=NXGeometry.refresh;NXGeometry.refresh=(...args)=>{window.__laterRefresh++;return refresh(...args);};
        if(kind==='sync')NXExports.syncCatalog=()=>{throw new Error('Synthetic subscriber failure');};
        else if(kind==='promise')NXSchemas.sync=async()=>{throw new Error('Synthetic subscriber failure');};
        else NXReviews.logsChanged=()=>{throw new Error('Synthetic subscriber failure');};} """,module)
    track(page,'refresh');page.locator('#refresh').click();page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#resources')).to_contain_text(changed['name'])
    expect(page.locator('#refresh-state')).to_contain_text('Bestand geladen; einzelne Module')
    expect(page.locator('#refresh-state')).to_contain_text('Synthetic subscriber failure')
    assert page.evaluate('window.__laterSubscriber')==1 and page.evaluate('window.__laterRefresh')==1
    assert page.evaluate('resources[0].revision')==2 and page.evaluate('NXClientBoot.status().failed') is False
    assert snapshot(store)['sha256']==before


def test_decimal_cam_unicode_and_prototype_fields_survive_refresh_without_numeric_reencoding(ui):
    from playwright.sync_api import expect
    page,store,_,resource=ui
    changed=payload(resource);changed['cam']={'tolerance':0.015,'float':1.0,'small':1e-20,'__proto__':'literal','label':'🔧'}
    store.save_resource(changed,'fixture',resource['id'],resource['revision']);before=snapshot(store)['sha256']
    track(page,'refresh');page.locator('#refresh').click();page.wait_for_function('()=>window.__oldActionFinished')
    expect(page.locator('#refresh-state')).to_contain_text('Bestand aktualisiert')
    data=json.loads(page.evaluate('JSON.stringify(resources[0])'))
    assert data['cam']==changed['cam'] and data['revision']==2
    page.locator('#resources tr').get_by_role('button',name='Bearbeiten',exact=True).click()
    assert json.loads(page.locator('#cam').input_value())==changed['cam']
    assert snapshot(store)['sha256']==before

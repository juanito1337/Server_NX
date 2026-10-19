"""Bounded whole-folder plans on disposable Edge profiles and synthetic files only."""
import copy
import hashlib
import json
import os
import re

import pytest

from nxserver.libraries import SPECS,digest
from test_upgrade_recovery_browser import wizard,jobs
from test_libraries import simple,write

pytestmark=pytest.mark.skipif(os.environ.get('NX_BROWSER_TEST')!='1',reason='Opt-in isolated upgrade-plan qualification.')


@pytest.mark.parametrize('fault',['contract','scope','request','manifest','count','bytes','actions','hash',
                                  'input_hash','prerequisites','flags','libraries','references','new_columns'])
def test_bad_report_cannot_arm_or_render_an_upgrade_plan(wizard,fault):
    from playwright.sync_api import expect
    page,store,source,target=wizard;before=store.manifest(source)
    page.locator('#upgrade-preview').click();expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    expect(page.locator('#details')).to_contain_text('nx_folder_upgrade_plan')
    expect(page.locator('#upgrade-review')).to_be_visible()
    def invalid(route):
        response=route.fetch();value=response.json()
        if fault=='contract':value.pop('plan_contract_version')
        elif fault=='scope':value['scope']='another_scope'
        elif fault=='request':value['request_sha256']='0'*64
        elif fault=='manifest':value['result_manifest'][0]['path']='../outside.txt'
        elif fault=='count':value['file_count']+=1
        elif fault=='bytes':value['bytes']+=1
        elif fault=='actions':value['actions'][0]['action']='automatic_approval'
        elif fault=='hash':value['preview_hash']='0'*64
        elif fault=='input_hash':value.pop('input_sha256')
        elif fault=='prerequisites':value['prerequisites']['nx_execution_verified']=True
        elif fault=='flags':value['automatic_activation']=True
        elif fault=='libraries':value['libraries'].pop('references')
        elif fault=='references':value['references']['automatic_geometry_validation']=True
        else:value['new_columns']=[{'library_id':'holder-metric','block_id':'SAMPLE:1','field':'EXTRA','confirmed':True,'value':'Unconfirmed','keys':['CUSTOM']}]
        route.fulfill(response=response,json=value)
    page.route('**/migrations/*/report',invalid);page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-state')).to_contain_text('Upgrade-Plan nicht bestätigt',timeout=15000)
    expect(page.locator('#upgrade-build')).to_be_disabled();expect(page.locator('#upgrade-preview')).to_be_enabled()
    expect(page.locator('#details')).not_to_contain_text('nx_folder_upgrade_plan')
    expect(page.locator('#upgrade-review')).to_be_hidden()
    expect(page.locator('#upgrade-review-items tr')).to_have_count(0)
    assert page.locator('#upgrade-columns fieldset').count()==0
    assert len(jobs(store))==2 and store.manifest(source)==before and not target.exists() and not store.releases()


@pytest.mark.parametrize('fault',['new_value','path_case','summary','manifest_hash','reference_flag'])
def test_coordinated_rehash_does_not_bypass_plan_semantics(wizard,fault):
    from playwright.sync_api import expect
    page,store,source,target=wizard
    def invalid(route):
        response=route.fetch();job=response.json()
        if job['status']!='completed':route.fulfill(response=response);return
        report=json.loads((store.root/'upgrades'/job['id']/'report.json').read_text(encoding='utf-8'))
        if fault=='new_value':report['new_columns']=[{'library_id':'holder-metric','block_id':'SAMPLE:1','field':'EXTRA','confirmed':True,'value':'Forged','keys':['CUSTOM']}]
        elif fault=='path_case':
            report['result_manifest'] += [{'path':path,'size':0,'sha256':hashlib.sha256(b'').hexdigest()} for path in ('MACH/custom/Sub/a.txt','MACH/custom/sub/b.txt')]
            report['file_count']+=2;report['result_manifest_sha256']=digest(report['result_manifest'])
        elif fault=='manifest_hash':report['result_manifest_sha256']='0'*64
        elif fault=='reference_flag':report['libraries']['references']['automatic_geometry_validation']=True;report['references']['automatic_geometry_validation']=True
        unsigned=copy.deepcopy(report);unsigned.pop('preview_hash');report['preview_hash']=digest(unsigned)
        raw=json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        job['report_sha256']=hashlib.sha256(raw).hexdigest()
        job['summary'].update({name:report[name] for name in ('file_count','bytes','preview_hash','can_build')})
        if fault=='summary':job['summary']['bytes']+=1
        page.route('**/migrations/'+job['id']+'/report',lambda report_route:report_route.fulfill(status=200,content_type='application/json',body=raw))
        route.fulfill(response=response,json=job)
    page.route(re.compile(r'/api/v1/migrations/[a-f0-9-]{36}$'),invalid);page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-state')).to_contain_text('Upgrade-Plan nicht bestätigt',timeout=15000)
    expect(page.locator('#upgrade-build')).to_be_disabled()
    assert len(jobs(store))==1 and not target.exists() and not store.releases()


@pytest.mark.parametrize('fault',['id','mode','request','status','progress','execution'])
def test_status_must_match_observed_operation_before_any_terminal_branch(wizard,fault):
    from playwright.sync_api import expect
    page,store,source,target=wizard
    def invalid(route):
        response=route.fetch();value=response.json()
        if fault=='id':value['id']='00000000-0000-0000-0000-000000000000'
        elif fault=='mode':value['mode']='build';value['release_id']='00000000-0000-0000-0000-000000000000'
        elif fault=='request':value['request']['name']='Another operation'
        elif fault=='status':value['status']='finished'
        elif fault=='progress':value['progress']=101
        else:value['execution'].pop('writer_may_be_active')
        route.fulfill(response=response,json=value)
    page.route(re.compile(r'/api/v1/migrations/[a-f0-9-]{36}$'),invalid);page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-state')).to_contain_text('Auftragsbeleg passt nicht',timeout=15000)
    expect(page.locator('#upgrade-build')).to_be_disabled();expect(page.locator('#upgrade-preview')).to_be_disabled()
    expect(page.locator('#message')).not_to_contain_text('Versionswechsel als neuer NX-Datenstand erstellt')
    assert len(jobs(store))==1 and not target.exists() and not store.releases()


def test_new_column_decision_remains_editable_then_builds_exact_confirmed_value(wizard):
    from playwright.sync_api import expect
    from nxserver.libraries import NativeTable
    page,store,source,target=wizard;spec=SPECS['holder-metric'];write(store.standards['2512'],spec['path'],simple(extra=True))
    before=store.manifest(source);page.locator('#upgrade-preview').click()
    field=page.locator('#upgrade-columns fieldset').first
    expect(field).to_be_visible(timeout=15000);expect(page.locator('#upgrade-build')).to_be_disabled()
    field.locator('input:not([type=checkbox])').fill('Explicit shop value')
    expect(field).to_be_visible();field.locator('input[type=checkbox]').check()
    assert json.loads(page.locator('#import-defaults').input_value())['holder-metric']['SAMPLE:1']['EXTRA']=='Explicit shop value'
    page.locator('#upgrade-preview').click();expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    expect(page.locator('#upgrade-columns input[type=checkbox]')).to_be_checked()
    page.locator('#upgrade-build').click()
    expect(page.locator('#message')).to_contain_text('Versionswechsel als neuer NX-Datenstand erstellt',timeout=15000)
    assert NativeTable.read(target/spec['path']).groups()['CUSTOM'][0]['values']['EXTRA']=='Explicit shop value'
    assert store.manifest(source)==before and store.active()['release_id'] is None and len(jobs(store))==3


def test_changed_source_clears_old_decision_controls_and_verified_details(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard;spec=SPECS['holder-metric'];write(store.standards['2512'],spec['path'],simple(extra=True))
    page.locator('#upgrade-preview').click();expect(page.locator('#upgrade-columns fieldset')).to_have_count(1,timeout=15000)
    page.locator('#source-path').fill(str(source)+'-different')
    expect(page.locator('#upgrade-columns fieldset')).to_have_count(0)
    expect(page.locator('#details')).to_contain_text('not_verified');expect(page.locator('#upgrade-build')).to_be_disabled()
    assert len(jobs(store))==1 and not target.exists()


@pytest.mark.parametrize('collision',['directory_case','stock_file','customer_file'])
def test_real_tree_collision_report_remains_visible_but_never_buildable(wizard,collision):
    from playwright.sync_api import expect
    page,store,source,target=wizard
    stock,customer={'directory_case':('Sub/a.txt','sub/b.txt'),'stock_file':('collision','collision/child.txt'),
                    'customer_file':('collision/child.txt','collision')}[collision]
    write(store.standards['2512'],'MACH/custom/'+stock,'Stock fixture')
    write(source,'MACH/custom/'+customer,'Customer fixture')
    page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-state')).to_contain_text('Konflikte. Entscheidungen bestätigen',timeout=15000)
    expect(page.locator('#upgrade-file-conflicts')).to_contain_text('Geplanter Zielbaum')
    expect(page.locator('#upgrade-build')).to_be_disabled();expect(page.locator('#upgrade-preview')).to_be_enabled()
    expect(page.locator('#details')).to_contain_text('nx_folder_upgrade_plan')
    assert len(jobs(store))==1 and not target.exists() and not store.releases()


def test_wrong_result_release_binding_does_not_claim_success_or_change_workspace(wizard):
    from playwright.sync_api import expect
    page,store,source,target=wizard;page.locator('#upgrade-preview').click()
    expect(page.locator('#upgrade-build')).to_be_enabled(timeout=15000)
    def invalid(route):
        response=route.fetch();value=response.json();value['catalog']['migration']['job_id']='00000000-0000-0000-0000-000000000000'
        route.fulfill(response=response,json=value)
    page.route('**/releases/*',invalid);page.locator('#upgrade-build').click()
    expect(page.locator('#upgrade-state')).to_contain_text('Ergebnisdatenstand ist nicht',timeout=15000)
    expect(page.locator('#panel-update')).to_be_visible()
    expect(page.locator('#message')).not_to_contain_text('Versionswechsel als neuer NX-Datenstand erstellt')
    assert len(jobs(store))==2 and len(store.releases())==1 and target.is_dir() and store.active()['release_id'] is None

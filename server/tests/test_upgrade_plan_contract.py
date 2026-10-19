"""Whole-folder plan bindings with synthetic installations, never NX execution."""
import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from nxserver.libraries import SPECS,digest
from nxserver.upgrades import Upgrades
from nxserver.safety import DomainError
from test_upgrades import upgrade_pilot,preview,finish,write,simple
from test_management import HEADERS


def test_entire_upgrade_plan_is_independently_hashable_and_bound_to_actual_request(upgrade_pilot):
    client,store,request,source=upgrade_pilot;job,report=preview(client,request)
    assert report['plan_contract_version']==1 and report['scope']=='nx_folder_upgrade_plan'
    assert report['read_only'] and not report['nx_files_written'] and not report['automatic_approval'] and not report['automatic_activation']
    assert report['request_sha256']==digest(job['request'])
    assert report['result_manifest_sha256']==digest(report['result_manifest'])
    value=copy.deepcopy(report);value.pop('preview_hash');assert digest(value)==report['preview_hash']
    assert hashlib.sha256(json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()==job['report_sha256']
    assert report['file_count']==len(report['result_manifest']) and report['bytes']==sum(item['size'] for item in report['result_manifest'])
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']}))
    assert result['status']=='completed',result['message']
    assert sorted(store.get_release(result['release_id'])['manifest'],key=lambda item:item['path'])==sorted(report['result_manifest'],key=lambda item:item['path'])


def test_legacy_report_remains_readable_but_cannot_accept_a_new_build(upgrade_pilot):
    client,store,request,*_=upgrade_pilot;job,report=preview(client,request)
    old=copy.deepcopy(report);old.pop('plan_contract_version');raw=json.dumps(old,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    (store.root/'upgrades'/job['id']/'report.json').write_bytes(raw)
    manager=Upgrades(store);manager._update(job['id'],report_sha256=hashlib.sha256(raw).hexdigest())
    assert manager.report(job['id'])==old
    before=len(manager.list());response=client.post('/api/v1/migrations/build',headers=HEADERS,json={**request,'preview_id':job['id'],'preview_hash':old['preview_hash']})
    assert response.status_code==409 and len(manager.list())==before


def test_shared_directory_casing_conflict_is_reported_before_creating_target(upgrade_pilot):
    client,store,request,source=upgrade_pilot
    write(store.standards['2512'],'MACH/custom/Sub/new.txt','New stock fixture')
    write(source,'MACH/custom/sub/customer.txt','Customer fixture')
    job,report=preview(client,request)
    assert not report['can_build'] and any(item['category']=='path' and 'casing' in item['reason'] for item in report['conflicts'])


@pytest.mark.parametrize('stock_is_file',[True,False])
def test_file_directory_collision_is_a_preview_conflict_before_target_creation(upgrade_pilot,stock_is_file):
    client,store,request,source=upgrade_pilot
    write(store.standards['2512'],'MACH/custom/collision'+('' if stock_is_file else '/child.txt'),'Stock file')
    write(source,'MACH/custom/collision'+('/child.txt' if stock_is_file else ''),'Customer file')
    job,report=preview(client,request)
    assert not report['can_build'] and any(item['category']=='path' for item in report['conflicts'])
    response=client.post('/api/v1/migrations/build',headers=HEADERS,json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']})
    assert response.status_code==409 and not Path(request['target_path']).exists() and not store.releases()


def test_report_reader_has_a_real_byte_bound(upgrade_pilot,monkeypatch):
    from nxserver import upgrades
    client,store,request,*_=upgrade_pilot;job,report=preview(client,request);manager=Upgrades(store)
    monkeypatch.setattr(upgrades,'MAX_REPORT_BYTES',1)
    with pytest.raises(DomainError):manager.report(job['id'])


@pytest.mark.parametrize('fault',['changed_bytes','duplicate_fields','nonfinite','array','invalid_encoding'])
def test_report_reader_rejects_changed_or_nonunique_json_without_writing(upgrade_pilot,fault):
    client,store,request,*_=upgrade_pilot;job,report=preview(client,request);manager=Upgrades(store)
    raw={'changed_bytes':b'{"modified":true}','duplicate_fields':b'{"scope":1,"scope":2}',
         'nonfinite':b'{"scope":NaN}','array':b'[]','invalid_encoding':b'\xff'}[fault]
    report_path=store.root/'upgrades'/job['id']/'report.json';report_path.write_bytes(raw)
    if fault!='changed_bytes':manager._update(job['id'],report_sha256=hashlib.sha256(raw).hexdigest())
    before=store.audit_entries()
    with pytest.raises(DomainError):manager.report(job['id'])
    assert report_path.read_bytes()==raw and store.audit_entries()==before and not store.releases()


def test_actual_javascript_validator_accepts_backend_plan_and_hashes_unicode_like_python(upgrade_pilot):
    client,store,request,*_=upgrade_pilot;job,report=preview(client,request)
    candidates=[shutil.which('node'),r'C:\Program Files\nodejs\node.exe','/mnt/c/Program Files/nodejs/node.exe']
    node=next((value for value in candidates if value and Path(value).is_file()),None)
    if not node:pytest.skip('JavaScript runtime unavailable.')
    web=Path(__file__).parents[1]/'nxserver/web';source=(web/'upgrades.js').read_text(encoding='utf-8').split('globalThis.NXUpgrade=')[0]
    data=(web/'app.js').read_text(encoding='utf-8').split('globalThis.NXRequests=')[0]
    vendor=(web/'vendor/jssha-3.3.2-sha256.js').read_text(encoding='utf-8')
    samples=[{'10':'ten','2':'two','\U00010000':'astral','\ue000':'bmp','__proto__':'literal'},['ä','🛠',None,True,12],{'line':'one\nline\tquote"','nested':{'constructor':'value'}}]
    script='''const assert=require('node:assert/strict');
const holder={exports:{}};new Function('module','exports',VENDOR)(holder,holder.exports);globalThis.jsSHA=holder.exports;
new Function(DATA)();new Function(SOURCE)();
const packet=PACKET;assert.equal(NXUpgradePlan.verify(packet.report,packet.job,packet.request,packet.specs),true);
for(const item of SAMPLES)assert.equal(NXUpgradePlan.hash(item.value),item.sha);
console.log('Actual upgrade verifier and Python-compatible Unicode hashes passed.');'''
    values={'VENDOR':vendor,'DATA':data,'SOURCE':source,'PACKET':{'report':report,'job':job,'request':job['request'],'specs':list(SPECS.values())},'SAMPLES':[{'value':value,'sha':digest(value)} for value in samples]}
    for name,value in values.items():
        encoded=json.dumps(value,ensure_ascii=True)
        replacement='JSON.parse('+json.dumps(encoded)+')' if name in ('PACKET','SAMPLES') else encoded
        script=script.replace(name,replacement)
    result=subprocess.run([node,'-'],input=script,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr


def test_explicit_standard_choice_is_honored_even_when_the_file_was_not_conflicting(upgrade_pilot):
    client,store,request,source=upgrade_pilot;relative='MACH/custom/unchanged-stock.txt'
    write(store.standards['2406'],relative,'Stock bytes');write(store.standards['2512'],relative,'Stock bytes');write(source,relative,'Customer bytes')
    request['file_resolutions']={relative:'standard'};job,report=preview(client,request)
    item=next(item for item in report['result_manifest'] if item['path']==relative)
    assert item['sha256']==hashlib.sha256(b'Stock bytes').hexdigest()
    assert {'path':relative,'action':'standard_selected'} in report['actions']


@pytest.mark.parametrize('choice',['standard','customer'])
def test_explicit_choice_for_customer_only_file_is_consistent_in_plan_and_build(upgrade_pilot,choice):
    client,store,request,source=upgrade_pilot;relative='MACH/custom/customer.txt'
    request['file_resolutions']={relative:choice};job,report=preview(client,request)
    assert any(item['path']==relative for item in report['result_manifest']) is (choice=='customer')
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']}))
    assert result['status']=='completed'
    assert (Path(request['target_path'])/relative).is_file() is (choice=='customer')
    assert (source/relative).read_text()=='Customer customization'

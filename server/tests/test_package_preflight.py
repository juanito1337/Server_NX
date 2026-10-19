"""Editor file-package preflight, synthetic files only; no NX/production data."""
import copy

import pytest

from nxserver.integrity import snapshot
from test_filepackages import packages,body
from test_management import HEADERS,ERP_HEADERS


def request(value):
    return {'kind':value['kind'],'nx_version':value['file_package']['nx_version'],
        'asset_ids':value['asset_ids'],'entries':value['file_package']['entries']}


def test_technical_preflight_is_read_only_and_reports_bytes_not_nx_qualification(packages):
    client,store,release,*_=packages;value=body(store,'ui_standard','UGII/menus/roles/FIRM.mtx',filename='FIRM.mtx',content=b'<NX_PROFILES/>')
    before=snapshot(store)['sha256'];data=request(value)
    result=client.post('/api/v1/file-packages/preflight',headers=HEADERS,json=data)
    assert result.status_code==200,result.text
    report=result.json();assert report['contract_version']==1 and report['can_bind'] and report['files'][0]['xml_checked'] is True
    assert report['read_only'] and not report['nx_files_written'] and not report['automatic_approval'] and not report['nx_validation']
    assert report['files'][0]['sha256']==value['file_package']['asset_hashes'][value['asset_ids'][0]]
    assert snapshot(store)['sha256']==before and store.resources()==[]


@pytest.mark.parametrize('collision',['case','file_directory'])
def test_conflicting_directory_prefixes_are_rejected_already_when_saving_draft(packages,collision):
    client,store,*_=packages;value=body(store,path='UGII/templates/Sub/A.prt',filename='A.prt',status='draft')
    other=body(store,path='UGII/templates/sub/B.prt' if collision=='case' else 'UGII/templates/Sub/A.prt/B.prt',filename='B.prt',status='draft')
    value['asset_ids']+=other['asset_ids'];value['file_package']['entries']+=other['file_package']['entries']
    response=client.post('/api/v1/resources',headers=HEADERS,json=value)
    assert response.status_code in (400,409),response.text
    assert store.resources()==[]


@pytest.mark.parametrize('fault',['path','basename','xml','missing','changed','detached','empty','duplicate_asset','duplicate_path'])
def test_all_technical_errors_are_visible_without_creating_resources(packages,fault):
    client,store,release,*_=packages;value=body(store,'ui_standard','UGII/menus/roles/FIRM.mtx',filename='FIRM.mtx',content=b'<broken>' if fault=='xml' else b'<NX_PROFILES/>');data=request(value)
    if fault=='path':data['entries'][0]['path']='W:/SiemensShare/nx2406/FIRM.mtx'
    elif fault=='basename':data['entries'][0]['path']='UGII/menus/roles/RENAMED.mtx'
    elif fault=='xml':pass  # Invalid XML with correct, current metadata/bytes.
    elif fault=='missing':store.asset(value['asset_ids'][0])[1].unlink()
    elif fault=='changed':store.asset(value['asset_ids'][0])[1].write_bytes(b'Changed')
    elif fault=='detached':data['asset_ids']=[]
    elif fault=='empty':data['entries']=[]
    elif fault=='duplicate_asset':data['entries'].append({'asset_id':data['entries'][0]['asset_id'],'path':'UGII/menus/profiles/FIRM.mtx'})
    else:data['entries'].append(dict(data['entries'][0]))
    before=snapshot(store)['sha256'];result=client.post('/api/v1/file-packages/preflight',headers=HEADERS,json=data)
    assert result.status_code==200,result.text
    report=result.json();assert not report['can_bind'] and report['errors']
    assert snapshot(store)['sha256']==before and not store.resources()


def test_preflight_bounds_apply_before_any_file_read_and_never_use_unbounded_asset_verifier(packages,monkeypatch):
    from nxserver import filepackages
    client,store,*_=packages;value=body(store);data=request(value);calls=[]
    def forbidden(*args):raise AssertionError('Preflight must not invoke unbounded legacy asset reads')
    monkeypatch.setattr(store,'asset',forbidden)
    original=filepackages.file_content
    def counted(*args):calls.append(True);return original(*args)
    monkeypatch.setattr(filepackages,'file_content',counted)
    result=client.post('/api/v1/file-packages/preflight',headers=HEADERS,json=data)
    assert result.status_code==200 and result.json()['can_bind'] and len(calls)==1
    calls.clear();monkeypatch.setattr(filepackages,'MAX_PREFLIGHT_BYTES',1)
    result=client.post('/api/v1/file-packages/preflight',headers=HEADERS,json=data)
    assert result.status_code==200 and not result.json()['can_bind'] and not calls


def test_unused_private_attachments_are_not_read_or_claimed_verified(packages,monkeypatch):
    from nxserver import filepackages
    client,store,*_=packages;value=body(store);private=store.add_asset('DOCUMENT.pdf',b'Synthetic private document','fixture')
    data=request(value);data['asset_ids'].append(private['id']);before=snapshot(store)['sha256'];paths=[];original=filepackages.file_content
    def counted(path,metadata):paths.append(str(path));assert metadata['id']!=private['id'];return original(path,metadata)
    monkeypatch.setattr(filepackages,'file_content',counted)
    result=client.post('/api/v1/file-packages/preflight',headers=HEADERS,json=data)
    assert result.status_code==200 and result.json()['can_bind'] and result.json()['private_attachments']==1 and len(paths)==1
    assert snapshot(store)['sha256']==before


def test_repeated_bad_xml_is_read_only_once_not_once_per_duplicate_entry(packages,monkeypatch):
    from nxserver import filepackages
    client,store,*_=packages;value=body(store,'ui_standard','UGII/menus/roles/FIRM.mtx',filename='FIRM.mtx',content=b'<broken>');data=request(value)
    data['entries']=data['entries']*100;original=filepackages.file_content;calls=[]
    def counted(*args):calls.append(True);return original(*args)
    monkeypatch.setattr(filepackages,'file_content',counted)
    result=client.post('/api/v1/file-packages/preflight',headers=HEADERS,json=data)
    assert result.status_code==200 and not result.json()['can_bind'] and len(calls)==1


def test_final_selected_metadata_drift_prevents_a_success_report(packages,monkeypatch):
    from nxserver import filepackages
    client,store,*_=packages;value=body(store);data=request(value);original=filepackages.file_content
    def changed(path,metadata):
        raw=original(path,metadata)
        with store.db() as db:db.execute('UPDATE assets SET sha256=? WHERE id=?',('0'*64,metadata['id']))
        return raw
    monkeypatch.setattr(filepackages,'file_content',changed)
    response=client.post('/api/v1/file-packages/preflight',headers=HEADERS,json=data)
    assert response.status_code==409 and not store.resources()


def test_api_is_admin_only_and_never_accepts_a_qualification_payload(packages):
    client,store,*_=packages;value=body(store);data=request(value);path='/api/v1/file-packages/preflight';before=snapshot(store)['sha256']
    assert client.post(path,json=data).status_code==401
    assert client.post(path,headers=ERP_HEADERS,json=data).status_code==403
    assert client.post(path,headers=HEADERS,json={**data,'automatic_approval':True}).status_code==422
    data['asset_ids']=['invalid-id'];assert client.post(path,headers=HEADERS,json=data).status_code==400
    assert snapshot(store)['sha256']==before

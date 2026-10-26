"""Corrupt recovery metadata must never become a usable snapshot or a 500."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver import backups
from nxserver.safety import DomainError
from test_backups import backup_pilot,create_backup,finish,repack,HEADERS


BAD_DOCUMENTS=[
    b'[]',b'null',b'"private snapshot text"',b'42',b'true',
    b'{"id":"first","id":"second"}',b'{"entry":{"size":1,"size":2}}',
    b'{"value":NaN}',b'{"value":Infinity}',b'{"value":-Infinity}',b'{"value":1e400}',
    b'{"text":"\\ud800"}',b'{"\\udfff":1}',b'{"text":"\xff"}',
    b'{"private snapshot text":',b'{"nested":'+b'['*4000+b'0'+b']'*4000+b'}',
]


@pytest.mark.parametrize('raw',BAD_DOCUMENTS,ids=['array','null','string','number','boolean',
    'duplicate_root','duplicate_nested','nan','infinity','negative_infinity','overflow',
    'surrogate_value','surrogate_key','bad_utf8','syntax','deep_nesting'])
def test_snapshot_reader_rejects_ambiguous_or_unsupported_documents_without_echo(tmp_path,raw):
    path=tmp_path/'metadata.json';path.write_bytes(raw)
    with pytest.raises(DomainError) as caught:backups.read_json(path)
    assert caught.value.status==409 and 'private snapshot text' not in str(caught.value)
    assert path.read_bytes()==raw


@pytest.mark.parametrize('raw',[b'[]',b'null',b'"broken"'])
def test_malformed_receipt_is_listed_as_invalid_and_cannot_start_a_writer(backup_pilot,raw):
    client,store,*_=backup_pilot;receipt=create_backup(client);manager=client.app.state.backups
    path=manager._folder(receipt['id'])/'receipt.json';path.write_bytes(raw)
    before=store.audit_entries();jobs={job['id'] for job in manager.jobs()}
    with TestClient(client.app,raise_server_exceptions=False) as http:
        response=http.get('/api/v1/backups',headers=HEADERS)
        assert response.status_code==200,response.text
        assert response.json()['snapshots']==[{'status':'invalid','folder':path.parent.name,
            'message':'Snapshot receipt is invalid; no recovery or download.'}]
        verify=http.post('/api/v1/backups/'+receipt['id']+'/verify',headers=HEADERS,json={})
        assert verify.status_code==409,verify.text
    assert {job['id'] for job in manager.jobs()}==jobs and store.audit_entries()==before
    assert not manager.restore_roots['local'].exists() and path.read_bytes()==raw


@pytest.mark.parametrize('defect',['duplicate_root','duplicate_nested','nonfinite','surrogate','boolean_version'])
def test_even_rehashed_bad_archive_metadata_cannot_prepare_recovery(backup_pilot,defect):
    client,store,*_=backup_pilot;receipt=create_backup(client);manager=client.app.state.backups
    import zipfile
    with zipfile.ZipFile(manager.download(receipt['id'])) as archive:raw=archive.read('manifest.json')
    if defect=='duplicate_root':raw=b'{"schema_version":1,'+raw[1:]
    elif defect=='duplicate_nested':raw=b'{"extra":{"key":1,"key":2},'+raw[1:]
    elif defect=='nonfinite':raw=b'{"extra":NaN,'+raw[1:]
    elif defect=='surrogate':raw=b'{"extra":"\\ud800",'+raw[1:]
    else:
        value=json.loads(raw);value['schema_version']=True;raw=json.dumps(value).encode()
    # Deliberately recompute the unsigned outer receipt: parsing must still
    # reject ambiguity; this test never claims an authenticity signature.
    repack(manager,receipt,extra={'manifest.json':raw})
    before=store.resources()
    job=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',
        headers=HEADERS,json={'name':'NeverPrepared'}))
    assert job['status']=='failed',job
    assert not manager.restore_roots['local'].exists() and store.resources()==before
    assert not (manager._job_folder(job['id'])/'management.sqlite3').exists()


def test_report_swap_after_first_read_is_rejected_instead_of_returning_unbound_content(backup_pilot,monkeypatch):
    client,store,*_=backup_pilot;receipt=create_backup(client);manager=client.app.state.backups
    job=next(item for item in manager.jobs() if item['mode']=='backup')
    path=manager._job_folder(job['id'])/'report.json'
    replacement=json.dumps({**receipt,'unbound':'must never be returned'}).encode()
    real_open=Path.open;swapped=[];before=store.audit_entries()
    class SwapAfterClose:
        def __init__(self,stream):self.stream=stream
        def __getattr__(self,name):return getattr(self.stream,name)
        def __enter__(self):self.stream.__enter__();return self
        def __exit__(self,*args):
            result=self.stream.__exit__(*args)
            if not swapped:
                swapped.append(True)
                with real_open(path,'wb') as target:target.write(replacement)
            return result
    def opened(candidate,*args,**kwargs):
        stream=real_open(candidate,*args,**kwargs)
        return SwapAfterClose(stream) if candidate==path and args and args[0]=='rb' and not swapped else stream
    monkeypatch.setattr(Path,'open',opened)
    with pytest.raises(DomainError) as caught:manager.report(job['id'])
    assert caught.value.status==409 and swapped and store.audit_entries()==before


@pytest.mark.parametrize('path',['W:/never-read/metadata.json',r'\\never-read\share\metadata.json',r'\\?\W:\never-read\metadata.json'])
def test_snapshot_metadata_rejects_protected_path_text_before_mocked_lookup(monkeypatch,path):
    monkeypatch.setattr(Path,'lstat',lambda *a,**k:pytest.fail('Protected input must not reach filesystem lookup.'))
    with pytest.raises(DomainError):backups.read_json(path)


def test_supported_unicode_and_large_integer_metadata_remain_exact(tmp_path):
    value={'name':'Fr\u00e4ser \U0001f6e0','revision':2**60+1,'future':{'version':3},'size':1.25}
    path=tmp_path/'metadata.json';raw=json.dumps(value,ensure_ascii=False).encode();path.write_bytes(raw)
    assert backups.read_json(path)==value and path.read_bytes()==raw


def test_unknown_finite_manifest_fields_remain_backward_compatible(backup_pilot):
    client,*_=backup_pilot;receipt=create_backup(client);manager=client.app.state.backups
    repack(manager,receipt,change_manifest=lambda value:value.update(future={'format':2,'note':'retained'}))
    job=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',
        headers=HEADERS,json={'name':'FutureFields'}))
    assert job['status']=='completed',job
    assert manager.report(job['id'])['can_restore'] and not manager.restore_roots['local'].exists()


@pytest.mark.parametrize('defect',['boolean_version','duplicate_status'])
def test_ambiguous_receipt_cannot_be_listed_as_ready(backup_pilot,defect):
    client,*_=backup_pilot;receipt=create_backup(client);manager=client.app.state.backups
    path=manager._folder(receipt['id'])/'receipt.json';raw=path.read_bytes()
    if defect=='boolean_version':
        value=json.loads(raw);value['schema_version']=True;raw=json.dumps(value).encode()
    else:raw=b'{"status":"invalid",'+raw[1:]
    path.write_bytes(raw)
    assert manager.list()[0]['status']=='invalid'
    with pytest.raises(DomainError) as caught:manager.receipt(receipt['id'])
    assert caught.value.status==409 and path.read_bytes()==raw


def test_reader_preserves_missing_status_and_enforces_exact_hash_and_byte_bound(tmp_path):
    import hashlib
    path=tmp_path/'metadata.json';raw=b'{"value":"bounded"}'
    with pytest.raises(DomainError) as missing:backups.read_json(path)
    assert missing.value.status==404
    path.write_bytes(raw);pin=hashlib.sha256(raw).hexdigest()
    assert backups.read_json(path,limit=len(raw),expected_sha256=pin)=={'value':'bounded'}
    for limit,expected in [(len(raw)-1,pin),(len(raw),'0'*64)]:
        with pytest.raises(DomainError) as invalid:backups.read_json(path,limit=limit,expected_sha256=expected)
        assert invalid.value.status==409 and path.read_bytes()==raw

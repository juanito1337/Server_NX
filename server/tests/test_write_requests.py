"""Lost-reply-safe metadata writes, using isolated SQLite/HTTP fixtures only."""
import copy
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from nxserver.store import Store,canonical
from nxserver.integrity import scan
from nxserver.write_requests import cancel,get
from test_management import pilot,ADMIN,ERP,HEADERS,ERP_HEADERS


def body(name='Synthetic gauge'):
    return {'kind':'gauge','name':name,'cam':{},'cam_status':'draft','erp':None,'asset_ids':[]}


def header(key,erp=False):return {**(ERP_HEADERS if erp else HEADERS),'Idempotency-Key':key}


def test_create_replay_is_atomic_durable_and_role_scoped(pilot):
    client,store,*_=pilot;key=str(uuid.uuid4());value=body()
    first=client.post('/api/v1/resources',headers=header(key),json=value);assert first.status_code==201,first.text
    before=store.audit_entries();again=client.post('/api/v1/resources',headers=header(key),json=value)
    assert again.json()==first.json() and len(store.resources())==1
    assert store.audit_entries()==before
    restarted=Store(store.root);assert get(restarted,'admin',key)['result']==first.json()
    assert client.get('/api/v1/write-requests/'+key).status_code==401
    assert client.get('/api/v1/write-requests/'+key,headers=ERP_HEADERS).status_code==404
    assert scan(store)['error_count']==0


def test_update_retry_returns_original_revision_not_new_head(pilot):
    client,store,*_=pilot;resource=client.post('/api/v1/resources',headers=HEADERS,json=body()).json();key=str(uuid.uuid4())
    first=client.put('/api/v1/resources/'+resource['id'],headers=header(key),json={**body('First change'),'expected_revision':1})
    assert first.status_code==200,first.text
    newer=client.put('/api/v1/resources/'+resource['id'],headers=HEADERS,json={**body('Later change'),'expected_revision':2})
    assert newer.status_code==200
    prefix=store.audit_entries();again=client.put('/api/v1/resources/'+resource['id'],headers=header(key),json={**body('First change'),'expected_revision':1})
    assert again.json()==first.json() and again.json()['revision']==2
    assert store.resources()[0]['revision']==3 and store.audit_entries()==prefix
    assert get(store,'admin',key)['result']['name']=='First change'


@pytest.mark.parametrize('mutation',['payload','revision','route'])
def test_same_key_different_action_body_or_expected_revision_fails(pilot,mutation):
    client,store,*_=pilot;key=str(uuid.uuid4());first=client.post('/api/v1/resources',headers=header(key),json=body()).json()
    before=store.audit_entries();path='/api/v1/resources';value=body()
    if mutation=='payload':value['name']='Different payload'
    else:path+='/'+first['id'];value['expected_revision']=1 if mutation=='route' else 99
    response=client.post(path,headers=header(key),json=value) if mutation=='payload' else client.put(path,headers=header(key),json=value)
    assert response.status_code==409 and 'different data/action/expected revision' in response.json()['detail']
    assert len(store.resources())==1 and store.audit_entries()==before


def test_failed_transaction_leaves_neither_resource_nor_receipt(pilot,monkeypatch):
    client,store,*_=pilot;key=str(uuid.uuid4());original=store.audit
    def failure(*args,**kwargs):raise RuntimeError('Isolated failure before commit')
    monkeypatch.setattr(store,'audit',failure)
    with pytest.raises(RuntimeError,match='before commit'):store.save_resource(body(),'admin',request_key=key)
    assert store.resources()==[]
    with store.db() as db:assert db.execute('SELECT count(*) FROM write_requests').fetchone()[0]==0
    monkeypatch.setattr(store,'audit',original);result=store.save_resource(body(),'admin',request_key=key)
    assert result['revision']==1 and len(store.resources())==1


def test_two_independent_writers_share_one_receipt(pilot):
    client,store,*_=pilot;other=Store(store.root);key=str(uuid.uuid4());barrier=threading.Barrier(2)
    def save(writer):barrier.wait(timeout=10);return writer.save_resource(body(),'admin',request_key=key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one=pool.submit(save,store);two=pool.submit(save,other);results=[one.result(timeout=20),two.result(timeout=20)]
    assert results[0]==results[1] and len(store.resources())==1 and len(store.history(results[0]['id']))==1
    assert scan(store)['error_count']==0


def test_cancel_is_durable_fence_and_never_undoes_committed_data(pilot):
    client,store,*_=pilot;key=str(uuid.uuid4());path='/api/v1/write-requests/'+key+'/cancel'
    response=client.post(path,headers=HEADERS);assert response.status_code==200 and response.json()['state']=='cancelled'
    before=store.audit_entries();assert client.post(path,headers=HEADERS).json()==response.json()
    assert store.audit_entries()==before
    assert client.post('/api/v1/resources',headers=header(key),json=body()).status_code==409
    assert store.resources()==[] and get(Store(store.root),'admin',key)['state']=='cancelled'
    committed=str(uuid.uuid4());value=client.post('/api/v1/resources',headers=header(committed),json=body()).json()
    cancel_value=client.post('/api/v1/write-requests/'+committed+'/cancel',headers=HEADERS).json()
    assert cancel_value['state']=='committed' and cancel_value['result']==value and len(store.resources())==1
    assert scan(store)['error_count']==0


def test_cancel_waits_for_an_inflight_transaction_and_reports_actual_commit(pilot,monkeypatch):
    client,store,*_=pilot;writer=Store(store.root);key=str(uuid.uuid4());entered=threading.Event();continue_write=threading.Event();audit=writer.audit
    def delayed(*args,**kwargs):entered.set();assert continue_write.wait(timeout=10);return audit(*args,**kwargs)
    monkeypatch.setattr(writer,'audit',delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=pool.submit(writer.save_resource,body(),'admin',request_key=key);assert entered.wait(timeout=10)
        cancelling=pool.submit(cancel,store,'admin',key);continue_write.set()
        result=pending.result(timeout=20);receipt=cancelling.result(timeout=20)
    assert receipt['state']=='committed' and receipt['result']==result and len(store.resources())==1


def test_erp_retry_does_not_revert_later_cam_or_erp_changes(pilot):
    client,store,*_=pilot;key=str(uuid.uuid4());payload={'kind':'gauge','company_id':'synthetic','system_id':'ID1','number':'G1',
        'description':'Synthetic ERP gauge','unit':'PCS','source_revision':'1'}
    first=client.post('/api/v1/erp/resources/upsert',headers=header(key,True),json=payload)
    assert first.status_code==200,first.text
    resource=first.json();cam=body('CAM-owned label');cam['erp']=resource['erp']
    assert client.put('/api/v1/resources/'+resource['id'],headers=HEADERS,json={**cam,'expected_revision':1}).status_code==200
    later=client.post('/api/v1/erp/resources/upsert',headers=ERP_HEADERS,json={**payload,'source_revision':'2','expected_revision':2})
    assert later.status_code==200,later.text
    before=store.audit_entries();retry=client.post('/api/v1/erp/resources/upsert',headers=header(key,True),json=payload)
    assert retry.json()==first.json() and store.audit_entries()==before
    assert store.resources()[0]['revision']==3 and store.resources()[0]['name']=='CAM-owned label'
    assert get(store,'erp',key)['result']==first.json() and scan(store)['error_count']==0


def test_unchanged_erp_receipted_once_and_kind_cannot_change(pilot):
    client,store,*_=pilot;data={'kind':'gauge','company_id':'synthetic','system_id':'ID1','number':'G1',
        'description':'Synthetic ERP gauge','source_revision':'1'}
    client.post('/api/v1/erp/resources/upsert',headers=ERP_HEADERS,json=data);key=str(uuid.uuid4())
    first=client.post('/api/v1/erp/resources/upsert',headers=header(key,True),json=data);assert first.json()['unchanged']
    before=store.audit_entries();assert client.post('/api/v1/erp/resources/upsert',headers=header(key,True),json=data).json()==first.json()
    assert store.audit_entries()==before and len(store.history(first.json()['id']))==1
    assert client.post('/api/v1/erp/resources/upsert',headers=ERP_HEADERS,json={**data,'kind':'machine'}).status_code==409


@pytest.mark.parametrize('value',['not-a-key','A'*36,'0'*36])
def test_invalid_keys_make_no_mutation(pilot,value):
    client,store,*_=pilot;before=store.audit_entries()
    response=client.post('/api/v1/resources',headers=header(value),json=body())
    assert response.status_code in (400,422)
    assert store.resources()==[] and store.audit_entries()==before


def test_corrupt_receipt_is_not_replayed_as_success(pilot):
    client,store,*_=pilot;key=str(uuid.uuid4());response=client.post('/api/v1/resources',headers=header(key),json=body()).json()
    response['name']='Tampered response'
    with store.db() as db:db.execute('UPDATE write_requests SET response=?',(canonical(response),))
    assert client.get('/api/v1/write-requests/'+key,headers=HEADERS).status_code==409
    assert client.post('/api/v1/resources',headers=header(key),json=body()).status_code==409
    assert 'invalid_write_receipt' in {issue['code'] for issue in scan(store)['errors']}


@pytest.mark.parametrize('legacy',[False,True])
def test_backup_restore_preserves_receipts_or_accepts_pre_receipt_archives(pilot,legacy):
    from nxserver.backups import FUTURES
    client,store,*_=pilot;key=str(uuid.uuid4())
    first=client.post('/api/v1/resources',headers=HEADERS if legacy else header(key),json=body()).json()
    if legacy:
        # Own synthetic old database; do not change the legacy snapshot digest
        # by inventing the new empty table before it has been validated.
        with store.db() as db:db.execute('DROP TABLE write_requests')
    def finish(response):
        assert response.status_code==202,response.text
        job=response.json();FUTURES[job['id']].result(timeout=30)
        value=client.get('/api/v1/backup-jobs/'+job['id'],headers=HEADERS).json()
        assert value['status']=='completed',value.get('message')
        return value,client.app.state.backups.report(job['id'])
    _,receipt=finish(client.post('/api/v1/backups',headers=HEADERS,json={}))
    job,plan=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',headers=HEADERS,json={'name':'R'}))
    assert plan['can_restore'],plan['errors']
    _,report=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,json={
        'name':'R','preview_id':job['id'],'preview_hash':plan['preview_hash'],'accept_unsigned':True}))
    restored=Store(report['target_folder'])
    if not legacy:
        assert get(restored,'admin',key)['result']==first
        before=restored.audit_entries()
        # A direct duplicate API payload uses the same normalized DTO contract.
        from nxserver.api import ResourceInput
        assert restored.save_resource(ResourceInput.model_validate(body()).model_dump(),'admin',request_key=key)==first
        assert len(restored.resources())==1 and restored.audit_entries()==before
    else:
        with restored.db() as db:assert db.execute('SELECT count(*) FROM write_requests').fetchone()[0]==0
    assert restored.active()['release_id'] is None and scan(restored)['error_count']==0

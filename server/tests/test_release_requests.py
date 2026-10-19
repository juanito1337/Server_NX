"""Historical approval/switch receipts; isolated synthetic evidence, not NX proof."""
import copy
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from nxserver.integrity import scan
from nxserver.release_requests import get,cancel
from nxserver.safety import DomainError
from nxserver.store import Store
from test_management import pilot,HEADERS,ERP_HEADERS,imported
from test_reviews import setup_review


def headers(key):return {**HEADERS,'Idempotency-Key':key}
def review(client,release,body,key):return client.post('/api/v1/releases/'+release['id']+'/review',headers=headers(key),json=body)
def activate(client,release,key,generation=0):return client.post('/api/v1/active',headers=headers(key),json={'release_id':release['id'],'expected_generation':generation})


def test_approval_replay_is_original_response_without_revalidation(pilot,monkeypatch):
    client,store,_,log=pilot;release,body=setup_review(client,store,log);key=str(uuid.uuid4())
    first=review(client,release,body,key);assert first.status_code==200,first.text
    before=store.audit_entries();log.write_text(log.read_text()+'\nExternal change',encoding='utf-8')
    monkeypatch.setattr(store,'validate_release',lambda *args:pytest.fail('Receipt replay must not revalidate'))
    assert review(client,release,body,key).json()==first.json() and store.audit_entries()==before
    receipt=get(Store(store.root),'admin',key)
    assert receipt['result']==first.json() and not receipt['current_state'] and not receipt['files_verified']
    assert receipt['writer_finished_at'] and receipt['audit_sequence'] and scan(store)['error_count']==0


def test_activation_replay_does_not_reactivate_old_pointer(pilot,monkeypatch):
    client,store,_,log=pilot;release,body=setup_review(client,store,log)
    assert review(client,release,body,str(uuid.uuid4())).status_code==200
    key=str(uuid.uuid4());first=activate(client,release,key);assert first.status_code==200,first.text
    assert activate(client,release,str(uuid.uuid4()),1).json()['generation']==2
    before=store.audit_entries()
    monkeypatch.setattr(store,'validate_release',lambda *args:pytest.fail('Replay must not switch/revalidate'))
    assert activate(client,release,key).json()==first.json()
    assert get(store,'admin',key)['result']['generation']==1 and store.active()['generation']==2
    assert store.audit_entries()==before and scan(store)['error_count']==0


def test_activation_key_is_bound_to_original_generation(pilot):
    client,store,_,log=pilot;release,body=setup_review(client,store,log)
    assert review(client,release,body,str(uuid.uuid4())).status_code==200
    key=str(uuid.uuid4());assert activate(client,release,key).status_code==200;before=store.audit_entries()
    assert activate(client,release,key,1).status_code==409 and store.active()['generation']==1 and store.audit_entries()==before


@pytest.mark.parametrize('field',['folder','nx_version'])
def test_activation_identity_drift_after_qualification_cannot_switch(pilot,monkeypatch,field):
    client,store,_,log=pilot;release,body=setup_review(client,store,log)
    assert review(client,release,body,str(uuid.uuid4())).status_code==200
    original=store.get_release;calls=0
    def changed(identifier):
        nonlocal calls
        result=original(identifier);calls+=1
        if calls==3:  # Initial read, diagnostic read, then qualified read.
            with store.db() as db:db.execute('UPDATE releases SET '+field+'=? WHERE id=?',(str(store.root/'other-local-candidate') if field=='folder' else '2406',identifier))
        return result
    monkeypatch.setattr(store,'get_release',changed)
    key=str(uuid.uuid4());response=activate(client,release,key)
    assert response.status_code==409,response.text
    assert store.active()['release_id'] is None and store.active()['generation']==0 and get(store,'admin',key)['state']=='failed'


@pytest.mark.parametrize('change',['body','target','action'])
def test_same_key_different_intent_is_rejected(pilot,change):
    client,store,_,log=pilot;release,body=setup_review(client,store,log);key=str(uuid.uuid4())
    assert review(client,release,body,key).status_code==200
    other=imported(client) if change=='target' else release;before=store.audit_entries()
    changed={**body,'evidence':'Different synthetic human statement, not real CAM proof.'} if change=='body' else body
    response=activate(client,release,key) if change=='action' else review(client,other,changed,key)
    assert response.status_code==409 and store.audit_entries()==before and store.active()['release_id'] is None


def test_absent_receipt_is_not_stop_proof_and_fence_blocks_late_review(pilot):
    client,store,_,log=pilot;release,body=setup_review(client,store,log);key=str(uuid.uuid4());before=store.audit_entries()
    assert client.get('/api/v1/release-requests/'+key,headers=HEADERS).status_code==404 and store.audit_entries()==before
    value=cancel(store,'admin',key);assert value['state']=='cancelled';before=store.audit_entries()
    assert cancel(store,'admin',key)==value and review(client,release,body,key).status_code==409
    assert store.audit_entries()==before and store.get_release(release['id'])['status']=='draft'


@pytest.mark.parametrize('action',['review','activate'])
def test_same_store_fence_during_validation_blocks_final_commit_and_backup(pilot,monkeypatch,action):
    client,store,_,log=pilot;release,body=setup_review(client,store,log)
    if action=='activate':assert review(client,release,body,str(uuid.uuid4())).status_code==200
    key=str(uuid.uuid4());entered=threading.Event();proceed=threading.Event();validate=store.validate_release
    def delayed(*args):
        result=validate(*args);entered.set();assert proceed.wait(timeout=15);return result
    monkeypatch.setattr(store,'validate_release',delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=pool.submit(activate if action=='activate' else review,client,release,*([key] if action=='activate' else [body,key]))
        assert entered.wait(timeout=10);assert get(store,'admin',key)['state']=='reserved'
        fenced=pool.submit(cancel,store,'admin',key).result(timeout=5);assert fenced['state']=='cancelled'
        from nxserver.backups import Backups
        # An admitted writer still owns the intent until finally acknowledges it.
        service=Backups(Store(store.root))
        with pytest.raises(DomainError,match='writer|Release transition'):service.create('backup','admin',{})
        proceed.set();assert pending.result(timeout=20).status_code==409
    assert get(store,'admin',key)['writer_finished_at'] and store.active()['release_id'] is None
    assert not any(row['action']=='release.activated' for row in store.audit_entries())
    if action=='review':assert store.get_release(release['id'])['status']=='validated' and store.get_release(release['id'])['review'] is None
    assert scan(store)['error_count']==0


def test_two_stores_cannot_admit_same_request_twice(pilot,monkeypatch):
    client,store,_,log=pilot;release,body=setup_review(client,store,log);key=str(uuid.uuid4());other=Store(store.root,logs={'test-log':log})
    entered=threading.Event();proceed=threading.Event();validate=store.validate_release
    def delayed(*args):result=validate(*args);entered.set();assert proceed.wait(timeout=15);return result
    monkeypatch.setattr(store,'validate_release',delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(review,client,release,body,key);assert entered.wait(timeout=10)
        with pytest.raises(DomainError,match='unfinished'):other.review_release(release['id'],body['tests'],body['evidence'],body['syslog_source'],'admin',body['expected_log_sha256'],body['expected_manifest_sha256'],request_key=key)
        proceed.set();assert pending.result(timeout=20).status_code==200
    assert sum(row['action']=='release.approved' for row in store.audit_entries())==1


@pytest.mark.parametrize('action',['review','activate'])
def test_audit_failure_rolls_back_final_transition_and_response(pilot,monkeypatch,action):
    client,store,_,log=pilot;release,body=setup_review(client,store,log)
    if action=='activate':assert review(client,release,body,str(uuid.uuid4())).status_code==200
    audit=store.audit;key=str(uuid.uuid4())
    def fail(db,actor,event,payload):
        if event==('release.approved' if action=='review' else 'release.activated'):raise RuntimeError('Synthetic final audit failure')
        return audit(db,actor,event,payload)
    monkeypatch.setattr(store,'audit',fail)
    with pytest.raises(RuntimeError):activate(client,release,key) if action=='activate' else review(client,release,body,key)
    receipt=get(store,'admin',key);assert receipt['state']=='failed' and receipt['result'] is None and receipt['writer_finished_at']
    assert store.active()['release_id'] is None and store.active()['generation']==0
    if action=='review':assert store.get_release(release['id'])['status']=='validated'
    assert scan(store)['error_count']==0


@pytest.mark.parametrize('tamper',['response','sequence','binding'])
def test_receipt_tampering_blocks_replay_and_is_reported(pilot,tamper):
    from nxserver.auditstate import fingerprint,encoded
    client,store,_,log=pilot;release,body=setup_review(client,store,log);key=str(uuid.uuid4())
    assert review(client,release,body,key).status_code==200
    with store.db() as db:
        if tamper=='sequence':db.execute('UPDATE release_requests SET audit_sequence=audit_sequence-1')
        elif tamper=='binding':db.execute("UPDATE release_requests SET error='Changed after immutable audit'")
        else:
            response=copy.deepcopy(get(store,'admin',key)['result']);response['name']='Changed synthetic receipt'
            db.execute('UPDATE release_requests SET response=?,response_sha256=?',(encoded(response),fingerprint(response)))
    assert client.get('/api/v1/release-requests/'+key,headers=HEADERS).status_code==409
    assert review(client,release,body,key).status_code==409
    assert 'invalid_release_receipt' in {item['code'] for item in scan(store)['errors']}


def test_role_key_and_failed_intent_boundaries(pilot):
    client,store,_,log=pilot;release,body=setup_review(client,store,log);key=str(uuid.uuid4())
    assert client.get('/api/v1/release-requests/'+key).status_code==401
    assert client.get('/api/v1/release-requests/'+key,headers=ERP_HEADERS).status_code==403
    assert client.post('/api/v1/release-requests/'+key+'/cancel',headers=ERP_HEADERS).status_code==403
    assert review(client,release,body,'wrong').status_code==400
    response=activate(client,release,key);assert response.status_code==409
    assert get(store,'admin',key)['state']=='failed' and activate(client,release,key).status_code==409


@pytest.mark.parametrize('legacy',[False,True])
def test_restore_preserves_historical_receipt_without_current_approval_or_pointer(pilot,legacy):
    from nxserver.backups import FUTURES
    client,store,_,log=pilot;release,body=setup_review(client,store,log);review_key=str(uuid.uuid4());active_key=str(uuid.uuid4())
    if legacy:
        approved=client.post('/api/v1/releases/'+release['id']+'/review',headers=HEADERS,json=body).json()
        switched=client.post('/api/v1/active',headers=HEADERS,json={'release_id':release['id'],'expected_generation':0}).json()
    else:approved=review(client,release,body,review_key).json();switched=activate(client,release,active_key).json()
    if legacy:
        with store.db() as db:db.execute('DROP TABLE release_requests')
    def finish(response):
        assert response.status_code==202,response.text
        identifier=response.json()['id'];FUTURES[identifier].result(timeout=30)
        assert client.app.state.backups.get_job(identifier)['status']=='completed'
        return identifier,client.app.state.backups.report(identifier)
    _,receipt=finish(client.post('/api/v1/backups',headers=HEADERS,json={}))
    # A short destination keeps the receipt test within the actual Windows
    # path-length gate; do not bypass/weaken the production safety check.
    preview_id,plan=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',headers=HEADERS,json={'name':'R'}))
    assert plan['can_restore'],plan['errors']
    _,report=finish(client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,json={'name':'R','preview_id':preview_id,'preview_hash':plan['preview_hash'],'accept_unsigned':True}))
    restored=Store(report['target_folder'])
    if legacy:
        with restored.db() as db:assert db.execute('SELECT COUNT(*) FROM release_requests').fetchone()[0]==0
    else:
        assert get(restored,'admin',review_key)['result']==approved and get(restored,'admin',active_key)['result']==switched
    assert restored.get_release(release['id'])['status']!='approved' and restored.active()['release_id'] is None
    assert scan(restored)['error_count']==0

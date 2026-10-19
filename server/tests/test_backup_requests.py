"""Backup acceptance/worker failure proofs, isolated data and no real restore."""
import copy
import json
import threading
import uuid
from concurrent.futures import Future

import pytest

from nxserver.backups import Backups,FUTURES,read_json,write_json
from nxserver.backup_requests import get,cancel
from nxserver.integrity import scan
from nxserver.safety import DomainError
from nxserver.store import Store
from test_backups import backup_pilot,HEADERS,ERP_HEADERS,finish


def headers(key):return {**HEADERS,'Idempotency-Key':key}
def post(client,path,key,body=None):return client.post(path,headers=headers(key),json=body or {})
def counts(manager):return len(manager.jobs()),len(manager.list())


def test_same_backup_key_replays_original_acceptance_with_one_worker_and_snapshot(backup_pilot,monkeypatch):
    client,store,*_=backup_pilot;manager=client.app.state.backups;key=str(uuid.uuid4())
    first=post(client,'/api/v1/backups',key);assert first.status_code==202,first.text
    initial=first.json();assert initial['phase']=='queued'
    assert finish(client,first)['status']=='completed';audit=store.audit_entries();before=counts(manager)
    def forbidden(*args,**kwargs):raise AssertionError('Replay submitted another worker')
    monkeypatch.setattr(manager.pool,'submit',forbidden)
    assert post(client,'/api/v1/backups',key).json()==initial and counts(manager)==before and store.audit_entries()==audit
    assert get(Store(store.root),'admin',key)['result']==initial
    restarted=Backups(Store(store.root));assert restarted.get_job(initial['id'])['status']=='completed' and not restarted.busy
    assert scan(store)['error_count']==0
    assert client.get('/api/v1/backup-requests/'+key).status_code==401
    assert client.get('/api/v1/backup-requests/'+key,headers=ERP_HEADERS).status_code==403


def test_replay_while_worker_live_is_allowed_but_new_request_and_writes_stay_paused(backup_pilot,monkeypatch):
    client,store,*_=backup_pilot;manager=client.app.state.backups;entered=threading.Event();release=threading.Event();backup=manager._backup;key=str(uuid.uuid4())
    def held(*args):entered.set();assert release.wait(timeout=15);return backup(*args)
    monkeypatch.setattr(manager,'_backup',held)
    first=post(client,'/api/v1/backups',key);assert entered.wait(timeout=10)
    try:
        audit=store.audit_entries()
        assert post(client,'/api/v1/backups',key).json()==first.json()
        assert post(client,'/api/v1/backups',str(uuid.uuid4())).status_code==409
        assert client.post('/api/v1/resources',headers=HEADERS,json={'kind':'gauge','name':'Blocked','cam':{}}).status_code==409
        assert client.post('/api/v1/backup-requests/'+key+'/cancel',headers=HEADERS).json()['state']=='accepted'
        unknown=str(uuid.uuid4())
        assert client.post('/api/v1/backup-requests/'+unknown+'/cancel',headers=HEADERS).status_code==409
        assert store.audit_entries()==audit and not FUTURES[first.json()['id']].done()
    finally:release.set()
    assert finish(client,first)['status']=='completed'


def test_missing_receipt_is_not_stop_proof_and_fence_blocks_late_submission(backup_pilot):
    client,store,*_=backup_pilot;manager=client.app.state.backups;key=str(uuid.uuid4());before=counts(manager)
    assert client.get('/api/v1/backup-requests/'+key,headers=HEADERS).status_code==404
    receipt=cancel(store,'admin',key);assert receipt['state']=='cancelled'
    audit=store.audit_entries();assert cancel(store,'admin',key)==receipt and store.audit_entries()==audit
    assert post(client,'/api/v1/backups',key).status_code==409 and counts(manager)==before


def test_independent_manager_cannot_accept_parallel_root_writer_and_reads_current_job(backup_pilot,monkeypatch):
    client,store,*_=backup_pilot;manager=client.app.state.backups;other=Backups(Store(store.root))
    entered=threading.Event();release=threading.Event();backup=manager._backup
    def held(*args):entered.set();assert release.wait(timeout=15);return backup(*args)
    monkeypatch.setattr(manager,'_backup',held)
    first=post(client,'/api/v1/backups',str(uuid.uuid4()));assert entered.wait(timeout=10)
    try:
        with pytest.raises(DomainError,match='Another durable backup writer'):
            other.create('backup','admin',{},request_key=str(uuid.uuid4()))
        assert other.get_job(first.json()['id'])['status']=='running'
    finally:release.set()
    assert finish(client,first)['status']=='completed'
    assert other.get_job(first.json()['id'])['status']=='completed'


@pytest.mark.parametrize('mode',['verify','preview','restore'])
def test_all_archive_modes_replay_without_relookup_or_second_worker(backup_pilot,monkeypatch,mode):
    client,store,*_=backup_pilot;manager=client.app.state.backups
    created=post(client,'/api/v1/backups',str(uuid.uuid4()));finish(client,created);backup_id=manager.report(created.json()['id'])['id']
    path='/api/v1/backups/'+backup_id
    if mode=='verify':path+='/verify';body={}
    elif mode=='preview':path+='/restore-preview';body={'target':'local','name':'BP'}
    else:
        plan=post(client,path+'/restore-preview',str(uuid.uuid4()),{'target':'local','name':'BP'});finish(client,plan)
        report=manager.report(plan.json()['id']);path+='/restore';body={'target':'local','name':'BP','preview_id':plan.json()['id'],'preview_hash':report['preview_hash'],'accept_unsigned':True}
    key=str(uuid.uuid4());first=post(client,path,key,body);assert finish(client,first)['status']=='completed'
    before=counts(manager);audit=store.audit_entries()
    def forbidden(*args,**kwargs):raise AssertionError('Replay looked up original archive or started a worker')
    with monkeypatch.context() as guarded:
        guarded.setattr(manager,'receipt',forbidden);guarded.setattr(manager.pool,'submit',forbidden)
        assert post(client,path,key,body).json()==first.json()
        changed={**body,'name':'Other'} if mode!='verify' else None
        if changed:assert post(client,path,key,changed).status_code==409
        else:assert post(client,'/api/v1/backups/'+str(uuid.uuid4())+'/verify',key).status_code==409
    assert counts(manager)==before and store.audit_entries()==audit


@pytest.mark.parametrize('failure',['submit','journal'])
def test_failed_start_remains_durable_and_replay_never_submits_again(backup_pilot,monkeypatch,failure):
    client,store,*_=backup_pilot;manager=client.app.state.backups;key=str(uuid.uuid4())
    def fail(*args,**kwargs):raise OSError('Synthetic start failure')
    if failure=='submit':monkeypatch.setattr(manager.pool,'submit',fail)
    else:monkeypatch.setattr('nxserver.backups.write_json',fail)
    first=post(client,'/api/v1/backups',key);assert first.status_code==202,first.text
    identifier=first.json()['id'];assert identifier not in FUTURES
    job=manager.get_job(identifier);assert job['status']=='failed' and job['start_failure_proven'] and not manager.busy
    receipt=get(store,'admin',key);assert receipt['failure_observation']['status']=='failed'
    assert post(client,'/api/v1/backups',key).json()==first.json() and counts(manager)==(1,0)
    restarted=Backups(Store(store.root));assert restarted.get_job(identifier)['status']=='failed' and not restarted.busy
    assert scan(store)['error_count']==0


def test_audit_rollback_means_no_acceptance_or_worker(backup_pilot,monkeypatch):
    client,store,*_=backup_pilot;manager=client.app.state.backups;key=str(uuid.uuid4());audit=store.audit
    def fail(db,actor,action,payload):
        if action=='backup.request.accepted':raise RuntimeError('Synthetic acceptance audit rollback')
        return audit(db,actor,action,payload)
    monkeypatch.setattr(store,'audit',fail)
    with pytest.raises(RuntimeError):post(client,'/api/v1/backups',key)
    assert counts(manager)==(0,0) and not manager.busy
    assert client.get('/api/v1/backup-requests/'+key,headers=HEADERS).status_code==404


def test_done_failed_future_plus_unwritable_journal_gets_sql_failure_proof(backup_pilot,monkeypatch):
    client,store,*_=backup_pilot;manager=client.app.state.backups;key=str(uuid.uuid4())
    def fail_submit(fn,identifier):
        future=Future();future.set_exception(RuntimeError('Synthetic worker failure'));return future
    monkeypatch.setattr(manager.pool,'submit',fail_submit)
    response=post(client,'/api/v1/backups',key);identifier=response.json()['id']
    def fail(*args,**kwargs):raise OSError('Synthetic journal failure')
    monkeypatch.setattr('nxserver.backups.write_json',fail)
    assert not manager.busy;job=manager.get_job(identifier)
    assert job['status']=='failed' and job['worker_terminal_proven'] and job['journal_write_failed']
    assert get(store,'admin',key)['failure_observation']['worker_terminal_proven']
    assert not Backups(Store(store.root)).busy
    FUTURES.pop(identifier)  # Synthetic exception already asserted; no real worker.


def test_submit_failure_with_enqueued_callback_cannot_start_late_filesystem_work(backup_pilot,monkeypatch):
    client,store,*_=backup_pilot;manager=client.app.state.backups;queued=[]
    def enqueue_then_fail(fn,*args):queued.append((fn,args));raise OSError('Synthetic thread-start failure after enqueue')
    monkeypatch.setattr(manager.pool,'submit',enqueue_then_fail)
    response=post(client,'/api/v1/backups',str(uuid.uuid4()));assert response.status_code==202
    fn,args=queued[0];fn(*args)  # Later executor callback must observe the start fence.
    assert counts(manager)==(1,0) and manager.get_job(response.json()['id'])['start_failure_proven']


def test_missing_live_journal_waits_then_authoritative_owner_loss_interrupts_without_replay(backup_pilot,monkeypatch):
    client,store,*_=backup_pilot;manager=client.app.state.backups;key=str(uuid.uuid4())
    future=Future()
    monkeypatch.setattr(manager.pool,'submit',lambda *args:future)
    initial=post(client,'/api/v1/backups',key).json();identifier=initial['id']
    # Own isolated journal is deliberately absent; keep initial acceptance.
    (manager.jobs_root/identifier/'job.json').unlink();FUTURES.pop(identifier)
    owner=initial['owner_stamp'];monkeypatch.setattr('nxserver.backups.process_stamp',lambda pid:owner)
    restarted=Backups(Store(store.root));assert restarted.busy and restarted.get_job(identifier)['journal_missing']
    monkeypatch.setattr('nxserver.backups.process_stamp',lambda pid:None)
    assert not restarted.busy and restarted.get_job(identifier)['status']=='interrupted'
    assert get(store,'admin',key)['failure_observation']['owner_ended_proven']
    assert post(client,'/api/v1/backups',key).json()==initial and identifier not in FUTURES
    future.set_result(None)


def test_receipt_corruption_is_not_a_green_replay(backup_pilot):
    client,store,*_=backup_pilot;key=str(uuid.uuid4());response=post(client,'/api/v1/backups',key);finish(client,response)
    with store.db() as db:db.execute('UPDATE backup_requests SET response=?',('{"id":"wrong"}',))
    assert client.get('/api/v1/backup-requests/'+key,headers=HEADERS).status_code==409
    assert post(client,'/api/v1/backups',key).status_code==409
    assert 'invalid_backup_acceptance' in {issue['code'] for issue in scan(store)['errors']}


def test_snapshot_retains_own_acceptance_but_restore_treats_it_as_history_not_live_writer(backup_pilot):
    client,store,*_=backup_pilot;manager=client.app.state.backups;key=str(uuid.uuid4());initial=post(client,'/api/v1/backups',key);finish(client,initial)
    backup_id=manager.report(initial.json()['id'])['id'];plan=post(client,'/api/v1/backups/'+backup_id+'/restore-preview',str(uuid.uuid4()),{'name':'BR'});finish(client,plan)
    report=manager.report(plan.json()['id'])
    restored=post(client,'/api/v1/backups/'+backup_id+'/restore',str(uuid.uuid4()),{'name':'BR','preview_id':plan.json()['id'],'preview_hash':report['preview_hash'],'accept_unsigned':True});finish(client,restored)
    target=Store(manager.report(restored.json()['id'])['target_folder']);new=Backups(target)
    assert get(target,'admin',key)['result']==initial.json()
    job=new.get_job(initial.json()['id']);assert job['historical_origin'] and job['status']=='interrupted' and not new.busy
    assert new.create('backup','admin',{'accept_legacy_unbound':False},request_key=key)==initial.json() and not new.busy
    assert scan(target)['error_count']==0


def test_legacy_archive_without_acceptance_table_keeps_original_hash_before_restore_schema(backup_pilot):
    client,store,*_=backup_pilot;manager=client.app.state.backups
    with store.db() as db:db.execute('DROP TABLE backup_requests')  # Own isolated old-format database only.
    created=client.post('/api/v1/backups',headers=HEADERS,json={});assert finish(client,created)['status']=='completed'
    backup_id=manager.report(created.json()['id'])['id']
    plan=client.post('/api/v1/backups/'+backup_id+'/restore-preview',headers=HEADERS,json={'name':'BL'});finish(client,plan)
    report=manager.report(plan.json()['id'])
    restored=client.post('/api/v1/backups/'+backup_id+'/restore',headers=HEADERS,json={'name':'BL','preview_id':plan.json()['id'],'preview_hash':report['preview_hash'],'accept_unsigned':True})
    assert finish(client,restored)['status']=='completed'
    target=Store(manager.report(restored.json()['id'])['target_folder'])
    with target.db() as db:assert db.execute('SELECT count(*) FROM backup_requests').fetchone()[0]==0
    assert scan(target)['error_count']==0

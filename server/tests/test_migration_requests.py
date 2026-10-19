"""Durable acceptance, not successful CAM work; all inputs are synthetic/local."""
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor

import pytest

from nxserver.api import MigrationInput
from nxserver.integrity import scan
from nxserver.migration_requests import cancel,get
from nxserver.safety import DomainError
from nxserver.store import Store,canonical
from nxserver.upgrades import Upgrades,LIVE
from test_management import HEADERS
from test_upgrades import upgrade_pilot,preview,finish


def headers(key):return {**HEADERS,'Idempotency-Key':key}
def normalized(request):return MigrationInput.model_validate(request).model_dump()


def inert_worker(upgrades,monkeypatch):
    submitted=[]
    def submit(function,identifier):
        submitted.append(identifier);future=Future()
        # A never-started callback remains pending, not successfully ended.
        # monkeypatch removes only this synthetic LIVE entry before teardown.
        monkeypatch.setitem(LIVE,identifier,future)
        return future
    monkeypatch.setattr(upgrades.pool,'submit',submit)
    return submitted


def test_completed_build_replay_never_accesses_sources_or_overwrites_existing_target(upgrade_pilot,monkeypatch):
    client,store,request,source=upgrade_pilot;old_manifest=store.manifest(source)
    checked,report=preview(client,request);key=str(uuid.uuid4())
    data={**request,'preview_id':checked['id'],'preview_hash':report['preview_hash']}
    first=client.post('/api/v1/migrations/build',headers=headers(key),json=data)
    completed=finish(client,first);assert completed['status']=='completed'
    target=store.folders.targets['output']/ 'NX2512'
    before=store.manifest(target);audits=store.audit_entries()
    def forbidden(*args,**kwargs):raise AssertionError('Replay accessed source/prerequisites or resubmitted worker')
    monkeypatch.setattr('nxserver.upgrades.request_source',forbidden)
    monkeypatch.setattr(client.app.state.upgrades.pool,'submit',forbidden)
    again=client.post('/api/v1/migrations/build',headers=headers(key),json=data)
    assert again.status_code==202 and again.json()==first.json() and again.json()['status']=='queued'
    assert get(Store(store.root),'admin',key)['result']==first.json()
    assert len(store.releases())==1 and store.audit_entries()==audits
    assert store.manifest(source)==old_manifest and store.manifest(target)==before
    assert scan(store)['error_count']==0


@pytest.mark.parametrize('mutation',['name','target','preview','mode'])
def test_reused_key_different_inputs_or_preview_never_reaches_filesystem(upgrade_pilot,monkeypatch,mutation):
    client,store,request,source=upgrade_pilot;key=str(uuid.uuid4())
    first=client.post('/api/v1/migrations/preview',headers=headers(key),json=request);finish(client,first)
    data=dict(request);route='/api/v1/migrations/preview'
    if mutation=='name':data['name']='Changed name'
    elif mutation=='target':data['target_path']=r'W:\SiemensShare\nx2406'  # Literal only; replay must reject before lookup.
    elif mutation=='mode':route='/api/v1/migrations/extract';data={'version':'2512'}
    else:route='/api/v1/migrations/build';data.update(preview_id=first.json()['id'],preview_hash='0'*64)
    def forbidden(*args,**kwargs):raise AssertionError('Different-key replay accessed filesystem')
    monkeypatch.setattr('nxserver.upgrades.request_source',forbidden)
    before=store.audit_entries();response=client.post(route,headers=headers(key),json=data)
    assert response.status_code==409 and store.audit_entries()==before
    assert len(client.get('/api/v1/migrations',headers=HEADERS).json())==1


def test_two_independent_acceptors_commit_one_job_and_submit_one_worker(upgrade_pilot,monkeypatch):
    client,store,request,source=upgrade_pilot
    other=Store(store.root,store.sources,standards=store.standards,sidts=store.sidts,
        target_roots={'output':store.folders.targets['output']})
    one,two=Upgrades(store),Upgrades(other)
    first_calls=inert_worker(one,monkeypatch);second_calls=inert_worker(two,monkeypatch)
    key=str(uuid.uuid4());barrier=threading.Barrier(2)
    def accept(worker):barrier.wait(timeout=10);return worker.create(normalized(request),'preview','admin',request_key=key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(accept,one);b=pool.submit(accept,two);results=[a.result(timeout=20),b.result(timeout=20)]
    assert results[0]==results[1] and len(one.list())==1 and len(first_calls+second_calls)==1
    assert len([e for e in store.audit_entries() if e['action']=='migration.job.created'])==1
    assert scan(store)['error_count']==0


def test_rollback_before_audit_has_no_job_receipt_or_worker(upgrade_pilot,monkeypatch):
    client,store,request,source=upgrade_pilot;worker=client.app.state.upgrades;calls=inert_worker(worker,monkeypatch)
    key=str(uuid.uuid4());audit=store.audit
    def failure(*args,**kwargs):raise RuntimeError('Synthetic audit commit failure')
    monkeypatch.setattr(store,'audit',failure)
    with pytest.raises(RuntimeError,match='commit failure'):worker.create(normalized(request),'preview','admin',request_key=key)
    assert worker.list()==[] and calls==[]
    with store.db() as db:assert db.execute('SELECT count(*) FROM migration_requests').fetchone()[0]==0
    monkeypatch.setattr(store,'audit',audit);worker.create(normalized(request),'preview','admin',request_key=key)
    assert len(worker.list())==1 and len(calls)==1


def test_cancel_missing_acceptance_is_durable_but_cannot_stop_an_accepted_worker(upgrade_pilot,monkeypatch):
    client,store,request,source=upgrade_pilot;worker=client.app.state.upgrades;calls=inert_worker(worker,monkeypatch)
    key=str(uuid.uuid4());before=store.audit_entries()
    assert client.get('/api/v1/migration-requests/'+key,headers=HEADERS).status_code==404
    assert store.audit_entries()==before
    erp_headers={'Authorization':'Bearer isolated-upgrade-erp-'+'e'*40}
    assert client.get('/api/v1/migration-requests/'+key,headers=erp_headers).status_code==403
    assert client.post('/api/v1/migration-requests/'+key+'/cancel',headers=erp_headers).status_code==403
    assert client.post('/api/v1/migration-requests/'+key+'/cancel').status_code==401
    fence=cancel(store,'admin',key);audits=store.audit_entries()
    assert fence['state']=='cancelled' and cancel(store,'admin',key)==fence
    assert store.audit_entries()==audits and get(Store(store.root),'admin',key)==fence
    assert client.post('/api/v1/migrations/preview',headers=headers(key),json=request).status_code==409
    assert worker.list()==[] and calls==[]
    accepted_key=str(uuid.uuid4());accepted=worker.create(normalized(request),'preview','admin',request_key=accepted_key)
    receipt=cancel(store,'admin',accepted_key)
    assert receipt['state']=='accepted' and receipt['result']==accepted and len(calls)==1
    assert worker.get(accepted['id'])['status']=='queued'


def test_fence_during_validation_prevents_late_acceptance(upgrade_pilot,monkeypatch):
    client,store,request,source=upgrade_pilot;worker=client.app.state.upgrades;calls=inert_worker(worker,monkeypatch)
    entered=threading.Event();continue_validation=threading.Event();key=str(uuid.uuid4())
    def delayed(*args):entered.set();assert continue_validation.wait(timeout=10);return {'ready':True}
    monkeypatch.setattr('nxserver.upgrades.prerequisites',delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        attempt=pool.submit(worker.create,normalized(request),'preview','admin',request_key=key)
        assert entered.wait(timeout=10);assert cancel(store,'admin',key)['state']=='cancelled'
        continue_validation.set()
        with pytest.raises(DomainError,match='fenced'):attempt.result(timeout=20)
    assert worker.list()==[] and calls==[]


def test_cancel_serializes_with_already_committing_acceptance(upgrade_pilot,monkeypatch):
    client,store,request,source=upgrade_pilot;worker=client.app.state.upgrades;calls=inert_worker(worker,monkeypatch)
    reader=Store(store.root);entered=threading.Event();commit=threading.Event();key=str(uuid.uuid4());audit=store.audit
    def delayed(*args,**kwargs):entered.set();assert commit.wait(timeout=10);return audit(*args,**kwargs)
    monkeypatch.setattr(store,'audit',delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempt=pool.submit(worker.create,normalized(request),'preview','admin',request_key=key)
        assert entered.wait(timeout=10)
        fence=pool.submit(cancel,reader,'admin',key);commit.set()
        accepted=attempt.result(timeout=20);receipt=fence.result(timeout=20)
    assert receipt['state']=='accepted' and receipt['result']==accepted and len(calls)==1


def test_submit_failure_is_terminal_but_acceptance_replay_never_resubmits(upgrade_pilot,monkeypatch):
    client,store,request,source=upgrade_pilot;worker=client.app.state.upgrades;key=str(uuid.uuid4());calls=[]
    def fail(*args):calls.append(True);raise RuntimeError('Synthetic executor unavailable')
    monkeypatch.setattr(worker.pool,'submit',fail)
    first=client.post('/api/v1/migrations/preview',headers=headers(key),json=request)
    assert first.status_code==202 and get(store,'admin',key)['state']=='accepted'
    job=worker.get(first.json()['id']);assert job['status']=='failed' and job['worker_submission_failed']
    assert client.post('/api/v1/migrations/preview',headers=headers(key),json=request).json()==first.json()
    assert len(calls)==1 and scan(store)['error_count']==0


def test_confirmed_owner_loss_marks_interrupted_but_replay_never_restarts(upgrade_pilot,monkeypatch):
    client,store,request,source=upgrade_pilot;worker=client.app.state.upgrades;calls=inert_worker(worker,monkeypatch)
    key=str(uuid.uuid4());first=worker.create(normalized(request),'preview','admin',request_key=key)
    monkeypatch.delitem(LIVE,first['id'])  # Simulate a different process, not a live local future.
    monkeypatch.setattr('nxserver.upgrades.process_stamp',lambda pid:None)  # Explicit isolated owner-death evidence.
    restarted=Upgrades(store)
    assert restarted.get(first['id'])['status']=='interrupted'
    def forbidden(*args):raise AssertionError('Recovery/replay must not resubmit a worker')
    monkeypatch.setattr(restarted.pool,'submit',forbidden)
    before=store.audit_entries()
    assert restarted.create(normalized(request),'preview','admin',request_key=key)==first
    assert len(calls)==1 and restarted.get(first['id'])['status']=='interrupted' and store.audit_entries()==before


@pytest.mark.parametrize('tamper',['receipt','job'])
def test_corrupt_receipt_or_original_job_is_not_claimed_accepted(upgrade_pilot,tamper):
    client,store,request,source=upgrade_pilot;key=str(uuid.uuid4())
    first=client.post('/api/v1/migrations/preview',headers=headers(key),json=request);finish(client,first)
    if tamper=='receipt':
        value=first.json();value['request']['name']='Corrupted acceptance'
        with store.db() as db:db.execute('UPDATE migration_requests SET response=?',(canonical(value),))
    else:
        value=client.app.state.upgrades.get(first.json()['id']);value['request']['name']='Corrupted job'
        with store.db() as db:db.execute('UPDATE migration_jobs SET payload=?',(canonical(value),))
    assert client.get('/api/v1/migration-requests/'+key,headers=HEADERS).status_code==409
    assert client.post('/api/v1/migrations/preview',headers=headers(key),json=request).status_code==409
    assert 'invalid_migration_receipt' in {i['code'] for i in scan(store)['errors']}


def test_extraction_replay_uses_original_acceptance_not_changed_installer(tmp_path,monkeypatch):
    from test_extraction import fake_setup
    store,archive,calls=fake_setup(tmp_path,monkeypatch);worker=Upgrades(store);key=str(uuid.uuid4())
    first=worker.create_extraction('2512','admin',request_key=key);LIVE[first['id']].result(timeout=10)
    assert worker.get(first['id'])['status']=='completed';before=list(calls)
    archive.write_bytes(b'Changed synthetic installer; never executable')
    assert worker.create_extraction('2512','admin',request_key=key)==first and calls==before
    assert store.sidt_root('2512') is None


@pytest.mark.parametrize('legacy',[False,True])
def test_backup_restore_keeps_acceptance_or_accepts_old_archive_without_table(upgrade_pilot,legacy):
    from nxserver.backups import FUTURES
    client,store,request,source=upgrade_pilot;key=str(uuid.uuid4())
    first=client.post('/api/v1/migrations/preview',headers=HEADERS if legacy else headers(key),json=request);finish(client,first)
    if legacy:
        with store.db() as db:db.execute('DROP TABLE migration_requests')  # Only own synthetic old archive fixture.
    def complete(response):
        assert response.status_code==202,response.text
        identifier=response.json()['id'];FUTURES[identifier].result(timeout=30)
        assert client.app.state.backups.get_job(identifier)['status']=='completed'
        return identifier,client.app.state.backups.report(identifier)
    _,receipt=complete(client.post('/api/v1/backups',headers=HEADERS,json={}))
    preview_id,plan=complete(client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',headers=HEADERS,json={'name':'R'}))
    assert plan['can_restore'],plan['errors']
    _,report=complete(client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,json={
        'name':'R','preview_id':preview_id,'preview_hash':plan['preview_hash'],'accept_unsigned':True}))
    restored=Store(report['target_folder']);worker=Upgrades(restored)
    if not legacy:
        assert get(restored,'admin',key)['result']==first.json()
        before=restored.audit_entries()
        assert worker.create(normalized(request),'preview','admin',request_key=key)==first.json()
        assert restored.audit_entries()==before
    else:
        with restored.db() as db:assert db.execute('SELECT count(*) FROM migration_requests').fetchone()[0]==0
    assert restored.active()['release_id'] is None and scan(restored)['error_count']==0

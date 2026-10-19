"""Ongoing job evidence uses only isolated stores and synthetic runtime state."""
import json
import uuid
from concurrent.futures import Future

import pytest

from nxserver.backups import Backups
from nxserver.migration_requests import get as receipt
from nxserver.safety import DomainError
from nxserver.store import Store,canonical
from nxserver.upgrades import LIVE,Upgrades,writer_active


@pytest.fixture
def worker(tmp_path,monkeypatch):
    manager=Upgrades(Store(tmp_path/'managed'))
    def submit(function,identifier):
        future=Future();monkeypatch.setitem(LIVE,identifier,future)
        return future
    monkeypatch.setattr(manager.pool,'submit',submit)
    yield manager
    manager.pool.shutdown(wait=True)


def accept(worker,mode='preview'):
    key=str(uuid.uuid4())
    job=worker._accept({'name':'Synthetic observation'},mode,'admin',lambda:{},request_key=key)
    return job,key,LIVE[job['id']]


def raw(worker,identifier):
    with worker.store.db() as db:
        return json.loads(db.execute('SELECT payload FROM migration_jobs WHERE id=?',(identifier,)).fetchone()[0])


def persist(worker,job):
    with worker.store.db() as db:
        db.execute('UPDATE migration_jobs SET payload=? WHERE id=?',(canonical(job),job['id']))


@pytest.mark.parametrize('end',['exception','cancel','return'])
def test_unexpected_end_is_observed_once_without_reexecution(worker,monkeypatch,end):
    job,key,future=accept(worker)
    assert worker.get(job['id'])['execution']['writer_may_be_active']
    if end=='exception':future.set_exception(RuntimeError('Synthetic private diagnostic must not enter response'))
    elif end=='cancel':assert future.cancel()
    else:future.set_result(None)
    value=worker.get(job['id'])
    assert value['status']=='failed' and value['partial_output_retained']
    assert value['execution_observation']['terminal_evidence_verified']
    assert not value['execution']['writer_may_be_active']
    assert 'private diagnostic' not in canonical(value)
    assert 'execution' not in raw(worker,job['id'])
    assert receipt(worker.store,'admin',key)['result']==job
    before=worker.store.audit_entries()
    assert worker.get(job['id'])==value and worker.list()==[value]
    assert worker.store.audit_entries()==before
    def forbidden(*args,**kwargs):raise AssertionError('Observed ended task was resubmitted')
    monkeypatch.setattr(worker.pool,'submit',forbidden)
    assert worker._accept(job['request'],'preview','admin',forbidden,request_key=key)==job
    assert not (worker.store.root/'upgrades').exists()


@pytest.mark.parametrize('persisted',['completed','failed','interrupted'])
def test_live_future_overrides_terminal_projection_and_blocks_all_writers(worker,persisted):
    job,key,future=accept(worker);worker._update(job['id'],status=persisted,finished_at='Synthetic terminal flag')
    before=worker.store.audit_entries();value=worker.get(job['id'])
    assert value['status']=='registering' and value['execution']['persisted_status']==persisted
    assert raw(worker,job['id'])['status']==persisted and 'execution' not in raw(worker,job['id'])
    assert worker.store.audit_entries()==before
    with pytest.raises(DomainError,match='active'):
        worker._accept({'name':'Another'},'preview','admin',lambda:{},request_key=str(uuid.uuid4()))
    with pytest.raises(DomainError,match='active'):
        Backups(worker.store).create('backup','admin',request_key=str(uuid.uuid4()))
    assert receipt(worker.store,'admin',key)['result']==job
    future.set_result(None)
    assert worker.get(job['id'])['status']==persisted


@pytest.mark.parametrize('child',['live','unknown','gone','reused'])
def test_done_future_does_not_hide_reader_that_may_still_run(worker,monkeypatch,child):
    job,key,future=accept(worker)
    worker._update(job['id'],child_pid=23456,child_stamp='child-birth')
    future.set_exception(RuntimeError('Synthetic outer worker failure'))
    def stamp(pid):
        if pid==23456:return {'live':'child-birth','unknown':'unknown','gone':None,'reused':'other-birth'}[child]
        return None  # Confirmed lost owner, not a timeout.
    monkeypatch.setattr('nxserver.upgrades.process_stamp',stamp)
    value=worker.get(job['id']);active=child in ('live','unknown')
    assert value['status']==('queued' if active else 'failed')
    assert value['execution']['writer_may_be_active']==active
    assert writer_active(worker.store,raw(worker,job['id']))==active
    if active:
        with pytest.raises(DomainError,match='active'):
            Backups(worker.store).create('backup','admin',request_key=str(uuid.uuid4()))
        monkeypatch.setattr('nxserver.upgrades.process_stamp',lambda pid:None)
        assert worker.get(job['id'])['status']=='failed'


@pytest.mark.parametrize('child',['live','unknown'])
def test_reader_live_after_terminal_flag_blocks_acceptance_without_future(worker,monkeypatch,child):
    job,key,future=accept(worker);future.set_result(None)
    monkeypatch.delitem(LIVE,job['id'])
    worker._update(job['id'],status='completed',child_pid=23456,child_stamp='reader-birth')
    monkeypatch.setattr('nxserver.upgrades.process_stamp',lambda pid:'reader-birth' if child=='live' else 'unknown')
    assert worker.get(job['id'])['status']=='extracting'
    with pytest.raises(DomainError,match='active'):
        worker._accept({'name':'Other'},'preview','admin',lambda:{},request_key=str(uuid.uuid4()))
    with pytest.raises(DomainError,match='active'):
        Backups(worker.store).create('backup','admin',request_key=str(uuid.uuid4()))
    assert raw(worker,job['id'])['status']=='completed'


@pytest.mark.parametrize('owner',['live','unknown'])
def test_owner_loss_later_is_observed_without_server_restart(worker,monkeypatch,owner):
    job,key,future=accept(worker);monkeypatch.delitem(LIVE,job['id'])
    monkeypatch.setattr('nxserver.upgrades.process_stamp',lambda pid:job['owner_stamp'] if owner=='live' else 'unknown')
    first=worker.get(job['id'])
    assert first['status']=='queued' and first['execution']['writer_may_be_active']
    monkeypatch.setattr('nxserver.upgrades.process_stamp',lambda pid:None)
    value=worker.get(job['id'])
    assert value['status']=='interrupted' and not value['execution']['writer_may_be_active']
    assert receipt(worker.store,'admin',key)['result']==job
    before=worker.store.audit_entries();worker.list();assert worker.store.audit_entries()==before


def test_legacy_missing_owner_is_uncertainty_not_exit(worker):
    identifier=str(uuid.uuid4())
    with worker.store.db() as db:
        db.execute('INSERT INTO migration_jobs VALUES(?,?)',(identifier,canonical({
            'id':identifier,'status':'queued','mode':'preview','actor':'admin','request':{}})))
    value=worker.get(identifier)
    assert value['status']=='queued' and value['execution']['owner']=='not_recorded'
    assert value['execution']['writer_may_be_active'] and 'execution_observation' not in raw(worker,identifier)


def test_live_future_wins_over_apparent_owner_loss(worker,monkeypatch):
    job,key,future=accept(worker)
    monkeypatch.setattr('nxserver.upgrades.process_stamp',lambda pid:None)
    assert worker.get(job['id'])['status']=='queued'
    assert worker.get(job['id'])['execution']['future']=='live'
    future.set_result(None)
    assert worker.get(job['id'])['status']=='failed'


def test_recovered_historical_job_never_borrows_original_future_or_process(worker,monkeypatch):
    job,key,future=accept(worker)
    worker._update(job['id'],status='completed',child_pid=23456,child_stamp='birth')
    worker.store.recovered_job_ids={job['id']}
    def forbidden(*args):raise AssertionError('Historical recovered job inspected original process')
    monkeypatch.setattr('nxserver.upgrades.process_stamp',forbidden)
    before=worker.store.audit_entries();value=worker.get(job['id'])
    assert value['status']=='completed' and value['execution']['historical']
    assert not value['execution']['writer_may_be_active']
    assert worker.store.audit_entries()==before
    assert not writer_active(worker.store,raw(worker,job['id']))
    future.set_result(None)


def test_progress_update_reads_fresh_sql_payload_not_a_projected_get(worker,monkeypatch):
    job,key,future=accept(worker)
    other=Store(worker.store.root)
    with other.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT payload FROM migration_jobs WHERE id=?',(job['id'],)).fetchone()
        value=json.loads(row[0]);value['independent_writer_marker']={'retained':True}
        db.execute('UPDATE migration_jobs SET payload=? WHERE id=?',(canonical(value),job['id']))
    def forbidden(*args):raise AssertionError('Progress update read response projection via separate connection')
    monkeypatch.setattr(worker,'get',forbidden)
    worker._update(job['id'],progress=32,message='Synthetic progress')
    value=raw(worker,job['id'])
    assert value['independent_writer_marker']=={'retained':True} and value['progress']==32
    assert 'execution' not in value
    future.set_result(None)


def test_corrupt_domain_result_is_not_promoted_to_an_execution_success(worker):
    job,key,future=accept(worker,mode='catalog');future.set_result(None)
    worker._update(job['id'],status='completed',domain_result={'contract_version':1,'mode':'catalog',
        'status':'draft','nx_files_changed':False,'resources':[{'id':str(uuid.uuid4()),'revision':1}]})
    before=worker.store.audit_entries()
    with pytest.raises(DomainError,match='immutable versions'):worker.get(job['id'])
    assert worker.store.audit_entries()==before and raw(worker,job['id'])['status']=='completed'


def test_integrity_does_not_finalize_live_writer_or_borrow_original_future(worker):
    from nxserver.integrity import scan
    job,key,future=accept(worker);worker._update(job['id'],status='completed')
    value=scan(worker.store)
    assert 'writer_in_progress' in {item['code'] for item in value['warnings']}
    assert not any(item['code']=='artifact' for item in value['errors'])
    # Historical provenance must disable even a live same-ID original Future.
    worker.store.recovered_job_ids={job['id']}
    historical=scan(worker.store)
    assert 'state_hash' in {item['code'] for item in historical['errors']}
    assert 'writer_in_progress' not in {item['code'] for item in historical['warnings']}
    future.set_result(None)

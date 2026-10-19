"""Read-only verification, corruption detection and externally pinned checkpoints."""
import copy
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.integrity import IntegrityJobs,scan
from nxserver.store import Store,TOOL_DATABASE,canonical,now
from nxserver.safety import DomainError
from test_management import ADMIN,ERP,HEADERS,ERP_HEADERS,pilot,imported,tool_payload
from test_libraries import write,simple,resource_body,native_pilot,normalized
from test_upgrades import upgrade_pilot


def codes(report):return {item['code'] for item in report['errors']+report['warnings']}


def minimal(tmp_path):
    source=tmp_path/'source';write(source,TOOL_DATABASE,simple())
    store=Store(tmp_path/'managed',{'test':source})
    release=store.import_release('test','2512','Disposable release','fixture')
    resource=store.save_resource({'kind':'gauge','name':'Gauge','cam':{},'cam_status':'draft','erp':None,'asset_ids':[]},'fixture')
    return store,release,resource


def test_new_bindings_verify_without_writing_tables_or_files(tmp_path):
    store,release,resource=minimal(tmp_path)
    asset=store.add_asset('Gauge.pdf',b'Synthetic PDF','fixture')
    store.save_resource({**resource_body(resource),'asset_ids':[asset['id']]},'fixture',resource['id'],1)
    log=store.add_log('nx.syslog',b'UG_APP_MANUFACTURING synthetic record','fixture')
    audit=store.audit_entries();manifest=store.manifest(Path(release['folder']));history=store.history(resource['id'])
    report=scan(store)
    assert report['status']=='passed',report
    assert report['audit']['valid'] and report['audit']['entries']==len(audit)
    assert report['metadata']['legacy_unbound_rows']==0
    assert report['files']['files']==len(manifest)+3  # asset, log and launcher ZIP
    assert not report['scope']['automatic_repair'] and not report['trust']['signed']
    assert store.audit_entries()==audit and store.history(resource['id'])==history
    assert store.manifest(Path(release['folder']))==manifest and store.active()['generation']==0
    assert scan(store,anchor=report['checkpoint'])['status']=='passed'


@pytest.mark.parametrize('change',['actor','action','payload','time','previous_hash','hash','middle_delete','tail_delete'])
def test_entire_audit_chain_not_just_last_two_hundred_is_verified(tmp_path,change):
    store,release,resource=minimal(tmp_path)
    for index in range(205):
        with store.db() as db:store.audit(db,'fixture','diagnostic.marker',{'index':index})
    with store.db() as db:
        if change=='middle_delete':db.execute('DELETE FROM audit WHERE sequence=2')
        elif change=='tail_delete':db.execute('DELETE FROM audit WHERE sequence=(SELECT max(sequence) FROM audit)')
        else:
            column='created_at' if change=='time' else change
            db.execute('UPDATE audit SET '+column+'=? WHERE sequence=1',('changed',))
    report=scan(store,include_files=False)
    assert report['status']=='failed' and not report['audit']['valid']
    assert not report['checkpoint_available']
    assert 'audit_tail' in codes(report) if change=='tail_delete' else report['error_count']>0


def test_rollback_recomputed_history_and_row_deletion_are_detected_by_checkpoint(tmp_path):
    store,release,resource=minimal(tmp_path);anchor=scan(store)['checkpoint']
    # Even a fully recalculated chain is not the separately pinned original.
    with store.db() as db:
        rows=[dict(row) for row in db.execute('SELECT * FROM audit ORDER BY sequence')]
        db.execute('DELETE FROM audit');db.execute("DELETE FROM sqlite_sequence WHERE name='audit'")
        for row in rows:store.audit(db,'different actor',row['action'],json.loads(row['payload']))
    assert 'anchor_mismatch' in codes(scan(store,anchor=anchor))
    with store.db() as db:db.execute('DELETE FROM resource_versions WHERE id=?',(resource['id'],))
    result=scan(store,anchor=anchor)
    assert 'missing_bound_row' in codes(result) and 'revision_gap' in codes(result)


@pytest.mark.parametrize('target',['current_payload','history_payload','both_payloads','external_key','delete_history','huge_revision','duplicate_identity'])
def test_current_and_historical_resource_tampering_are_not_misreported_as_verified(tmp_path,target):
    store,release,resource=minimal(tmp_path)
    with store.db() as db:
        if target in ('current_payload','history_payload','both_payloads'):
            payload=resource_body(resource);payload['name']='Changed outside API'
            if target in ('current_payload','both_payloads'):db.execute('UPDATE resources SET payload=? WHERE id=?',(canonical(payload),resource['id']))
            if target in ('history_payload','both_payloads'):db.execute('UPDATE resource_versions SET payload=? WHERE id=?',(canonical(payload),resource['id']))
        elif target=='external_key':db.execute('UPDATE resources SET external_key=? WHERE id=?',('incorrect',resource['id']))
        elif target=='delete_history':db.execute('DELETE FROM resource_versions WHERE id=?',(resource['id'],))
        elif target=='huge_revision':db.execute('UPDATE resources SET revision=1000000000 WHERE id=?',(resource['id'],))
        else:
            # Invalid CAM payload is identified in addition to its changed row hash.
            payload=resource_body(resource);payload['cam']={'library_id':'unknown'}
            db.execute('UPDATE resources SET payload=? WHERE id=?',(canonical(payload),resource['id']))
    result=scan(store,include_files=False)
    assert result['status']=='failed' and result['error_count']>0 and not result['checkpoint_available']
    assert ('missing_bound_row' if target=='delete_history' else 'state_hash') in codes(result)


@pytest.mark.parametrize('kind',['release','asset','log','launcher'])
def test_owned_artifact_mutations_are_reported_without_touching_the_original_source(tmp_path,kind):
    store,release,resource=minimal(tmp_path)
    source_before=store.manifest(store.sources['test'])
    if kind=='release':path=Path(release['folder'])/TOOL_DATABASE
    elif kind=='asset':
        asset=store.add_asset('Gauge.pdf',b'Synthetic PDF','fixture');path=store.asset(asset['id'])[1]
    elif kind=='log':
        log=store.add_log('nx.syslog',b'Synthetic protocol','fixture');path=store.root/'evidence'/log['id']/'nx.syslog'
    else:path=store.test_launcher_package(release['id'])
    path.write_bytes(path.read_bytes()+b'Changed outside API')
    result=scan(store)
    assert result['status']=='failed' and 'artifact_integrity' in codes(result)
    assert store.manifest(store.sources['test'])==source_before


def test_old_unbound_audits_are_reported_honestly_and_checkpoint_can_pin_a_reviewed_baseline(tmp_path):
    store=Store(tmp_path/'managed')
    payload={'kind':'gauge','name':'Old fixture','cam':{},'cam_status':'draft','erp':None,'asset_ids':[]}
    import uuid,hashlib
    identifier=str(uuid.uuid4());stamp=now()
    # Synthetic legacy database: valid old audit algorithm, no new _state field.
    text=canonical({'id':identifier,'revision':1,'payload':payload})
    digest=hashlib.sha256(canonical(['0'*64,'fixture','resource.saved',text,stamp]).encode()).hexdigest()
    with store.db() as db:
        db.execute('INSERT INTO resources VALUES(?,?,?,?,?)',(identifier,1,canonical(payload),None,stamp))
        db.execute('INSERT INTO resource_versions VALUES(?,?,?,?,?)',(identifier,1,canonical(payload),'fixture',stamp))
        db.execute('INSERT INTO audit(actor,action,payload,created_at,previous_hash,hash) VALUES(?,?,?,?,?,?)',('fixture','resource.saved',text,stamp,'0'*64,digest))
    result=scan(store)
    assert result['status']=='partial' and result['audit']['valid'] and result['metadata']['legacy_unbound_rows']==2
    assert result['checkpoint_available'] and not result['trust']['historical_authenticity_proven']
    assert scan(store,anchor=result['checkpoint'])['status']=='passed'
    store.save_resource({**payload,'name':'Legitimate new revision'},'fixture',identifier,1)
    assert scan(store,anchor=result['checkpoint'])['status']=='passed'


def test_new_columns_are_not_silently_covered_by_old_hash_bindings(tmp_path):
    store,release,resource=minimal(tmp_path)
    with store.db() as db:db.execute('ALTER TABLE resources ADD COLUMN future_column TEXT')
    result=scan(store)
    assert result['status']=='partial' and 'unbound_columns' in codes(result)
    assert result['error_count']==0
    store.save_resource({**resource_body(resource),'name':'Fresh binding includes the new management column'},'fixture',resource['id'],1)
    assert scan(store)['status']=='passed'


def test_protected_release_pointer_is_rejected_before_any_file_lookup(tmp_path,monkeypatch):
    store,release,resource=minimal(tmp_path)
    with store.db() as db:
        db.execute('UPDATE releases SET folder=? WHERE id=?',(r'W:\SiemensShare\nx2406',release['id']))
        # Simulate a deliberately re-bound pointer; even authenticated metadata
        # must not expand the allowed artifact roots / production authority.
        store.audit(db,'fixture','release.validated',{'id':release['id']})
    original=Path.lstat
    def guarded(path,*args,**kwargs):
        assert not str(path).lower().startswith(('w:','/mnt/w'))
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'lstat',guarded)
    result=scan(store)
    assert result['status']=='failed' and 'artifact_integrity' in codes(result)


def test_writers_make_an_observation_stale_instead_of_false_corruption_or_green(tmp_path):
    store,release,resource=minimal(tmp_path);mutated=False
    def progress(phase,message):
        nonlocal mutated
        if phase=='verifying' and not mutated:
            mutated=True;store.save_resource({**resource_body(resource),'name':'Legitimate concurrent update'},'fixture',resource['id'],1)
    result=scan(store,progress=progress)
    assert result['status']=='stale' and not result['stable_database_snapshot'] and not result['checkpoint_available']


def test_catalog_import_bindings_and_erp_upserts_are_covered(native_pilot):
    client,store,source=native_pilot;release=normalized(client);url='/api/v1/releases/'+release['id']
    body={'libraries':['tool-metric'],'include_standard':True}
    proposal=client.post(url+'/catalog-preview',headers=HEADERS,json=body).json()
    assert client.post(url+'/catalog-import',headers=HEADERS,json={**body,'preview_hash':proposal['preview_hash']}).status_code==200
    erp={'kind':'gauge','company_id':'company','system_id':'system','number':'G-1','description':'Gauge','unit':'PCS','source_revision':'1'}
    with TestClient(create_app(store,ADMIN,ERP)) as erp_client:
        created=erp_client.post('/api/v1/erp/resources/upsert',headers=ERP_HEADERS,json=erp)
        assert created.status_code==200,created.text
        value=created.json()
        assert erp_client.post('/api/v1/erp/resources/upsert',headers=ERP_HEADERS,json={**erp,'expected_revision':value['revision'],'source_revision':'2'}).status_code==200
    result=scan(store)
    assert result['status']=='passed',result


def wait(client,identifier):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        value=client.get('/api/v1/integrity/'+identifier,headers=HEADERS).json()
        if value['status']!='running':return value
        time.sleep(0.01)
    raise AssertionError('Owned integrity job still running after test wait.')


def test_read_only_job_api_roles_strict_shapes_and_session_lifetime(tmp_path):
    store,release,resource=minimal(tmp_path)
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        assert client.post('/api/v1/integrity',json={}).status_code==401
        assert client.post('/api/v1/integrity',headers=ERP_HEADERS,json={}).status_code==403
        for body in ({'include_files':'yes'},{'path':r'W:\SiemensShare\nx2406'},
            {'anchor':{'schema_version':True,'audit':{'sequence':0,'hash':'0'*64},'tables':{}}}):
            assert client.post('/api/v1/integrity',headers=HEADERS,json=body).status_code==422
        response=client.post('/api/v1/integrity',headers=HEADERS,json={})
        assert response.status_code==202,response.text
        result=wait(client,response.json()['id']);assert result['report']['status']=='passed',result
        anchor=result['report']['checkpoint']
        anchor['tables']['not_a_table']={'columns':['id'],'rows':[{'key':['not-a-path'],'sha256':'0'*64}]}
        assert client.post('/api/v1/integrity',headers=HEADERS,json={'anchor':anchor}).status_code==400
        assert client.get('/api/v1/integrity',headers=HEADERS).json()[0]['id']==response.json()['id']
    assert IntegrityJobs(store).list()==[]


def test_step_job_packages_and_diagnostics_remain_bound_to_historical_inputs(tmp_path):
    from nxserver.geometry import GeometryJobs
    from test_geometry import STEP,report_for
    store=Store(tmp_path/'managed');asset=store.add_asset('Tool.step',STEP,'fixture')
    resource=store.save_resource({'kind':'tool','name':'STEP tool','cam':{},'cam_status':'draft',
        'erp':None,'asset_ids':[asset['id']]},'fixture')
    jobs=GeometryJobs(store);job=jobs.create(resource['id'],1,asset['id'],'drilling_solid','fixture')
    result=scan(store);assert result['status']=='passed',result
    jobs.submit_report(job['id'],report_for(job),'fixture')
    # Editing a catalog resource makes its old job obsolete, not corrupted.
    store.save_resource({**resource_body(resource),'name':'A later draft'},'fixture',resource['id'],1)
    result=scan(store);assert result['status']=='passed',result
    package=store.root/'geometry-packages'/job['id']/'NX_STEP.zip';package.write_bytes(package.read_bytes()+b'Changed')
    assert 'artifact_integrity' in codes(scan(store))


def test_extraction_report_manifest_input_and_selected_pointer_are_verified(tmp_path,monkeypatch):
    from test_extraction import fake_setup
    from nxserver.upgrades import Upgrades,LIVE
    store,archive,calls=fake_setup(tmp_path,monkeypatch);upgrades=Upgrades(store)
    job=upgrades.create_extraction('2512','fixture');LIVE[job['id']].result(timeout=10)
    upgrades.select_extraction(job['id'],0,'fixture')
    result=scan(store);assert result['status']=='passed',result
    listing=store.root/'upgrades'/job['id']/'extraction/listing.txt';listing.write_bytes(b'Tampered diagnostic')
    result=scan(store);assert result['status']=='failed' and 'artifact_integrity' in codes(result)


def test_completed_preview_build_and_recovery_jobs_have_terminal_bindings(upgrade_pilot):
    from test_upgrades import preview,finish
    client,store,request,source=upgrade_pilot
    job,report=preview(client,request)
    value=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,
        json={**request,'preview_id':job['id'],'preview_hash':report['preview_hash']}))
    assert value['status']=='completed'
    result=scan(store);assert result['status']=='passed',result


def test_only_one_live_read_job_no_observation_timeout_restart_and_thread_failure_reconciliation(tmp_path,monkeypatch):
    import threading
    store=Store(tmp_path/'managed');gate=threading.Event();started=threading.Event()
    def held(*args):started.set();gate.wait(timeout=10);return scan(store)
    monkeypatch.setattr('nxserver.integrity.scan',held)
    jobs=IntegrityJobs(store);job=jobs.create(True,None)
    assert started.wait(timeout=2) and not jobs.futures[job['id']].done()
    assert jobs.get(job['id'])['status']=='running'
    with pytest.raises(DomainError,match='already running'):jobs.create(True,None)
    gate.set();jobs.futures[job['id']].result(timeout=10)
    assert jobs.get(job['id'])['status']=='completed'
    # A terminated future is evidence, not a stored running flag.
    from concurrent.futures import Future
    jobs.jobs[job['id']]['status']='running';future=Future();future.set_exception(RuntimeError('Synthetic failure'))
    jobs.futures[job['id']]=future
    assert jobs.get(job['id'])['status']=='failed'


@pytest.mark.parametrize('case',['active_missing','active_negative','parent_cycle','approval_without_review'])
def test_state_graph_invariants_are_checked_even_after_a_hash_binding(tmp_path,case):
    store,release,resource=minimal(tmp_path)
    with store.db() as db:
        if case=='active_missing':db.execute('DELETE FROM active')
        elif case=='active_negative':
            db.execute('UPDATE active SET generation=-1');store.audit(db,'fixture','release.activated',{'id':release['id']})
        elif case=='parent_cycle':
            db.execute('UPDATE releases SET parent_id=id');store.audit(db,'fixture','release.validated',{'id':release['id']})
        else:
            db.execute("UPDATE releases SET status='approved'");store.audit(db,'fixture','release.approved',{'id':release['id']})
    report=scan(store)
    assert report['status']=='failed'
    assert {'active_singleton','active_generation','release_cycle','approval_evidence'}&codes(report)


def test_byte_consistency_does_not_erase_a_known_blocked_validation(tmp_path):
    store,release,resource=minimal(tmp_path)
    with store.db() as db:
        db.execute("UPDATE releases SET status='blocked',validation=? WHERE id=?",(canonical({'integrity_passed':False,'errors':['Synthetic PAX fault']}),release['id']))
        store.audit(db,'fixture','release.validated',{'id':release['id']})
    report=scan(store)
    assert report['status']=='partial' and report['error_count']==0 and 'blocked_release' in codes(report)
    assert store.get_release(release['id'])['status']=='blocked'

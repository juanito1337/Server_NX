"""Backup/recovery proofs use isolated synthetic data, never real NX or W:."""
import hashlib
import json
import sqlite3
import threading
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.backups import Backups, FUTURES, read_json, write_json
from nxserver.geometry import GeometryJobs
from nxserver.integrity import scan
from nxserver.safety import DomainError
from nxserver.store import Store, FUNCTIONAL_TESTS, canonical, sha256
from test_geometry import STEP, report_for
from test_libraries import simple,write
from nxserver.libraries import SPECS

ADMIN='isolated-backup-admin-'+'a'*40
ERP='isolated-backup-erp-'+'e'*40
HEADERS={'Authorization':'Bearer '+ADMIN}
ERP_HEADERS={'Authorization':'Bearer '+ERP}


@pytest.fixture
def backup_pilot(tmp_path):
    source=tmp_path/'source';write(source,SPECS['tool-metric']['path'],simple())
    store=Store(tmp_path/'managed',{'test':source})
    release=store.import_release('test','2512','Synthetic release','fixture')
    asset=store.add_asset('vendor.step',STEP,'fixture')
    resource=store.save_resource({'kind':'tool','name':'Synthetic draft','cam_status':'draft','cam':{},
                                 'asset_ids':[asset['id']],'erp':None},'fixture')
    geometry=GeometryJobs(store).create(resource['id'],1,asset['id'],'drilling_solid','fixture')
    GeometryJobs(store).submit_report(geometry['id'],report_for(geometry),'fixture')
    store.add_log('test.syslog',b'Synthetic log, no NX proof','fixture')
    # Failed/orphan diagnostics and empty directories are part of the snapshot.
    write(store.root,'geometry-packages/orphan/partial.txt','Partial forensic artifact')
    (store.root/'geometry-packages/orphan/copy.step').write_bytes(STEP)
    (store.root/'assets/empty').mkdir()
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        yield client,store,release,resource,asset
        for job in client.app.state.backups.jobs():
            if job['id'] in FUTURES:FUTURES[job['id']].result(timeout=30)


def finish(client,response):
    assert response.status_code==202,response.text
    value=response.json();FUTURES[value['id']].result(timeout=30)
    return client.get('/api/v1/backup-jobs/'+value['id'],headers=HEADERS).json()


def create_backup(client):
    job=finish(client,client.post('/api/v1/backups',headers=HEADERS,json={}))
    assert job['status']=='completed',job
    return client.get('/api/v1/backup-jobs/'+job['id']+'/report',headers=HEADERS).json()


def preview(client,backup_id,name='R'):
    job=finish(client,client.post('/api/v1/backups/'+backup_id+'/restore-preview',headers=HEADERS,json={'name':name}))
    assert job['status']=='completed',job
    report=client.get('/api/v1/backup-jobs/'+job['id']+'/report',headers=HEADERS).json()
    return job,report


def test_full_backup_dedup_and_isolated_restore_preserve_metadata_and_original_audit(backup_pilot):
    client,store,release,resource,asset=backup_pilot
    files=store.manifest(store.release_folder(release['id']));original=store.resources();history=store.history(resource['id'])
    prefix=store.audit_entries();receipt=create_backup(client);manager=client.app.state.backups
    archive=manager.download(receipt['id'])
    with zipfile.ZipFile(archive) as package:
        manifest=json.loads(package.read('manifest.json'))
        assert manifest['scope']['partial_outputs'] and not manifest['scope']['secrets']
        assert hashlib.sha256(package.read('management.sqlite3')).hexdigest()==manifest['database']['sha256']
        assert receipt['distinct_files']<receipt['files']  # Identical files share one archive object.
    job,report=preview(client,receipt['id']);assert report['can_restore'] and not Path(report['target_folder']).exists()
    result=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,
        json={'name':'R','preview_id':job['id'],'preview_hash':report['preview_hash'],'accept_unsigned':True}))
    assert result['status']=='completed',result['message']
    restored_report=manager.report(result['id']);restored=Store(Path(restored_report['target_folder']))
    assert restored.resources()==original and restored.history(resource['id'])==history
    assert restored.manifest(restored.release_folder(release['id']))==files
    assert restored.audit_entries()[-len(prefix):]==prefix  # audit_entries is descending; old prefix remains unchanged.
    assert restored.active()['release_id'] is None
    assert restored.asset_content(asset['id'])[1]==STEP
    assert (restored.root/'assets/empty').is_dir()
    assert (restored.root/'geometry-packages/orphan/partial.txt').read_text()=='Partial forensic artifact'
    assert scan(restored)['error_count']==0
    assert store.resources()==original and store.manifest(store.release_folder(release['id']))==files
    assert not (restored.root/'RECOVERY_PENDING.json').exists()
    assert restored.get_release(release['id'])['test_launcher']['folder'].startswith(str(restored.root))


def test_approved_active_snapshot_is_restored_as_inactive_draft_with_original_evidence(backup_pilot):
    client,store,release,_,_=backup_pilot
    with store.db() as db:
        # Synthetic metadata contract, explicitly not a real CAM acceptance.
        db.execute('UPDATE releases SET status=?,validation=?,review=? WHERE id=?',('approved',canonical({'integrity_passed':True}),
            canonical({'tests':{key:True for key in FUNCTIONAL_TESTS},'evidence':'Synthetic fixture, not actual NX proof'}),release['id']))
        store.audit(db,'fixture','release.approved',{'id':release['id']})
        db.execute('UPDATE active SET release_id=?,generation=5 WHERE singleton=1',(release['id'],))
        store.audit(db,'fixture','release.activated',{'release_id':release['id']})
    receipt=create_backup(client);job,report=preview(client,receipt['id'])
    result=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,
        json={'name':'R','preview_id':job['id'],'preview_hash':report['preview_hash'],'accept_unsigned':True}))
    assert result['status']=='completed',result
    new=Store(report['target_folder']);value=new.get_release(release['id'])
    assert value['status']=='draft' and value['review']==store.get_release(release['id'])['review']
    assert new.active()['release_id'] is None and new.active()['generation']==6
    assert store.active()['release_id']==release['id'] and store.active()['generation']==5


@pytest.mark.parametrize('mutation',['release','asset','syslog','geometry','launcher','database'])
def test_corrupt_source_fails_backup_without_publishing_or_repairing(backup_pilot,mutation):
    client,store,release,resource,asset=backup_pilot;manager=client.app.state.backups
    if mutation=='database':
        with store.db() as db:db.execute("UPDATE resources SET payload=payload||' ' WHERE id=?",(resource['id'],))
    else:
        paths={'release':store.release_folder(release['id'])/SPECS['tool-metric']['path'],
               'asset':store.root/'assets'/asset['id']/asset['filename'],
               'syslog':next((store.root/'evidence').rglob('nx.syslog')),
               'geometry':next((store.root/'geometry-packages').rglob('NX_STEP.zip')),
               'launcher':manager.store.test_launcher_package(release['id'])}
        paths[mutation].write_bytes(paths[mutation].read_bytes()+b'Corruption')
    result=finish(client,client.post('/api/v1/backups',headers=HEADERS,json={}))
    assert result['status']=='failed' and manager.list()==[]
    assert store.get_release(release['id'])['status']=='draft'


def test_backup_download_receipt_and_strict_roles(backup_pilot):
    client,store,_,_,_=backup_pilot
    for headers,status in (({},401),(ERP_HEADERS,403)):
        assert client.get('/api/v1/backups',headers=headers).status_code==status
        assert client.post('/api/v1/backups',headers=headers,json={}).status_code==status
    assert client.post('/api/v1/backups',headers=HEADERS,json={'accept_legacy_unbound':'yes'}).status_code==422
    receipt=create_backup(client)
    response=client.get('/api/v1/backups/'+receipt['id']+'/download',headers=HEADERS)
    assert response.status_code==200 and hashlib.sha256(response.content).hexdigest()==receipt['archive_sha256']
    assert client.get('/api/v1/backups/'+receipt['id']+'/receipt',headers=HEADERS).json()==receipt
    assert client.get('/api/v1/backups/'+str(uuid.uuid4())+'/receipt',headers=HEADERS).status_code==404
    assert client.get('/backups.js').status_code==200


def test_no_overwrite_no_unsigned_restore_no_stale_target_decisions(backup_pilot):
    client,store,_,_,_=backup_pilot;receipt=create_backup(client);job,report=preview(client,receipt['id'])
    body={'name':'R','preview_id':job['id'],'preview_hash':report['preview_hash']}
    assert client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,json=body).status_code==400
    assert client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,
        json={**body,'name':'Changed','accept_unsigned':True}).status_code==409
    target=Path(report['target_folder']);target.mkdir(parents=True);(target/'user.txt').write_bytes(b'Keep me')
    result=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,json={**body,'accept_unsigned':True}))
    assert result['status']=='failed' and (target/'user.txt').read_bytes()==b'Keep me'
    assert not (target/'management.sqlite3').exists()


@pytest.mark.parametrize('name',['..','../other','W:\\SiemensShare\\nx2406','/mnt/w/test','sub/folder','CON','name.'])
def test_protected_or_nonportable_restore_names_fail_before_target_lookup(backup_pilot,name):
    client,store,_,_,_=backup_pilot;receipt=create_backup(client)
    result=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',headers=HEADERS,json={'name':name}))
    assert result['status']=='failed' and not client.app.state.backups.restore_roots['local'].exists()


def test_changed_archive_cannot_be_restored_from_old_preview(backup_pilot):
    client,store,_,_,_=backup_pilot;receipt=create_backup(client);job,report=preview(client,receipt['id'])
    archive=client.app.state.backups.download(receipt['id']);archive.write_bytes(archive.read_bytes()+b'Changed')
    result=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,
        json={'name':'R','preview_id':job['id'],'preview_hash':report['preview_hash'],'accept_unsigned':True}))
    assert result['status']=='failed' and not Path(report['target_folder']).exists()


def test_running_backup_blocks_double_click_and_all_writes_but_not_reading(backup_pilot,monkeypatch):
    client,store,_,_,_=backup_pilot;manager=client.app.state.backups
    started=threading.Event();release=threading.Event();original=manager._backup
    def held(*args):started.set();assert release.wait(15);return original(*args)
    monkeypatch.setattr(manager,'_backup',held)
    response=client.post('/api/v1/backups',headers=HEADERS,json={});assert started.wait(5)
    try:
        assert client.post('/api/v1/backups',headers=HEADERS,json={}).status_code==409
        assert client.post('/api/v1/resources',headers=HEADERS,json={'kind':'gauge','name':'No writer','cam':{}}).status_code==409
        assert client.post('/api/v1/erp/resources/upsert',headers=ERP_HEADERS,json={}).status_code==409
        assert client.get('/api/v1/resources',headers=HEADERS).status_code==200
        assert not FUTURES[response.json()['id']].done()
    finally:release.set()
    assert finish(client,response)['status']=='completed'


def test_incomplete_recovery_marker_prevents_startup(tmp_path):
    root=tmp_path/'R';root.mkdir();write_json(root/'RECOVERY_PENDING.json',{'status':'partial'})
    with pytest.raises(DomainError,match='incomplete'):Store(root)


def test_new_manager_lists_persisted_backup_and_verified_jobs_without_restarting(backup_pilot):
    client,store,_,_,_=backup_pilot;receipt=create_backup(client)
    verify=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/verify',headers=HEADERS,json={}))
    assert verify['status']=='completed'
    new=Backups(store)
    assert new.list()==[receipt] and {job['id'] for job in new.jobs()}=={job['id'] for job in client.app.state.backups.jobs()}
    assert not new.busy


def repack(manager,receipt,change_manifest=None,extra=None):
    """Test a rewritten self-receipt; this is not a cryptographic trust claim."""
    archive=manager.download(receipt['id'])
    with zipfile.ZipFile(archive) as package:data={entry.filename:package.read(entry) for entry in package.infolist()}
    if change_manifest:
        manifest=json.loads(data['manifest.json']);change_manifest(manifest)
        data['manifest.json']=canonical(manifest).encode()
    if extra:data.update(extra)
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as package:
        for name,content in data.items():package.writestr(name,content)
    receipt.update(archive_sha256=sha256(archive),archive_size=archive.stat().st_size,
                   manifest_sha256=hashlib.sha256(data['manifest.json']).hexdigest())
    write_json(archive.parent/'receipt.json',receipt)


@pytest.mark.parametrize('path',['../escape.txt','/absolute.txt','objects/not-a-sha','OBJECTS/fake','secret.txt'])
def test_even_self_recomputed_receipts_cannot_extract_unknown_archive_paths(backup_pilot,path):
    client,store,_,_,_=backup_pilot;receipt=create_backup(client);manager=client.app.state.backups
    repack(manager,receipt,extra={path:b'Never extracted'})
    job=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/verify',headers=HEADERS,json={}))
    assert job['status']=='failed' and not manager.restore_roots['local'].exists()


@pytest.mark.parametrize('mutation',['traversal','case_collision','object_sha','unknown_group','missing_release','size','directory_collision'])
def test_manifest_mutations_are_rejected_before_recovery_target_creation(backup_pilot,mutation):
    client,store,_,_,_=backup_pilot;receipt=create_backup(client);manager=client.app.state.backups
    def corrupt(manifest):
        group=manifest['groups'][0];item=group['files'][0]
        if mutation=='traversal':item['path']='../outside'
        elif mutation=='case_collision':group['files'].append({**item,'path':item['path'].swapcase()})
        elif mutation=='object_sha':item['sha256']='f'*64
        elif mutation=='unknown_group':group['kind']='arbitrary-path'
        elif mutation=='missing_release':group['id']=str(uuid.uuid4())
        elif mutation=='size':item['size']+=1
        else:group['directories'].append(item['path'])
    repack(manager,receipt,change_manifest=corrupt)
    job=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/restore-preview',headers=HEADERS,json={'name':'Never'}))
    assert job['status']=='failed' and not manager.restore_roots['local'].exists()


def test_legacy_unbound_snapshot_requires_explicit_acceptance(backup_pilot):
    client,store,_,_,_=backup_pilot
    with store.db() as db:
        previous='0'*64
        for row in db.execute('SELECT * FROM audit ORDER BY sequence').fetchall():
            payload=json.loads(row['payload']);payload.pop('_state',None);raw=canonical(payload)
            value=hashlib.sha256(canonical([previous,row['actor'],row['action'],raw,row['created_at']]).encode()).hexdigest()
            db.execute('UPDATE audit SET payload=?,previous_hash=?,hash=? WHERE sequence=?',(raw,previous,value,row['sequence']));previous=value
    first=finish(client,client.post('/api/v1/backups',headers=HEADERS,json={}))
    assert first['status']=='failed' and 'Legacy rows' in first['message']
    accepted=finish(client,client.post('/api/v1/backups',headers=HEADERS,json={'accept_legacy_unbound':True}))
    assert accepted['status']=='completed'
    assert client.app.state.backups.report(accepted['id'])['warning_count']>0


def test_future_sqlite_columns_are_preserved_through_recovery(backup_pilot):
    from test_libraries import resource_body
    client,store,_,resource,_=backup_pilot
    with store.db() as db:
        db.execute('ALTER TABLE resources ADD COLUMN future_field TEXT')
        db.execute('UPDATE resources SET future_field=? WHERE id=?',('Preserved future value',resource['id']))
    payload=resource_body(resource);payload['name']='New revision binds future columns'
    store.save_resource(payload,'fixture',resource['id'],resource['revision'])
    receipt=create_backup(client);job,report=preview(client,receipt['id'])
    restored=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,
        json={'name':'R','preview_id':job['id'],'preview_hash':report['preview_hash'],'accept_unsigned':True}))
    assert restored['status']=='completed',restored['message']
    with sqlite3.connect(Path(report['target_folder'])/'management.sqlite3') as db:
        assert db.execute('SELECT future_field FROM resources WHERE id=?',(resource['id'],)).fetchone()[0]=='Preserved future value'


def test_failed_external_build_and_registered_external_release_are_captured_and_not_replayed(backup_pilot,tmp_path):
    client,store,_,_,_=backup_pilot;manager=client.app.state.backups
    store.folders.targets['external']=tmp_path/'external'
    release=store.import_release('test','2512','External registered','fixture',target='external',target_path=str(tmp_path/'external/live'))
    key=str(uuid.uuid4());partial=tmp_path/'external/partial';partial.mkdir()
    (partial/'partial.prt').write_bytes(b'Synthetic partial NX file, never executed')
    value={'id':key,'mode':'build','status':'failed','owner_pid':123456789,'request':{'target_path':str(partial)}}
    with store.db() as db:
        db.execute('INSERT INTO migration_jobs(id,payload) VALUES(?,?)',(key,canonical(value)))
        store.audit(db,'fixture','migration.job.failed',{'id':key})
    receipt=create_backup(client);job,report=preview(client,receipt['id'])
    result=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,
        json={'name':'R','preview_id':job['id'],'preview_hash':report['preview_hash'],'accept_unsigned':True}))
    assert result['status']=='completed',result['message']
    restored=Store(report['target_folder'])
    assert restored.manifest(restored.release_folder(release['id']))==store.manifest(store.release_folder(release['id']))
    assert (restored.root/'retained/migrations'/key/'output/partial.prt').read_bytes()==(partial/'partial.prt').read_bytes()
    assert key in restored.recovered_job_ids
    # Recovery history reserves no original-host filesystem path, and a second
    # backup reads the new private forensic copy, not the original partial dir.
    assert partial not in restored.folders._releases()
    new=Backups(restored);backup=new.create('backup','fixture')
    FUTURES[backup['id']].result(timeout=30)
    assert new.get_job(backup['id'])['status']=='completed',new.get_job(backup['id'])['message']


@pytest.mark.parametrize('owner',['gone','reused','unknown','live'])
def test_process_recovery_uses_authoritative_liveness_and_never_restarts(backup_pilot,monkeypatch,owner):
    client,store,_,_,_=backup_pilot;manager=client.app.state.backups;key=str(uuid.uuid4())
    folder=manager._job_folder(key);folder.mkdir()
    write_json(folder/'job.json',{'id':key,'mode':'backup','status':'running','phase':'archive',
        'message':'Partial','owner_pid':123456789,'owner_stamp':'original','created_at':'synthetic'})
    monkeypatch.setattr('nxserver.backups.process_stamp',lambda pid:None if owner=='gone' else 'different' if owner=='reused' else 'unknown' if owner=='unknown' else 'original')
    new=Backups(store);job=new.get_job(key)
    assert job['status']==('interrupted' if owner in ('gone','reused') else 'running')
    assert key not in FUTURES and new.busy==(owner in ('unknown','live'))


def test_real_future_remains_busy_despite_a_wrong_persisted_completed_flag(backup_pilot,monkeypatch):
    client,store,_,_,_=backup_pilot;manager=client.app.state.backups
    started=threading.Event();release=threading.Event();original=manager._backup
    def held(*args):started.set();assert release.wait(15);return original(*args)
    monkeypatch.setattr(manager,'_backup',held)
    response=client.post('/api/v1/backups',headers=HEADERS,json={});assert started.wait(5)
    key=response.json()['id']
    try:
        manager._update(key,status='completed')
        assert manager.busy and not FUTURES[key].done()
        assert manager.get_job(key)['status']=='running'
        assert client.post('/api/v1/backups',headers=HEADERS,json={}).status_code==409
    finally:release.set()
    assert finish(client,response)['status']=='completed'


def test_source_database_external_mutation_prevents_publishing_a_stale_snapshot(backup_pilot,monkeypatch):
    client,store,_,resource,_=backup_pilot;manager=client.app.state.backups;original=manager._inventory;changed=False
    def mutate(*args):
        nonlocal changed
        result=original(*args)
        if not changed:
            changed=True
            with sqlite3.connect(store.root/'management.sqlite3') as db:
                db.execute('UPDATE resources SET payload=payload||? WHERE id=?',(' ',resource['id']))
        return result
    monkeypatch.setattr(manager,'_inventory',mutate)
    job=finish(client,client.post('/api/v1/backups',headers=HEADERS,json={}))
    assert job['status']=='failed' and 'Database changed' in job['message']
    assert manager.list()==[]


def test_interrupted_copy_keeps_new_target_startup_blocked(backup_pilot,monkeypatch):
    client,store,_,_,_=backup_pilot;receipt=create_backup(client);job,report=preview(client,receipt['id'])
    original=sha256
    def failed(path):
        if str(path).startswith(report['target_folder']) and str(path).endswith('.dat'):raise OSError('Synthetic copy failure')
        return original(path)
    monkeypatch.setattr('nxserver.backups.sha256',failed)
    result=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,
        json={'name':'R','preview_id':job['id'],'preview_hash':report['preview_hash'],'accept_unsigned':True}))
    assert result['status']=='failed' and (Path(report['target_folder'])/'RECOVERY_PENDING.json').exists()
    with pytest.raises(DomainError,match='incomplete'):Store(report['target_folder'])


def test_selected_extraction_and_immutable_reports_survive_relocation_and_second_backup(tmp_path,monkeypatch):
    from test_extraction import fake_setup
    from nxserver.upgrades import Upgrades,LIVE
    store,_,calls=fake_setup(tmp_path,monkeypatch);upgrades=Upgrades(store)
    extract=upgrades.create_extraction('2512','fixture');LIVE[extract['id']].result(timeout=10)
    upgrades.select_extraction(extract['id'],0,'fixture')
    original_report=upgrades.report(extract['id']);original_path=store.sidt_root('2512')
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        receipt=create_backup(client);job,report=preview(client,receipt['id'])
        result=finish(client,client.post('/api/v1/backups/'+receipt['id']+'/restore',headers=HEADERS,
            json={'name':'R','preview_id':job['id'],'preview_hash':report['preview_hash'],'accept_unsigned':True}))
        assert result['status']=='completed',result['message']
    restored=Store(report['target_folder']);new=Upgrades(restored)
    assert new.report(extract['id'])==original_report
    assert restored.sidt_root('2512')!=original_path and restored.root in restored.sidt_root('2512').parents
    assert scan(restored)['error_count']==0
    assert new.select_extraction(extract['id'],1,'fixture')['generation']==2
    manager=Backups(restored);backup=manager.create('backup','fixture');FUTURES[backup['id']].result(timeout=30)
    assert manager.get_job(backup['id'])['status']=='completed',manager.get_job(backup['id'])['message']
    check=manager.create('verify','fixture',backup_id=manager.report(backup['id'])['id']);FUTURES[check['id']].result(timeout=30)
    assert manager.get_job(check['id'])['status']=='completed',manager.get_job(check['id'])['message']
    assert calls==['-v','-t','-x']  # Mock reader only once; no extraction rerun by recovery.


def test_copied_archive_and_receipt_are_sufficient_on_a_fresh_management_server(backup_pilot,tmp_path):
    import shutil
    client,store,release,_,_=backup_pilot;receipt=create_backup(client);old=client.app.state.backups
    fresh=Store(tmp_path/'new-server');manager=Backups(fresh);folder=manager._folder(receipt['id']);folder.mkdir(parents=True)
    shutil.copyfile(old.download(receipt['id']),folder/'NX_Backup.zip');write_json(folder/'receipt.json',receipt)
    proposal=manager.create('preview','fixture',{'target':'local','name':'Fresh'},receipt['id']);FUTURES[proposal['id']].result(timeout=30)
    report=manager.report(proposal['id']);assert report['can_restore']
    job=manager.create('restore','fixture',{'target':'local','name':'Fresh','preview_id':proposal['id'],
        'preview_hash':report['preview_hash'],'accept_unsigned':True},receipt['id']);FUTURES[job['id']].result(timeout=30)
    assert manager.get_job(job['id'])['status']=='completed',manager.get_job(job['id'])['message']
    restored=Store(report['target_folder']);assert restored.manifest(restored.release_folder(release['id']))==store.manifest(store.release_folder(release['id']))
    assert fresh.resources()==[] and fresh.releases()==[]  # Restore does not replace the running server.

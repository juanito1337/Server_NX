"""Late NX evidence drift at final SQL gates, using synthetic offline data only."""
import base64
import uuid

import pytest

from nxserver import release_requests
from nxserver.integrity import scan
from test_management import HEADERS, pilot, pinned_review, imported
from test_reviews import setup_review


@pytest.mark.parametrize('action',['review','activate'])
@pytest.mark.parametrize('mutation',['file_bytes','extra_file','missing_file','configured_log','uploaded_log'])
@pytest.mark.parametrize('receipted',[False,True])
def test_late_evidence_drift_cannot_commit_approval_or_pointer(pilot,monkeypatch,action,mutation,receipted):
    client,store,source,configured=pilot
    release,body=setup_review(client,store,configured)
    if mutation=='uploaded_log':
        response=client.post('/api/v1/logs',headers=HEADERS,json={
            'filename':'synthetic.syslog','content_base64':base64.b64encode(configured.read_bytes()).decode()})
        assert response.status_code==201,response.text
        identifier=response.json()['id']
        body['syslog_source']=identifier
        body=pinned_review(client,release,body)
        log=store.root/'evidence'/identifier/'nx.syslog'
    else:log=configured
    if action=='activate':
        response=client.post('/api/v1/releases/'+release['id']+'/review',headers=HEADERS,json=body)
        assert response.status_code==200,response.text
    before=store.get_release(release['id'])
    source_before=store.manifest(source)
    root=store.release_folder(release['id'])
    file=root/release['manifest'][0]['path']
    gate=release_requests.gate
    injected=[]

    def change_at_final_gate(db,actor,key,operation,target,payload):
        gate(db,actor,key,operation,target,payload)
        if operation!=action or injected:return
        assert db.in_transaction
        injected.append(operation)
        if mutation=='file_bytes':file.write_bytes(file.read_bytes()+b'\nSynthetic late modification\n')
        elif mutation=='extra_file':(root/'synthetic-extra.txt').write_bytes(b'Synthetic late file')
        elif mutation=='missing_file':file.unlink()  # Test-owned fixture file only.
        else:log.write_bytes(log.read_bytes()+b'\nNX_VERSION = 2406\n')

    monkeypatch.setattr(release_requests,'gate',change_at_final_gate)
    key=str(uuid.uuid4()) if receipted else None
    headers={**HEADERS,**({'Idempotency-Key':key} if key else {})}
    response=(client.post('/api/v1/releases/'+release['id']+'/review',headers=headers,json=body)
              if action=='review' else client.post('/api/v1/active',headers=headers,
                                                  json={'release_id':release['id'],'expected_generation':0}))
    assert injected==[action]
    assert response.status_code==409,response.text
    current=store.get_release(release['id'])
    assert current['review']==before['review']
    assert current['status']==('validated' if action=='review' else 'approved')
    assert store.active()['release_id'] is None and store.active()['generation']==0
    assert store.manifest(source)==source_before
    event='release.approved' if action=='review' else 'release.activated'
    assert sum(entry['action']==event for entry in store.audit_entries())==0
    if receipted:
        receipt=release_requests.get(store,'admin',key)
        assert receipt['state']=='failed' and receipt['result'] is None and receipt['writer_finished_at']
        assert scan(store,False)['error_count']==0


@pytest.mark.parametrize('action',['review','activate'])
@pytest.mark.parametrize('reader',['log','manifest'])
def test_final_read_failure_is_conflict_not_server_error_or_success(pilot,monkeypatch,action,reader):
    client,store,_,log=pilot
    release,body=setup_review(client,store,log)
    if action=='activate':
        assert client.post('/api/v1/releases/'+release['id']+'/review',headers=HEADERS,json=body).status_code==200
    gate=release_requests.gate
    entered=[]
    def final_gate(*args):
        gate(*args)
        if args[3]==action:entered.append(True)
    monkeypatch.setattr(release_requests,'gate',final_gate)
    original=store.scan_log if reader=='log' else store.manifest
    def unreadable(*args,**kwargs):
        if entered:raise PermissionError('Synthetic final reader unavailable')
        return original(*args,**kwargs)
    monkeypatch.setattr(store,'scan_log' if reader=='log' else 'manifest',unreadable)
    key=str(uuid.uuid4());headers={**HEADERS,'Idempotency-Key':key}
    response=(client.post('/api/v1/releases/'+release['id']+'/review',headers=headers,json=body)
              if action=='review' else client.post('/api/v1/active',headers=headers,
                                                  json={'release_id':release['id'],'expected_generation':0}))
    assert response.status_code==409,response.text
    assert 'no approval or active-pointer switch committed' in response.json()['detail']
    assert release_requests.get(store,'admin',key)['state']=='failed'
    assert store.active()['release_id'] is None and store.active()['generation']==0


@pytest.mark.parametrize('receipted',[False,True])
def test_late_target_drift_preserves_existing_other_active_release(pilot,monkeypatch,receipted):
    client,store,_,log=pilot
    first,body=setup_review(client,store,log)
    assert client.post('/api/v1/releases/'+first['id']+'/review',headers=HEADERS,json=body).status_code==200
    assert client.post('/api/v1/active',headers=HEADERS,json={'release_id':first['id'],'expected_generation':0}).status_code==200
    prior_active=store.active();prior_release=store.get_release(first['id'])
    target=imported(client)
    log.write_text('NX_VERSION = 2512\nUG_APP_MANUFACTURING\n'+target['folder'],encoding='utf-8')
    body=pinned_review(client,target,body)
    assert client.post('/api/v1/releases/'+target['id']+'/review',headers=HEADERS,json=body).status_code==200
    gate=release_requests.gate;changed=[]
    def late(db,actor,key,action,release_id,payload):
        gate(db,actor,key,action,release_id,payload)
        if action=='activate' and not changed:
            changed.append(True)
            (store.release_folder(target['id'])/'synthetic-late.txt').write_bytes(b'Synthetic drift')
    monkeypatch.setattr(release_requests,'gate',late)
    headers={**HEADERS,**({'Idempotency-Key':str(uuid.uuid4())} if receipted else {})}
    result=client.post('/api/v1/active',headers=headers,json={'release_id':target['id'],'expected_generation':1})
    assert result.status_code==409,result.text
    assert store.active()==prior_active and store.get_release(first['id'])==prior_release


@pytest.mark.parametrize('action',['review','activate'])
def test_historical_success_replay_never_rechecks_or_switches_files(pilot,monkeypatch,action):
    client,store,_,log=pilot
    release,body=setup_review(client,store,log)
    if action=='activate':
        assert client.post('/api/v1/releases/'+release['id']+'/review',headers=HEADERS,json=body).status_code==200
    key=str(uuid.uuid4());headers={**HEADERS,'Idempotency-Key':key}
    def submit():
        return (client.post('/api/v1/releases/'+release['id']+'/review',headers=headers,json=body)
                if action=='review' else client.post('/api/v1/active',headers=headers,
                                                    json={'release_id':release['id'],'expected_generation':0}))
    first=submit();assert first.status_code==200,first.text
    before=store.audit_entries();active=store.active()
    log.write_bytes(log.read_bytes()+b'\nSynthetic later log modification')
    monkeypatch.setattr(store,'_verify_review_commit_evidence',lambda *args:pytest.fail('Historical receipt is not a new transition.'))
    assert submit().json()==first.json() and store.audit_entries()==before and store.active()==active


@pytest.mark.parametrize('action',['review','activate'])
def test_final_log_gate_reads_same_transaction_metadata_not_an_older_connection(pilot,monkeypatch,action):
    client,store,_,configured=pilot
    release,body=setup_review(client,store,configured)
    uploaded=client.post('/api/v1/logs',headers=HEADERS,json={'filename':'synthetic.syslog',
        'content_base64':base64.b64encode(configured.read_bytes()).decode()}).json()
    body['syslog_source']=uploaded['id'];body=pinned_review(client,release,body)
    if action=='activate':
        assert client.post('/api/v1/releases/'+release['id']+'/review',headers=HEADERS,json=body).status_code==200
    before=store.log_metadata(uploaded['id']);gate=release_requests.gate;changed=[]
    def change(db,actor,key,operation,target,payload):
        gate(db,actor,key,operation,target,payload)
        if operation==action and not changed:
            changed.append(True)
            db.execute('UPDATE syslogs SET size=size+1 WHERE id=?',(uploaded['id'],))
    monkeypatch.setattr(release_requests,'gate',change)
    key=str(uuid.uuid4());headers={**HEADERS,'Idempotency-Key':key}
    response=(client.post('/api/v1/releases/'+release['id']+'/review',headers=headers,json=body)
              if action=='review' else client.post('/api/v1/active',headers=headers,
                                                  json={'release_id':release['id'],'expected_generation':0}))
    assert response.status_code==409,response.text
    assert store.log_metadata(uploaded['id'])==before  # Synthetic in-transaction drift was rolled back.
    assert release_requests.get(store,'admin',key)['state']=='failed' and scan(store,False)['error_count']==0
    assert store.active()['release_id'] is None and store.active()['generation']==0

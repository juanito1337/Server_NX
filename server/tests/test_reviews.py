"""Synthetic approval contracts, not actual NX/CAM qualification."""
import copy
import hashlib

import pytest

from nxserver.safety import DomainError
from nxserver.store import FUNCTIONAL_TESTS, canonical
from nxserver.syslogs import scan_bytes
from test_management import HEADERS, ERP_HEADERS, imported, pilot, pinned_review


@pytest.mark.parametrize('header,status',[
    ('NX_VERSION = 2512.8500','match'),(' nx_version: 2512 ','match'),
    ('NX_VERSION = 2512.8500\nNX_VERSION = 2512.9000','match'),
    ('NX_VERSION = 2406.1000','mismatch'),
    ('NX_VERSION = 2406.1000\nNX_VERSION = 2512.8500','ambiguous'),
    ('NX_VERSION = not-a-version','invalid'),('NX_VERSION = 2512.8500 extra','invalid'),
    ('NX_VERSION = 2512\nNX_VERSION =','invalid'),
    ('Opened C:/Siemens/NX2512/file.prt. NX 2512 test; source NX 2406.','missing'),
    ('UGII_VERSION = 2512','missing'),('','missing')])
def test_only_unambiguous_explicit_vendor_headers_qualify(header,status):
    raw=(header+'\nUG_APP_MANUFACTURING\nC:/offline/candidate/file.prt').encode()
    report=scan_bytes(raw,'synthetic','C:/offline/candidate','2512')
    assert report['version_check']['status']==status
    assert report['can_review']==(status=='match')
    assert report['sha256']==hashlib.sha256(raw).hexdigest()
    assert scan_bytes(raw,'synthetic')['can_review'] is False


def test_real_vendor_separator_is_preserved_and_findings_are_bounded():
    raw=b'NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\nC:/offline/candidate/file.prt\n'
    raw+=b'field\x1dFailed to read in part\n'*250
    report=scan_bytes(raw,'synthetic','C:/offline/candidate','2512')
    assert report['version_check']['status']=='match'
    assert report['blocking_error_count']==250 and len(report['blocking_errors'])==200
    assert report['blocking_errors_truncated'] and report['warnings']
    assert report['blocking_errors'][0]['line']==4 and '\x1d' not in report['blocking_errors'][0]['message']
    assert report['sha256']==hashlib.sha256(raw).hexdigest() and not report['can_review']
    with pytest.raises(DomainError):scan_bytes(b'\x1bANSI control','synthetic')


def test_many_version_headers_do_not_grow_diagnostics_or_drop_ambiguity():
    report=scan_bytes(('NX_VERSION = 2512\n'*250+'NX_VERSION = 2406\n').encode(),'synthetic','C:/test','2512')
    assert report['version_check']['status']=='ambiguous' and report['version_check']['header_count']==251
    assert len(report['version_check']['evidence'])==200 and report['version_check']['evidence_truncated']


@pytest.mark.parametrize('text',['XC:/offline/candidate','C:/prefixC:/offline/candidate','C:/offline/candidate_other'])
def test_candidate_path_requires_both_boundaries(text):
    assert not scan_bytes(('NX_VERSION = 2512\nUG_APP_MANUFACTURING\n'+text).encode(),
        'synthetic','C:/offline/candidate','2512')['can_review']


def setup_review(client,store,log):
    release=imported(client)
    log.write_text('NX_VERSION = 2512.8500\nUG_APP_MANUFACTURING\n'+release['folder'],encoding='utf-8')
    body={'tests':{key:True for key in FUNCTIONAL_TESTS},'evidence':'Synthetic qualified review contract; not real NX evidence.','syslog_source':'test-log'}
    return release,pinned_review(client,release,body)


@pytest.mark.parametrize('mutation',['no_hashes','log_hash','manifest_hash','changed_log','missing_version','wrong_version','ambiguous_version','blank_statement','partial_checks'])
def test_approval_preflight_rejects_stale_or_unqualified_evidence_without_writes(pilot,mutation):
    client,store,_,log=pilot;release,body=setup_review(client,store,log)
    if mutation=='no_hashes':body.pop('expected_log_sha256');body.pop('expected_manifest_sha256')
    elif mutation=='log_hash':body['expected_log_sha256']='0'*64
    elif mutation=='manifest_hash':body['expected_manifest_sha256']='0'*64
    elif mutation=='changed_log':log.write_text(log.read_text()+'\nExternal change',encoding='utf-8')
    elif mutation in ('missing_version','wrong_version','ambiguous_version'):
        header={'missing_version':'NX 2512 is just a textual mention.','wrong_version':'NX_VERSION = 2406',
                'ambiguous_version':'NX_VERSION = 2512\nNX_VERSION = 2406'}[mutation]
        log.write_text(header+'\nUG_APP_MANUFACTURING\n'+release['folder'],encoding='utf-8')
        body=pinned_review(client,release,body)
    elif mutation=='blank_statement':body['evidence']=' '*40
    else:body['tests']['posts']=False
    before=store.audit_entries();response=client.post('/api/v1/releases/'+release['id']+'/review',headers=HEADERS,json=body)
    assert response.status_code==409,response.text
    assert store.get_release(release['id'])==release and store.audit_entries()==before
    assert store.active()['release_id'] is None


def test_fresh_scan_is_read_only_and_review_remains_bound_across_revalidation(pilot):
    client,store,_,log=pilot;release,body=setup_review(client,store,log)
    before=store.audit_entries();body=pinned_review(client,release,body)
    assert store.audit_entries()==before
    url='/api/v1/releases/'+release['id']
    approved=client.post(url+'/review',headers=HEADERS,json=body).json()
    assert approved['status']=='approved' and approved['review']['contract_version']==2
    assert approved['review']['syslog']['version_check']['versions']==['2512.8500']
    assert client.post(url+'/validate',headers=HEADERS).json()['approval_retained']
    assert store.get_release(release['id'])['review']==approved['review']
    assert client.post('/api/v1/active',headers=HEADERS,json={'release_id':release['id'],'expected_generation':0}).status_code==200
    assert store.active()['usable']
    unchanged=store.audit_entries()
    assert client.post('/api/v1/active',headers=HEADERS,json={'release_id':release['id'],'expected_generation':0}).status_code==409
    assert store.audit_entries()==unchanged  # A stale switch has no validation side effect.
    log.write_text(log.read_text()+'\nExternal change',encoding='utf-8')
    result=client.post(url+'/validate',headers=HEADERS).json()
    assert result['integrity_passed'] and result['approval_requires_requalification'] and not result['approval_retained']
    assert store.get_release(release['id'])['status']=='validated'
    assert store.get_release(release['id'])['review']==approved['review']  # Historical evidence retained.
    assert not store.active()['usable'] and store.active()['folder'] is None


def test_legacy_approval_and_pointer_do_not_silently_qualify_after_policy_change(pilot):
    client,store,_,log=pilot;release,body=setup_review(client,store,log)
    approved=client.post('/api/v1/releases/'+release['id']+'/review',headers=HEADERS,json=body).json()
    legacy=copy.deepcopy(approved['review']);legacy.pop('contract_version');legacy.pop('manifest_sha256')
    with store.db() as db:
        db.execute('UPDATE releases SET review=? WHERE id=?',(canonical(legacy),release['id']))
        db.execute('UPDATE active SET release_id=?,generation=3 WHERE singleton=1',(release['id'],))
        store.audit(db,'fixture','release.approved',{'id':release['id'],'review':legacy})
        store.audit(db,'fixture','release.activated',{'id':release['id']})
    before=store.audit_entries()
    assert not store.active()['usable'] and store.active()['generation']==3
    assert client.post('/api/v1/active',headers=HEADERS,json={'release_id':release['id'],'expected_generation':3}).status_code==409
    assert store.audit_entries()==before
    assert client.post('/api/v1/releases/'+release['id']+'/validate',headers=HEADERS).json()['approval_requires_requalification']
    assert store.get_release(release['id'])['review']==legacy


def test_configured_log_changed_during_validation_cannot_be_approved(pilot,monkeypatch):
    client,store,_,log=pilot;release,body=setup_review(client,store,log);original=store.validate_release
    def change(key,actor):
        result=original(key,actor);log.write_text(log.read_text()+'\nExternal in-flight update',encoding='utf-8');return result
    monkeypatch.setattr(store,'validate_release',change)
    response=client.post('/api/v1/releases/'+release['id']+'/review',headers=HEADERS,json=body)
    assert response.status_code==409,response.text
    assert store.get_release(release['id'])['status']=='validated' and store.get_release(release['id'])['review'] is None
    assert not any(row['action']=='release.approved' for row in store.audit_entries())


def test_erp_cannot_scan_or_approve_cam_evidence(pilot):
    client,store,_,log=pilot;release,body=setup_review(client,store,log)
    assert client.post('/api/v1/logs/test-log/scan',headers=ERP_HEADERS,json={'release_id':release['id']}).status_code==403
    assert client.post('/api/v1/releases/'+release['id']+'/review',headers=ERP_HEADERS,json=body).status_code==403
    assert client.get('/reviews.js').status_code==200


def test_failed_activation_demotes_stale_approval_and_preserves_historical_review(pilot):
    client,store,_,log=pilot;release,body=setup_review(client,store,log)
    approved=client.post('/api/v1/releases/'+release['id']+'/review',headers=HEADERS,json=body).json()
    log.write_text(log.read_text()+'\nChanged externally after approval',encoding='utf-8')
    assert client.post('/api/v1/active',headers=HEADERS,json={'release_id':release['id'],'expected_generation':0}).status_code==409
    current=store.get_release(release['id'])
    assert current['status']=='validated' and current['review']==approved['review']
    assert store.active()['release_id'] is None and store.active()['generation']==0


def test_log_changed_during_read_does_not_produce_a_scanned_binding(pilot,monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path
    client,store,_,log=pilot;release,body=setup_review(client,store,log);original=Path.open
    @contextmanager
    def change(path,*args,**kwargs):
        with original(path,*args,**kwargs) as stream:
            if path!=log:yield stream;return
            class Reader:
                def __getattr__(self,name):return getattr(stream,name)
                def read(self,amount=-1):
                    raw=stream.read(amount)
                    with original(log,'ab') as writer:writer.write(b'\nConcurrent NX logging')
                    return raw
            yield Reader()
    monkeypatch.setattr(Path,'open',change)
    response=client.post('/api/v1/logs/test-log/scan',headers=HEADERS,json={'release_id':release['id']})
    assert response.status_code==409,response.text
    assert store.get_release(release['id'])==release


@pytest.mark.parametrize('field',['manifest','nx_version','folder','status'])
def test_release_identity_changed_after_validation_cannot_commit_approval(pilot,monkeypatch,field):
    client,store,_,log=pilot;release,body=setup_review(client,store,log);validate=store.validate_release
    changes={'manifest':'[]','nx_version':'2406','folder':str(store.root/'other-local-candidate'),'status':'building'}
    def change(key,actor):
        report=validate(key,actor)
        with store.db() as db:db.execute('UPDATE releases SET '+field+'=? WHERE id=?',(changes[field],key))
        return report
    monkeypatch.setattr(store,'validate_release',change)
    response=client.post('/api/v1/releases/'+release['id']+'/review',headers=HEADERS,json=body)
    assert response.status_code==409,response.text
    with store.db() as db:assert db.execute('SELECT review FROM releases WHERE id=?',(release['id'],)).fetchone()[0] is None
    assert not any(row['action']=='release.approved' for row in store.audit_entries())

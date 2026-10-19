"""Strict diagnostic contracts and immutable worker artifacts; no real NX/CAD data."""
import copy
import hashlib
import json

import pytest

from nxserver.geometry import GeometryJobs
from nxserver.store import canonical
from test_geometry import HEADERS, pilot, draft, create_job, report_for


def prepared(pilot):
    client,store=pilot
    resource,asset=draft(client)
    job=create_job(client,resource,asset).json()
    return client,store,job


@pytest.mark.parametrize('path',['W:/SiemensShare/nx2406/tool.prt','//server/share/x.prt',
    '../x.prt','folder/../x.prt','folder//x.prt','C:/local/x.prt','con.prt',
    'tool.prt ','folder\\x.prt','x\x00.prt'])
def test_report_paths_are_relative_text_only_and_never_accepted_as_files(pilot,path):
    client,store,job=prepared(pilot)
    report=report_for(job)
    report['files'][0]['path']=path
    response=client.post('/api/v1/geometry-jobs/'+job['id']+'/report',headers=HEADERS,json={'report':report})
    assert response.status_code==400,response.text
    assert GeometryJobs(store).get(job['id'])['report'] is None
    assert store.resources()[0]['cam_status']=='draft'


@pytest.mark.parametrize('change',['body_bool','face_bool','unknown_body','file_size_bool','file_hash',
    'duplicate_file','duplicate_body','duplicate_component','unlisted_component','counts','empty_ready',
    'worker_hashes','resource_hash','unknown_property','errors_in_ready','failed_no_errors'])
def test_invalid_nested_contents_and_false_status_claims_are_rejected(pilot,change):
    client,store,job=prepared(pilot)
    report=report_for(job)
    if change=='body_bool':report['parts'][0]['bodies'][0]['solid']='yes'
    elif change=='face_bool':report['parts'][0]['bodies'][0]['face_count']=True
    elif change=='unknown_body':report['parts'][0]['bodies'][0]['cam_approved']=True
    elif change=='file_size_bool':report['files'][0]['size']=False
    elif change=='file_hash':report['files'][0]['sha256']='not a SHA-256'
    elif change=='duplicate_file':report['files'].append({**report['files'][0],'path':'TOOL.prt'})
    elif change=='duplicate_body':report['parts'][0]['bodies']*=2
    elif change=='duplicate_component':report['components']*=2
    elif change=='unlisted_component':report['components'][0]['part']='missing.prt'
    elif change=='counts':report['solid_prototypes']=999
    elif change=='empty_ready':report.update(parts=[],components=[],files=[])
    elif change=='worker_hashes':report['worker_hashes']={'unexpected.py':'0'*64}
    elif change=='resource_hash':report['resource_hash']='0'*64
    elif change=='unknown_property':report['automatic_publish']=True
    elif change=='errors_in_ready':report['errors']=['Unresolved import failure']
    elif change=='failed_no_errors':report['status']='failed'
    response=client.post('/api/v1/geometry-jobs/'+job['id']+'/report',headers=HEADERS,json={'report':report})
    assert response.status_code==400,response.text
    assert GeometryJobs(store).get(job['id'])['report'] is None


def test_failed_diagnostic_is_preserved_but_never_changes_cam_approval(pilot):
    client,store,job=prepared(pilot)
    report=report_for(job)
    report.update(status='failed',parts=[],components=[],files=[],errors=['Synthetic translator failure, NOT a real NX run.'])
    response=client.post('/api/v1/geometry-jobs/'+job['id']+'/report',headers=HEADERS,json={'report':report})
    assert response.status_code==200,response.text
    assert response.json()['status']=='failed'
    assert store.resources()[0]['revision']==1 and store.resources()[0]['cam_status']=='draft'
    assert store.active()['release_id'] is None


def test_package_stays_byte_identical_after_report_and_software_updates(pilot,monkeypatch):
    client,store,job=prepared(pilot)
    package=GeometryJobs(store).package(job['id'])
    assert len(package)==job['package_size'] and hashlib.sha256(package).hexdigest()==job['package_sha256']
    response=client.post('/api/v1/geometry-jobs/'+job['id']+'/report',headers=HEADERS,json={'report':report_for(job)})
    assert response.status_code==200,response.text
    monkeypatch.setattr('nxserver.geometry.worker_sources',lambda:{'nx_step_import.py':b'New unrelated version'})
    assert GeometryJobs(store).package(job['id'])==package


@pytest.mark.parametrize('damage',['missing','changed','truncated'])
def test_package_damage_cannot_be_hidden_by_regeneration(pilot,damage):
    client,store,job=prepared(pilot)
    path=store.root/'geometry-packages'/job['id']/'NX_STEP.zip'
    if damage=='missing':path.unlink()  # Synthetic artifact only.
    elif damage=='truncated':path.write_bytes(b'Incomplete ZIP')
    else:
        raw=path.read_bytes();path.write_bytes(bytes([raw[0]^1])+raw[1:])
    response=client.get('/api/v1/geometry-jobs/'+job['id']+'/package',headers=HEADERS)
    assert response.status_code==409,response.text
    assert GeometryJobs(store).get(job['id'])['report'] is None


def test_legacy_job_without_snapshot_still_requires_exact_original_workers(pilot,monkeypatch):
    client,store,job=prepared(pilot)
    legacy={key:value for key,value in job.items() if key not in ('package_sha256','package_size')}
    with store.db() as db:
        db.execute('UPDATE geometry_jobs SET payload=? WHERE id=?',(canonical(legacy),job['id']))
    assert client.get('/api/v1/geometry-jobs/'+job['id']+'/package',headers=HEADERS).status_code==200
    monkeypatch.setattr('nxserver.geometry.worker_sources',lambda:{'nx_step_import.py':b'Changed software'})
    assert client.get('/api/v1/geometry-jobs/'+job['id']+'/package',headers=HEADERS).status_code==409

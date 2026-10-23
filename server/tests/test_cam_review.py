"""Resume contracts use inert files and synthetic reports, never actual NX."""
import copy
import hashlib
import json
from pathlib import Path
import urllib.error

import pytest

from nxserver.safety import DomainError,file_fingerprint
from nxserver.store import Store
from tools import resume_cam_review as runner
from tools.nxlab import cam_review
from tools.nxlab.cam_target import prepare,bind_handoff
from tools.nxlab.guard import Blocked
from tools.nxlab.runtime import Server,seed
from tools.run_local_pipeline import archive_to
from test_cam_handoff import installed
from test_geometry import STEP,report_for


@pytest.fixture
def context(installed,tmp_path):
    case=tmp_path/'lab-aaaaaaaaaaaa';case.mkdir();baseline=tmp_path/'target'
    report=prepare(installed,baseline)
    specs=json.loads((Path(__file__).parents[1]/'tools/nxlab/fixtures.json').read_text())
    fixture=specs['cases'][0]
    observed={**copy.deepcopy(fixture['expected']),'units':fixture['units'],
              'mount_point':fixture['mount_point'],'axis':fixture['axis']}
    observed['volumes']=sorted(observed['volumes'])
    created=case/'create';created.mkdir();(created/'reference.prt').write_bytes(b'INERT: no NX geometry.')
    imported=case/'import/output';imported.mkdir(parents=True)
    names=['tool.prt','cut.prt','shank.prt','holder.prt']
    for name in names:(imported/name).write_bytes(('INERT synthetic '+name).encode())
    with Server(case/'server',sources={'nxlab':baseline}) as server:
        release=server.request('/api/v1/releases','POST',{'source':'nxlab','nx_version':'2406','name':'Synthetic context'})
        resource,asset,job,package=seed(server,STEP)
        diagnostic=report_for(job);diagnostic['nx_version']='2406'
        diagnostic['parts']=[{**copy.deepcopy(diagnostic['parts'][0]),'path':name,
            'bodies':[] if index==0 else [copy.deepcopy(diagnostic['parts'][0]['bodies'][0])]}
            for index,name in enumerate(names)]
        diagnostic['components']=[{'id':'/'+str(index),'name':'Synthetic','part':name} for index,name in enumerate(names[1:])]
        diagnostic['files']=[{'path':name,**file_fingerprint(imported/name)} for name in names]
        server.request('/api/v1/geometry-jobs/'+job['id']+'/report','POST',{'report':diagnostic})
        collector=server.request('/api/v1/geometry-jobs/'+job['id']+'/collector-package','POST',
            {'root_library_id':'tool-metric','root_key':'NXTEST_ENDMILL'},binary=True)
        archive_to(collector,case/'collector')
        source={'commit':'a'*40,'source_sha256':'b'*64}
        value=bind_handoff(case=fixture,source=source,baseline=report,release=release,resource=resource,job=job,
            collector=case/'collector',collector_sha256=hashlib.sha256(collector).hexdigest(),observed=observed,imported=observed)
        value.update(server_data=str(server.data),settings=str(case/'server/settings.local.json'))
    value['server_closed']=True
    value['artifacts']={name:{'directory':str(folder),'files':{part.name:file_fingerprint(part) for part in folder.glob('*.prt')}}
        for name,folder in [('created',created),('reopened',created),('import_reopened',imported)]}
    path=case/'cam-handoff.json';path.write_text(json.dumps(value),encoding='utf-8')
    return path,value,copy.deepcopy(source)


def test_existing_case_reopens_its_own_http_server_with_bound_collector_and_report(context,tmp_path):
    path,value,source=context
    loaded,case=cam_review.load(path,source,lab_root=tmp_path)
    assert loaded==value and case==path.parent
    with Server(tmp_path/'resumed',data=case/'server/data') as server:
        result=cam_review.check_server(server,loaded)
        assert result['status']=='passed' and not result['nx_execution_verified']
        assert all(resource['cam_status']=='draft' for resource in server.request('/api/v1/resources'))


@pytest.mark.parametrize('defect',['source','not_closed','outside_data','outside_collector','bad_oracle','missing_artifacts'])
def test_invalid_context_is_rejected_before_model_access(context,tmp_path,monkeypatch,defect):
    path,value,source=context
    if defect=='source':value['source']['source_sha256']='c'*64
    elif defect=='not_closed':value['server_closed']=False
    elif defect=='outside_data':value['server_data']='W:/must-not-be-looked-up'
    elif defect=='outside_collector':value['collector_directory']='//must-not-be-looked-up/share'
    elif defect=='bad_oracle':value['reference_observed']['body_count']=99
    elif defect=='missing_artifacts':value.pop('artifacts')
    path.write_text(json.dumps(value),encoding='utf-8')
    monkeypatch.setattr(cam_review,'file_fingerprint',lambda *a,**k:pytest.fail('Invalid context cannot reach model reads.'))
    with pytest.raises(Blocked):cam_review.load(path,source,lab_root=tmp_path)


def test_changed_model_bytes_cannot_reopen_as_a_qualified_context(context,tmp_path):
    path,value,source=context;part=path.parent/'create/reference.prt'
    part.write_bytes(b'X'*part.stat().st_size)
    with pytest.raises(Blocked,match='artifact changed'):cam_review.load(path,source,lab_root=tmp_path)


@pytest.mark.parametrize('defect',['report_hash','report_count','release','collector_helper','collector_archive'])
def test_changed_server_or_package_evidence_blocks_review(context,tmp_path,defect):
    path,value,source=context;case=path.parent;data=case/'server/data'
    loaded,_=cam_review.load(path,source,lab_root=tmp_path)
    if defect.startswith('report_'):
        store=Store(data)
        with store.db() as db:
            raw=json.loads(db.execute('SELECT report FROM geometry_jobs WHERE id=?',(value['job_id'],)).fetchone()[0])
            if defect=='report_hash':raw['files'][0]['sha256']='0'*64
            else:raw['parts'][1]['bodies']=[]
            db.execute('UPDATE geometry_jobs SET report=? WHERE id=?',(json.dumps(raw),value['job_id']))
    elif defect=='release':loaded['target_manifest'][0]['sha256']='0'*64
    elif defect=='collector_helper':(case/'collector/nx_result_collect.py').write_bytes(b'INERT changed helper')
    elif defect=='collector_archive':
        (data/'geometry-results/collectors'/value['job_id']/value['collector_sha256']/'collector.zip').write_bytes(b'INERT changed archive')
    with Server(tmp_path/'resumed',data=data) as server:
        with pytest.raises((Blocked,DomainError,urllib.error.HTTPError)):
            cam_review.check_server(server,loaded)


def test_check_only_writes_awaiting_cam_error_and_never_persists_the_session_key(context,tmp_path,monkeypatch,capsys):
    path,value,source=context;original_load=cam_review.load;tokens=[]
    monkeypatch.setattr(runner,'source_state',lambda:source)
    monkeypatch.setattr(runner,'load',lambda path,source:original_load(path,source,lab_root=tmp_path))
    class Capture(Server):
        def __enter__(self):
            result=super().__enter__();tokens.append(self.token);return result
    monkeypatch.setattr(runner,'Server',Capture)
    root=tmp_path/'reports'
    assert runner.main(['--handoff',str(path),'--check-only','--root',str(root)])==2
    directory=next(root.iterdir());result=json.loads((directory/'summary.json').read_text())
    assert result['status']=='awaiting_cam' and not result['complete_product_acceptance']
    output=capsys.readouterr().out
    for token in tokens:
        assert token not in output
        assert all(token not in file.read_text() for file in [directory/'summary.json',directory/'junit.xml',directory/'server/server.log'])
    import xml.etree.ElementTree as ET
    assert ET.parse(directory/'junit.xml').getroot().attrib['errors']=='1'


@pytest.mark.parametrize('value',['W:/never-read/cam-handoff.json','//never-read/share/cam-handoff.json','C:/outside/cam-handoff.json'])
def test_handoff_namespace_is_rejected_before_first_filesystem_lookup(monkeypatch,value):
    monkeypatch.setattr(Path,'lstat',lambda *a,**k:pytest.fail('Rejected namespace reached the filesystem.'))
    with pytest.raises(Blocked):cam_review.load(value,{})

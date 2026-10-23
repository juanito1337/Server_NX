"""The real-CAM pilot must have a usable, isolated server return target."""
import hashlib
from pathlib import Path

import pytest

from nxserver.libraries import SPECS,NativeTable
from nxserver.safety import DomainError
from tools.nxlab.cam_target import LIBRARIES,prepare,bind_handoff
from tools.nxlab.packet_contract import table,adopt_drafts
from tools.nxlab.runtime import Server,seed
from test_geometry import STEP


@pytest.fixture
def installed(tmp_path):
    root=tmp_path/'installation'
    for identifier in LIBRARIES:
        fields=['LIBRF','DESCR','HLDREF'] if identifier=='tool-metric' else ['LIBRF','DESCR']
        values=['STOCK','Synthetic stock',''] if len(fields)==3 else ['STOCK','Synthetic stock']
        text='# Synthetic header retained\n'+table(fields,values)
        if identifier=='shank-metric':
            text+=table(['LIBRF','DIAM'],['STOCK','10'])
        path=root/SPECS[identifier]['path'];path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
    return root


def test_empty_target_preserves_every_schema_and_leaves_installed_bytes_unchanged(installed,tmp_path):
    before={path.relative_to(installed).as_posix():path.read_bytes() for path in installed.rglob('*.dat')}
    output=tmp_path/'empty';report=prepare(installed,output)
    assert report['complete'] and len(report['libraries'])==len(LIBRARIES)
    assert not report['stock_objects_copied'] and not report['nx_execution_verified']
    for item in report['libraries']:
        original=NativeTable(before[item['path']].decode());path=output/item['path'];empty=NativeTable.read(path)
        assert not empty.records and empty.blocks==original.blocks
        assert path.read_text().startswith('# Synthetic header retained\n')
        assert hashlib.sha256(path.read_bytes()).hexdigest()==item['target_sha256']
    assert before=={path.relative_to(installed).as_posix():path.read_bytes() for path in installed.rglob('*.dat')}
    with pytest.raises(FileExistsError):prepare(installed,output)


def test_source_target_overlap_and_protected_text_are_rejected_before_writes(installed,monkeypatch):
    with pytest.raises(DomainError):prepare(installed,installed/'nested')
    monkeypatch.setattr(Path,'lstat',lambda *a,**k:pytest.fail('No lookup allowed for protected input.'))
    with pytest.raises(DomainError):prepare(r'W:\never-read',r'W:\never-written')


def test_missing_installed_library_never_returns_a_complete_target(installed,tmp_path):
    (installed/SPECS[LIBRARIES[-1]]['path']).unlink()
    with pytest.raises(DomainError):prepare(installed,tmp_path/'partial')


def test_real_http_empty_target_accepts_related_drafts_without_stock_or_approval(installed,tmp_path):
    directory=tmp_path/'http';baseline=directory/'empty';report=prepare(installed,baseline)
    with Server(directory/'server',sources={'nxlab':baseline}) as server:
        release=server.request('/api/v1/releases','POST',{'source':'nxlab','nx_version':'2406','name':'Empty return target'})
        resource,asset,job,package=seed(server,STEP)
        collector=server.request('/api/v1/geometry-jobs/'+job['id']+'/collector-package','POST',
            {'root_library_id':'tool-metric','root_key':'NXTEST_ENDMILL'},binary=True)
        handoff=bind_handoff(case={'id':'endmill'},source={'commit':'fixture','source_sha256':'a'*64},baseline=report,
            release=release,resource=resource,job=job,collector=directory/'collector',
            collector_sha256=hashlib.sha256(collector).hexdigest(),observed={},imported={})
        assert handoff['target_release_id']==release['id'] and handoff['input_sha256']==asset['sha256']
        assert handoff['root_key']=='NXTEST_ENDMILL' and handoff['status']=='awaiting_cam'
        assert handoff['automatic_approval'] is handoff['nx_cam_acceptance_verified'] is False
        assert all('token' not in key.lower() for key in handoff)
        result,resources=adopt_drafts(server,release,job)
        assert result['manifest']['nx_version']=='2406' and all(value['cam_status']=='draft' for value in resources)
        assert len(release['manifest'])==len(LIBRARIES) and collector


@pytest.mark.parametrize('defect',['version','resource','revision','partial','stock','manifest','collector'])
def test_handoff_cannot_bind_another_job_or_an_incomplete_target(tmp_path,defect):
    resource={'id':'resource','revision':1};job={'id':'job','resource_id':'resource','resource_revision':1,
        'resource_hash':'a'*64,'input':{'sha256':'b'*64},'package_sha256':'c'*64,'nx_version':'2406'}
    entries=[{'path':SPECS[key]['path'],'object_count':0,'target_sha256':'d'*64,'target_size':5} for key in LIBRARIES]
    release={'id':'release','nx_version':'2406','manifest':[{'path':item['path'],'sha256':'d'*64,'size':5} for item in entries]}
    baseline={'complete':True,'libraries':entries};collector_hash='e'*64
    if defect=='version':release['nx_version']='2512'
    elif defect=='resource':job['resource_id']='another'
    elif defect=='revision':job['resource_revision']=2
    elif defect=='partial':baseline['complete']=False
    elif defect=='stock':baseline['libraries'][0]['object_count']=1
    elif defect=='manifest':release['manifest'][0]['sha256']='f'*64
    elif defect=='collector':collector_hash='not-a-hash'
    message={'version':'target differs','resource':'resource revision','revision':'resource revision',
             'partial':'complete empty','stock':'complete empty','manifest':'manifest differs','collector':'archive hash'}[defect]
    with pytest.raises(RuntimeError,match=message):
        bind_handoff(case={'id':'endmill'},source={},baseline=baseline,release=release,resource=resource,
            job=job,collector=tmp_path/'collector',collector_sha256=collector_hash,observed={},imported={})


def test_phase_hashes_each_real_saved_artifact_without_hashing_other_outputs(tmp_path,monkeypatch):
    import json
    from tools import run_local_pipeline as runner
    models=tmp_path/'models';models.mkdir();(models/'root.prt').write_bytes(b'inert root model fixture')
    (models/'component.prt').write_bytes(b'inert component fixture');(models/'server.log').write_bytes(b'not a model')
    def observe(*a,directory,**k):
        (directory/'result.json').write_text(json.dumps({'status':'passed','case':'fixture','phase':'measure','observed':{}}))
    monkeypatch.setattr(runner,'run_owned',observe)
    report=runner.phase(tmp_path/'phase',{'phase':'measure','case':{'id':'fixture'},'part':str(models/'root.prt')},tmp_path,{})
    assert set(report['artifacts']['files'])=={'root.prt','component.prt'}
    assert report['artifacts']['files']['root.prt']=={'size':24,'sha256':hashlib.sha256(b'inert root model fixture').hexdigest()}

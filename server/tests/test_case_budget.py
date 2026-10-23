"""One case budget covers model sessions and HTTP; all native calls are inert."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest

from nxserver.safety import DomainError
from tools.nxlab import cases
from tools.nxlab.guard import Blocked,local
from tools import run_local_pipeline as runner


@pytest.fixture
def context(tmp_path,monkeypatch):
    work=tmp_path/'case';work.mkdir();policy=tmp_path/'policy.json';policy.write_text('{}')
    baseline=tmp_path/'baseline';baseline.mkdir();clock={'now':0.0}
    monkeypatch.setattr(cases.time,'monotonic',lambda:clock['now'])
    monkeypatch.setattr(cases,'server_python',lambda:(tmp_path/'python.exe',[],{}))
    source={'commit':'a'*40,'dirty':False,'files':{},'source_sha256':'b'*64}
    arguments={'case_id':'endmill','kind':'positive','profile':{'schema_version':1,'nx_version':'2406','developer_sid':'fixture'},
        'policy':policy,'source':source,'baseline':{},'baseline_root':baseline,
        'environment':{'UGII_BAD':'must-not-inherit','PYTHONPATH':'must-not-inherit','GIT_DIR':'must-not-inherit'},'started':0.0}
    return work,arguments,clock


def receipt(task,sha):
    identifier=task['case_id'];positive=task['kind']=='positive'
    return {'schema_version':1,'scope':'nxlab_case_result','task_sha256':sha,'case_id':identifier,
        'source_sha256':task['source']['source_sha256'],
        'result':{'id':identifier,'status':'awaiting_cam' if positive else 'passed'},
        'handoff':{'case_id':identifier,'status':'awaiting_cam','source':task['source'],'server_closed':True} if positive else None}


def complete(monkeypatch,context,defect=None):
    work,arguments,clock=context;observed={}
    def run(command,*,directory,environment,timeout,cwd):
        assert not (work/'cam-handoff.json').exists()
        task=json.loads((work/'case-task.json').read_text());value=receipt(task,command[-1])
        observed.update(timeout=timeout,environment=environment,command=command,cwd=cwd,task=task)
        assert directory==work
        if defect=='missing':return
        if defect=='task':value['task_sha256']='c'*64
        if defect=='case':value['case_id']='drill'
        if defect=='source':value['source_sha256']='c'*64
        if defect=='scope':value['scope']='other'
        if defect=='version':value['schema_version']=2
        if defect=='status':value['result']['status']='passed'
        if defect=='open_server':value['handoff']['server_closed']=False
        if defect=='handoff_source':value['handoff']['source']={}
        if defect in ('blocked','failed'):value['result'].update(status=defect,message='Synthetic outcome')
        (work/'case-result.json').write_text(json.dumps(value))
        if defect=='malformed':(work/'case-result.json').write_bytes(b'{')
        clock['now']=901 if defect=='late' else clock['now']+80
        if defect in ('timeout','unclosed'):raise RuntimeError('Owned case '+defect)
        observed['closed']=True
        assert not (work/'cam-handoff.json').exists()
    monkeypatch.setattr(cases,'run_owned',run)
    return observed


@pytest.mark.parametrize('kind',['positive','negative'])
def test_case_has_one_total_budget_and_publishes_only_after_owned_close(context,monkeypatch,kind):
    work,arguments,clock=context;arguments['kind']=kind
    original=cases.file_fingerprint
    def prepare(*args,**kwargs):
        clock['now']+=125
        return original(*args,**kwargs)
    monkeypatch.setattr(cases,'file_fingerprint',prepare)
    observed=complete(monkeypatch,context)
    write=runner.write
    def publish(path,value):
        if path.name=='cam-handoff.json':assert observed['closed']
        return write(path,value)
    monkeypatch.setattr(runner,'write',publish)
    result=cases.supervise(work,**arguments)
    assert observed['timeout']==775 and result['execution_limit_seconds']==900
    assert result['execution_seconds']==205 and observed['closed']
    assert observed['cwd']==runner.SERVER
    assert all(name not in observed['environment'] for name in ('UGII_BAD','PYTHONPATH','GIT_DIR'))
    assert (work/'cam-handoff.json').exists()==(kind=='positive')
    assert result['case_receipt_sha256']==hashlib.sha256((work/'case-result.json').read_bytes()).hexdigest()


@pytest.mark.parametrize('defect',['missing','task','case','source','scope','version','status','open_server',
    'handoff_source','blocked','failed','malformed','late','timeout','unclosed'])
def test_failed_partial_late_or_unclosed_case_never_publishes_handoff(context,monkeypatch,defect):
    work,arguments,_=context;complete(monkeypatch,context,defect)
    expected=Blocked if defect=='blocked' else (RuntimeError,OSError,ValueError,DomainError)
    with pytest.raises(expected):cases.supervise(work,**arguments)
    assert not (work/'cam-handoff.json').exists()


def test_expired_budget_never_starts_another_process_or_writes_task(context,monkeypatch):
    work,arguments,clock=context;clock['now']=900
    monkeypatch.setattr(cases,'run_owned',lambda *a,**k:pytest.fail('No start after the case budget.'))
    with pytest.raises(RuntimeError,match='budget exhausted'):cases.supervise(work,**arguments)
    assert not list(work.iterdir())


@pytest.fixture
def worker_task(tmp_path,monkeypatch):
    work=tmp_path/'worker';work.mkdir();policy=tmp_path/'policy.json';policy.write_text('{}')
    source={'commit':'a'*40,'dirty':False,'files':{},'source_sha256':'b'*64}
    task={'schema_version':1,'scope':'nxlab_case_task','case_id':'endmill','kind':'positive',
        'profile':{'developer_sid':'fixture','schema_version':1,'nx_version':'2406'},'source':source,
        'policy_file':str(policy),'policy_sha256':hashlib.sha256(policy.read_bytes()).hexdigest(),
        'baseline':{},'baseline_root':str(tmp_path/'baseline')}
    path=work/'case-task.json';path.write_text(json.dumps(task));sha=hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setenv('NX_LAB_CASE_TASK',sha)
    events=[]
    monkeypatch.setattr(cases,'_workdir',lambda value:work)
    monkeypatch.setattr(runner,'source_state',lambda:source)
    monkeypatch.setattr(cases,'isolation',lambda *a:events.append('isolation'))
    monkeypatch.setattr(runner,'installation_for',lambda *a:(events.append('metadata') or tmp_path))
    monkeypatch.setattr(runner,'nx_environment',lambda *a:(events.append('environment') or {}))
    def positive(*args):
        events.append('model');return {'id':'endmill','status':'awaiting_cam'},{'case_id':'endmill','server_closed':True}
    monkeypatch.setattr(runner,'positive_case',positive)
    return work,path,sha,task,source,events,policy


def test_worker_checks_isolation_before_native_setup_and_keeps_handoff_provisional(worker_task):
    work,path,sha,task,source,events,_=worker_task
    assert cases.worker(path,sha)==0
    value=json.loads((work/'case-result.json').read_text())
    assert events==['isolation','metadata','environment','model']
    assert value['result']['status']=='awaiting_cam' and value['handoff']['server_closed']
    assert not (work/'cam-handoff.json').exists()


@pytest.mark.parametrize('fault',['source_before','policy_before','isolation','license','source_after','policy_after'])
def test_worker_does_not_qualify_changed_inputs_or_missing_prerequisites(worker_task,monkeypatch,fault):
    work,path,sha,task,source,events,policy=worker_task
    if fault=='source_before':monkeypatch.setattr(runner,'source_state',lambda:{})
    if fault=='policy_before':policy.write_text('{"changed":true}')
    if fault=='isolation':
        def denied(*args):raise Blocked('Dedicated account missing')
        monkeypatch.setattr(cases,'isolation',denied)
    def fail(*args):
        events.append('model')
        if fault=='license':raise Blocked('License unavailable')
        if fault=='source_after':monkeypatch.setattr(runner,'source_state',lambda:{})
        if fault=='policy_after':policy.write_text('{"changed":true}')
        return {'id':'endmill','status':'awaiting_cam'},{'case_id':'endmill','server_closed':True}
    monkeypatch.setattr(runner,'positive_case',fail)
    assert cases.worker(path,sha)==0
    value=json.loads((work/'case-result.json').read_text())
    assert value['result']['status']==('blocked' if fault in ('isolation','license') else 'failed')
    assert value['handoff'] is None and not (work/'cam-handoff.json').exists()
    if fault in ('source_before','policy_before','isolation'):assert 'metadata' not in events


def test_task_hash_is_checked_before_any_isolation_or_model_call(worker_task):
    work,path,sha,task,source,events,_=worker_task
    path.write_text('{"tampered":true}')
    with pytest.raises(RuntimeError,match='task hash'):cases.worker(path,sha)
    assert not events and not (work/'case-result.json').exists()


def test_internal_worker_refuses_unsupervised_invocation_before_file_access(worker_task,monkeypatch):
    work,path,sha,task,source,events,_=worker_task
    monkeypatch.delenv('NX_LAB_CASE_TASK')
    monkeypatch.setattr(cases,'_workdir',lambda *a:pytest.fail('No lookup for an unsupervised invocation.'))
    with pytest.raises(RuntimeError,match='supervised controller'):cases.worker(path,sha)
    assert not events


@pytest.mark.parametrize('close_error',[False,True])
def test_positive_case_only_returns_a_provisional_context_after_server_close(tmp_path,monkeypatch,close_error):
    import tools.nxlab.cam_target as target
    work=tmp_path/'positive';work.mkdir();step=work/'input.step';step.write_bytes(b'inert STEP transport fixture')
    events=[];case={'id':'endmill','tool_type':'milling_square'};source={'source_sha256':'a'*64}
    def phase(directory,task,*args):
        events.append(task['phase'])
        return {'generated':{'reference':str(work/'reference.prt'),'step':str(step)},'observed':{},'artifacts':{}}
    class Server:
        def __init__(self,directory,**kwargs):self.data=directory/'data'
        def __enter__(self):events.append('server-open');return self
        def __exit__(self,*args):
            events.append('server-close')
            if close_error:raise RuntimeError('Owned server did not close')
        def request(self,path,*args,**kwargs):
            if path=='/api/v1/releases':
                assert args[1]['name']=='NX lab endmill empty native target'
                return {'id':'release'}
            return b'inert collector' if path.endswith('collector-package') else {}
    def archive(content,directory):
        directory.mkdir();(directory/'output').mkdir();return directory
    def native(command,*,directory,**kwargs):
        (directory/'output/report.json').write_text(json.dumps({'status':'needs_selection'}))
    monkeypatch.setattr(runner,'phase',phase);monkeypatch.setattr(runner,'compare',lambda *a:[])
    monkeypatch.setattr(runner,'Server',Server);monkeypatch.setattr(runner,'archive_to',archive)
    monkeypatch.setattr(runner,'run_owned',native)
    monkeypatch.setattr(runner,'seed',lambda *a,**k:({'id':'resource'},{},{'id':'job'},b'inert package'))
    monkeypatch.setattr(target,'bind_handoff',lambda **k:{'case_id':k['case']['id'],'source':k['source']})
    if close_error:
        with pytest.raises(RuntimeError,match='did not close'):
            runner.positive_case(work,case,tmp_path,{}, {'tolerances':{}},{},tmp_path,source)
    else:
        result,handoff=runner.positive_case(work,case,tmp_path,{}, {'tolerances':{}},{},tmp_path,source)
        assert result['status']=='awaiting_cam' and handoff['server_closed'] is True
        assert handoff['source']==source and events[-1]=='server-close'
    assert not (work/'cam-handoff.json').exists()


@pytest.mark.parametrize('value',['W:/never-read/case-task.json',r'\\never-read\share\case-task.json'])
def test_protected_worker_task_text_is_rejected_with_mocked_access_only(monkeypatch,value):
    monkeypatch.setattr(Path,'lstat',lambda *a,**k:pytest.fail('No protected lookup.'))
    with pytest.raises(DomainError):cases._workdir(value)


@pytest.mark.skipif(os.name!='nt',reason='Actual Windows nested Job Objects; inert Python only, never NX.')
def test_whole_case_timeout_closes_a_nested_native_job_without_process_name_lookup(tmp_path):
    from tools.nxlab.runtime import run_owned
    from tools.nxlab.processes import OwnedJob
    import ctypes
    from ctypes import wintypes
    marker=local(tmp_path/'child-open.bin')
    grandchild='import time; stream=open('+repr(str(marker))+',"wb"); stream.write(b"owned"); stream.flush(); time.sleep(60)'
    worker=('from tools.nxlab.processes import OwnedJob; import sys,subprocess;\n'
        'with OwnedJob() as inner:\n'
        ' p=inner.start([sys.executable,"-E","-s","-c",'+repr(grandchild)+'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n'
        ' p.wait()\n')
    environment={key:value for key,value in os.environ.items() if key.upper() in ('SYSTEMROOT','WINDIR','COMSPEC')}
    environment.update(PATH=str(local(sys.executable).parent)+os.pathsep+r'C:\Windows\System32',TEMP=str(tmp_path),TMP=str(tmp_path))
    with pytest.raises(RuntimeError,match='timeout'):
        run_owned([str(local(sys.executable)),'-E','-s','-c',worker],directory=tmp_path,cwd=runner.SERVER,environment=environment,timeout=5)
    assert marker.read_bytes()==b'owned','The nested child must have run before the timeout.'
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    api.CreateFileW.restype=wintypes.HANDLE
    api.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=api.CreateFileW(str(marker),0x80000000,0,None,3,0,None)
    assert handle not in (None,ctypes.c_void_p(-1).value),'The killed descendant must release its exclusive file access.'
    api.CloseHandle(handle)

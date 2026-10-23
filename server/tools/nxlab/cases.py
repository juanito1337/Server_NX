"""One owned worker bounds an entire automatic NX case, including HTTP waits.

Internal worker entry point. Use run_local_pipeline --stage nx. Both the
controller and this worker require the dedicated-account isolation policy.
No successful handoff is published by a worker which can still time out.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from nxserver.safety import DomainError, file_fingerprint, read_bounded_file
from .guard import Blocked, isolation, local
from .runtime import run_owned, server_python

CASE_SECONDS=15*60
TASK_BYTES=512*1024
RECEIPT_BYTES=8*1024*1024


def _workdir(task_path):
    path=local(task_path)
    if (path.name!='case-task.json' or path.parent.parent!=local('C:/NX_Migration/ToolJobs') or
            not re.fullmatch(r'(?:lab|fault)-[a-f0-9]{12}',path.parent.name)):
        raise DomainError('Case workers require their own generated ToolJobs directory.')
    return path.parent


def supervise(work,*,case_id,kind,profile,policy,source,baseline,baseline_root,environment,started):
    """All native processes, servers and blocking IO are inside one owned job."""
    from tools import run_local_pipeline as runner
    work=local(work)
    def remaining():
        value=CASE_SECONDS-(time.monotonic()-started)
        if not 0<value<=CASE_SECONDS:raise RuntimeError('NX case execution budget exhausted; no successful handoff published.')
        return value
    remaining()
    policy=local(policy)
    task={'schema_version':1,'scope':'nxlab_case_task','case_id':case_id,'kind':kind,
        'profile':profile,'policy_file':str(policy),'policy_sha256':file_fingerprint(policy,max_bytes=1024*1024)['sha256'],
        'source':source,'baseline':baseline,'baseline_root':str(local(baseline_root))}
    task_path=work/'case-task.json';runner.write(task_path,task)
    task_sha=hashlib.sha256(read_bounded_file(task_path,max_bytes=TASK_BYTES)).hexdigest()
    executable,flags,python_environment=server_python()
    # Server Python must not start with NX/Python/Git/module search settings.
    child={key:value for key,value in environment.items()
        if key.upper() in ('SYSTEMROOT','WINDIR','COMSPEC','SPLM_LICENSE_SERVER','UGS_LICENSE_SERVER')}
    for name,folder in [('USERPROFILE','python-user'),('APPDATA','python-user/appdata'),
                        ('LOCALAPPDATA','python-user/local'),('TEMP','python-temp'),('TMP','python-temp')]:
        directory=local(work/folder);directory.mkdir(parents=True,exist_ok=True);child[name]=str(directory)
    child.update(PATH=str(executable.parent)+os.pathsep+r'C:\Windows\System32',PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1',
                 NX_LAB_CASE_TASK=task_sha)
    child.update(python_environment)
    # run_owned creates the suspended process in a Job Object before resuming.
    # The worker's NX jobs are nested; every descendant also belongs to this job.
    run_owned([str(executable),*flags,'-E','-s','-m','tools.nxlab.cases',str(task_path),task_sha],
              directory=work,environment=child,timeout=remaining(),cwd=runner.SERVER)
    elapsed=CASE_SECONDS-remaining()
    raw=read_bounded_file(local(work/'case-result.json'),max_bytes=RECEIPT_BYTES)
    receipt=json.loads(raw)
    if (receipt.get('schema_version')!=1 or receipt.get('scope')!='nxlab_case_result' or
            receipt.get('task_sha256')!=task_sha or receipt.get('case_id')!=case_id or
            receipt.get('source_sha256')!=source['source_sha256']):
        raise RuntimeError('Missing or mismatched supervised NX case receipt.')
    result=receipt.get('result');handoff=receipt.get('handoff')
    if not isinstance(result,dict) or result.get('id')!=case_id:
        raise RuntimeError('Supervised NX case result does not identify this case.')
    if result.get('status')=='blocked':raise Blocked(result.get('message','NX case prerequisites unavailable.'))
    if result.get('status')=='failed':raise RuntimeError(result.get('message','NX case failed.'))
    expected='awaiting_cam' if kind=='positive' else 'passed' if kind=='negative' else None
    if expected is None or result.get('status')!=expected:
        raise RuntimeError('Supervised NX case returned an unsupported outcome.')
    if kind=='positive':
        if (not isinstance(handoff,dict) or handoff.get('case_id')!=case_id or
                handoff.get('status')!='awaiting_cam' or handoff.get('server_closed') is not True or
                handoff.get('source')!=source):
            raise RuntimeError('Supervised case has no closed, source-bound CAM handoff.')
        # The entire owned job is now closed and within budget, including its
        # server. Partial worker output can never publish this final marker.
        runner.write(work/'cam-handoff.json',handoff)
    elif handoff is not None:raise RuntimeError('A counterexample cannot publish a CAM handoff.')
    result.update(execution_limit_seconds=CASE_SECONDS,execution_seconds=round(elapsed,3),
                  case_task_sha256=task_sha,case_receipt_sha256=hashlib.sha256(raw).hexdigest())
    return result


def worker(task_path,task_sha):
    from tools import run_local_pipeline as runner
    # Prevent accidental standalone use without the controller's deadline.
    # This marker is an input hash, not a credential or a security boundary.
    if os.environ.pop('NX_LAB_CASE_TASK',None)!=task_sha:
        raise RuntimeError('Internal NX case worker requires its supervised controller.')
    work=_workdir(task_path)
    raw=read_bounded_file(local(task_path),max_bytes=TASK_BYTES)
    if not re.fullmatch('[a-f0-9]{64}',task_sha) or hashlib.sha256(raw).hexdigest()!=task_sha:
        raise RuntimeError('Case task hash differs before execution.')
    task=json.loads(raw)
    if task.get('schema_version')!=1 or task.get('scope')!='nxlab_case_task' or task.get('kind') not in ('positive','negative'):
        raise RuntimeError('Unsupported supervised case task.')
    identifier=task['case_id'];result={'id':identifier,'status':'failed','directory':str(work)};handoff=None
    try:
        if runner.source_state()!=task['source']:raise RuntimeError('Case source differs before execution.')
        policy=local(task['policy_file'])
        if file_fingerprint(policy,max_bytes=1024*1024)['sha256']!=task['policy_sha256']:
            raise RuntimeError('Isolation policy changed before case execution.')
        isolation(policy,task['profile']['developer_sid'])
        installation=runner.installation_for(task['profile'])
        environment=runner.nx_environment(installation,work/'runtime')
        specs=json.loads(local(runner.SERVER/'tools/nxlab/fixtures.json').read_text(encoding='utf-8'))
        if task['kind']=='positive':
            case=next(item for item in specs['cases'] if item['id']==identifier)
            result,handoff=runner.positive_case(work,case,installation,environment,specs,
                task['baseline'],local(task['baseline_root']),task['source'])
        else:
            from .faults import counterexamples
            fault=next(item for item in counterexamples(specs) if item['id']==identifier)
            result=runner.negative_case(work,fault,installation,environment,specs)
        if runner.source_state()!=task['source']:raise RuntimeError('Case source changed during execution.')
        if file_fingerprint(policy,max_bytes=1024*1024)['sha256']!=task['policy_sha256']:
            raise RuntimeError('Isolation policy changed during case execution.')
    except Exception as error:
        result={'id':identifier,'status':'blocked' if isinstance(error,Blocked) else 'failed',
                'directory':str(work),'message':str(error)};handoff=None
    runner.write(work/'case-result.json',{'schema_version':1,'scope':'nxlab_case_result','case_id':identifier,
        'task_sha256':task_sha,'source_sha256':task['source']['source_sha256'],'result':result,'handoff':handoff})
    # Zero confirms transport completion only. The controller must validate the
    # receipt/outcome after owned-process closure, before publishing any handoff.
    return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('task');parser.add_argument('sha256')
    arguments=parser.parse_args();sys.exit(worker(arguments.task,arguments.sha256))

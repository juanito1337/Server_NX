"""Bounded Windows CI runtime checks, with inert owned-process receipts."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
from nxserver.safety import DomainError
from tools import run_ci_tests as ci
from tools.nxlab import processes


def layout(tmp_path):
    root=tmp_path/'windows';home=root/'System32/WindowsPowerShell/v1.0'
    (home/'Modules').mkdir(parents=True);(home/'powershell.exe').write_bytes(b'Inert, never executed')
    report=tmp_path/'report';report.mkdir()
    return root,home,report


def test_runtime_uses_own_cache_and_local_system_modules_without_changing_input(tmp_path):
    root,home,report=layout(tmp_path)
    source={'SYSTEMROOT':str(root),'PSModulePath':'W:/never-import','PSModuleAnalysisCachePath':'W:/never-cache'}
    executable,updates=ci.powershell_settings(report,source)
    assert executable==home/'powershell.exe'
    assert updates=={'PSModulePath':str(home/'Modules'),'PSModuleAnalysisCachePath':str(report/'powershell-module.cache'),
                     'PSDisableModuleAnalysisCacheCleanup':'1'}
    assert source['PSModulePath']=='W:/never-import' and not (report/'powershell-module.cache').exists()


@pytest.mark.parametrize('root',['', 'relative', 'W:/never-read', 'w:relative', r'\\never-read\share'])
def test_bad_system_root_is_rejected_before_first_lookup(tmp_path,monkeypatch,root):
    def forbidden(*args,**kwargs):raise AssertionError('Unexpected filesystem access')
    for name in ('is_file','is_dir','stat','lstat','open'):monkeypatch.setattr(Path,name,forbidden)
    with pytest.raises(DomainError):ci.powershell_settings(tmp_path,{'SYSTEMROOT':root})


def fake_job(monkeypatch,receipt,*,code=0,timeout=False,active=0):
    observed={}
    class Process:
        def wait(self,timeout):
            observed['timeout']=timeout
            if observed.get('raise_timeout'):raise subprocess.TimeoutExpired('inert-probe',timeout)
            return code
    class Job:
        def __enter__(self):observed['entered']=True;return self
        def __exit__(self,*args):observed['closed']=True
        def start(self,command,**options):
            observed.update(command=command,options=options)
            options['stdout'].write(json.dumps(receipt).encode());return Process()
        def active(self):return active
    observed['raise_timeout']=timeout
    monkeypatch.setattr(processes,'OwnedJob',Job)
    return observed


def test_owned_preflight_binds_compiler_and_receipt_bytes(tmp_path,monkeypatch):
    root,home,report=layout(tmp_path);monkeypatch.setenv('SYSTEMROOT',str(root))
    receipt={'version':'5.1.123.4','compiler':True,'module_paths':[str(home/'Modules')],
             'module_cache':str(report/'powershell-module.cache')}
    observed=fake_job(monkeypatch,receipt)
    before=dict(os.environ)
    result=ci.prepare_powershell(report)
    assert dict(os.environ)==before
    assert observed['entered'] and observed['closed'] and observed['timeout']==60
    assert observed['command'][0]==str(home/'powershell.exe') and '-NoProfile' in observed['command']
    assert observed['options']['cwd']==report
    assert observed['options']['env']['PSDisableModuleAnalysisCacheCleanup']=='1'
    assert result['compiler_verified'] and result['scope']=='windows_ci_runtime_preflight'
    assert result['receipt_sha256']==hashlib.sha256((report/'powershell-preflight.log').read_bytes()).hexdigest()
    assert result['script_sha256']==hashlib.sha256((report/'powershell-preflight.ps1').read_bytes()).hexdigest()


@pytest.mark.parametrize('fault',['version','compiler','cache','network_module','exit','timeout','descendant'])
def test_bad_runtime_or_unclosed_owned_process_never_passes(tmp_path,monkeypatch,fault):
    root,home,report=layout(tmp_path);monkeypatch.setenv('SYSTEMROOT',str(root))
    receipt={'version':'5.1.123.4','compiler':True,'module_paths':[str(home/'Modules')],
             'module_cache':str(report/'powershell-module.cache')}
    if fault=='version':receipt['version']='7.5.0'
    if fault=='compiler':receipt['compiler']=False
    if fault=='cache':receipt['module_cache']='foreign-cache'
    if fault=='network_module':receipt['module_paths']=['W:/never-import']
    observed=fake_job(monkeypatch,receipt,code=9 if fault=='exit' else 0,timeout=fault=='timeout',active=fault=='descendant')
    with pytest.raises(DomainError):ci.prepare_powershell(report)
    assert observed['closed']

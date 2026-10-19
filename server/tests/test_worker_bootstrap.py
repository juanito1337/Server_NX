"""Standalone downloaded journal bootstrap, synthetic packages, NEVER NX execution."""
import io
import json
import importlib.util
import os
import runpy
import py_compile
import struct
import shutil
import sys
import types
import uuid
import zipfile
from pathlib import Path

import pytest

from nxserver.safety import offline_path
from test_geometry import pilot,draft,create_job,HEADERS,STEP

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Actual standalone Windows journal-path contract, no NX.')


@pytest.fixture
def local_package(pilot):
    client,store=pilot;resource,asset=draft(client)
    job=create_job(client,resource,asset).json()
    parent=offline_path('C:/NX_Migration/ToolJobs');parent.mkdir(parents=True,exist_ok=True)
    directory=offline_path(parent/('bootstrap-probe-'+uuid.uuid4().hex[:10]))
    assert not directory.exists();directory.mkdir()
    archive=client.get('/api/v1/geometry-jobs/'+job['id']+'/package',headers=HEADERS).content
    original_path=list(sys.path);original_modules={name:sys.modules.get(name) for name in ('nx_safety','stepfiles')}
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:zipped.extractall(directory)
        yield directory,job,store
    finally:
        sys.path[:]=original_path
        for name,module in original_modules.items():
            if module is None:sys.modules.pop(name,None)
            else:sys.modules[name]=module
        assert directory.parent==parent and directory.name.startswith('bootstrap-probe-')
        shutil.rmtree(offline_path(directory))  # Exact newly created synthetic fixture only.


def execute(directory):
    # __name__ is NOT __main__: main()/NXOpen/STEP import are never invoked.
    return runpy.run_path(str(directory/'nx_step_import.py'),run_name='isolated_worker_probe')


def test_cached_foreign_helpers_cannot_replace_pinned_package_sources(local_package,monkeypatch):
    directory,job,_=local_package
    foreign_safety=types.ModuleType('nx_safety');foreign_safety.DomainError=ValueError
    foreign_safety.offline_path=lambda value:Path(value);foreign_safety.tree_files=lambda value:[]
    foreign_step=types.ModuleType('stepfiles');foreign_step.TOOL_TYPES={};foreign_step.PROTOCOLS={}
    foreign_step.preflight=lambda raw:{'protocol':'foreign-cache'}
    monkeypatch.setitem(sys.modules,'nx_safety',foreign_safety);monkeypatch.setitem(sys.modules,'stepfiles',foreign_step)
    before_path=list(sys.path);before_modules=set(sys.modules)
    value=execute(directory)
    assert value['offline_path'] is not foreign_safety.offline_path
    assert value['preflight'](STEP)['protocol']=='214'
    assert sys.path==before_path and sys.modules['nx_safety'] is foreign_safety and sys.modules['stepfiles'] is foreign_step
    assert not any(name.startswith('_nx_job_helpers_') for name in set(sys.modules)-before_modules)
    assert not (directory/'output').exists()


@pytest.mark.parametrize('filename',['nx_safety.py','stepfiles.py'])
def test_changed_helper_is_rejected_before_any_of_its_code_executes(local_package,monkeypatch,filename):
    directory,_,_=local_package
    monkeypatch.delitem(sys.modules,'nx_safety',raising=False);monkeypatch.delitem(sys.modules,'stepfiles',raising=False)
    path=directory/filename
    path.write_bytes(b'raise AssertionError("Synthetic helper must never execute before hash verification")\n'+path.read_bytes())
    with pytest.raises(RuntimeError,match='Worker package changed'):
        execute(directory)
    assert not (directory/'output').exists()


@pytest.mark.parametrize('change',['missing_manifest','missing_hash','bad_hash','wrong_version'])
def test_bad_manifest_blocks_bootstrap_before_nx_or_output(local_package,change):
    directory,_,_=local_package;path=directory/'job.json'
    if change=='missing_manifest':path.unlink()  # Test-owned file only.
    else:
        value=json.loads(path.read_text(encoding='utf-8'))
        if change=='missing_hash':value['worker_hashes'].pop('stepfiles.py')
        elif change=='bad_hash':value['worker_hashes']['stepfiles.py']='bad-hash'
        else:value['nx_version']='2406'
        path.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(RuntimeError):execute(directory)
    assert not (directory/'output').exists()


def test_matching_timestamp_pyc_is_ignored_in_favour_of_verified_source(local_package,tmp_path):
    directory,_,_=local_package
    source=directory/'nx_safety.py';attributes=source.stat()
    poison=tmp_path/'synthetic-poison.py';poison.write_text('raise AssertionError("Synthetic cached bytecode must not execute")\n',encoding='utf-8')
    cache=Path(importlib.util.cache_from_source(str(source)));cache.parent.mkdir(exist_ok=True)
    py_compile.compile(str(poison),cfile=str(cache),dfile=str(source),doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    raw=cache.read_bytes();cache.write_bytes(raw[:8]+struct.pack('<II',int(attributes.st_mtime),attributes.st_size)+raw[16:])
    value=execute(directory)
    assert value['preflight'](STEP)['protocol']=='214' and not (directory/'output').exists()


def test_all_helper_sources_compile_before_either_executes(local_package,tmp_path):
    directory,_,_=local_package;marker=tmp_path/'synthetic-execution-marker'
    source=directory/'nx_safety.py'
    source.write_bytes(('from pathlib import Path\nPath('+repr(str(marker))+').write_bytes(b"should-not-execute")\n').encode()+source.read_bytes())
    (directory/'stepfiles.py').write_bytes(b'this is not valid python !!!')
    manifest=json.loads((directory/'job.json').read_text(encoding='utf-8'))
    import hashlib
    for name in ('nx_safety.py','stepfiles.py'):manifest['worker_hashes'][name]=hashlib.sha256((directory/name).read_bytes()).hexdigest()
    (directory/'job.json').write_text(json.dumps(manifest),encoding='utf-8')
    before=set(sys.modules)
    with pytest.raises(SyntaxError):execute(directory)
    assert not marker.exists() and not (directory/'output').exists()
    assert not any(name.startswith('_nx_job_helpers_') for name in set(sys.modules)-before)


def test_failed_helper_execution_restores_only_its_temporary_namespace(local_package):
    directory,_,_=local_package;source=directory/'stepfiles.py'
    source.write_bytes(b'raise RuntimeError("Synthetic verified helper failure")\n'+source.read_bytes())
    manifest=json.loads((directory/'job.json').read_text(encoding='utf-8'))
    import hashlib
    manifest['worker_hashes']['stepfiles.py']=hashlib.sha256(source.read_bytes()).hexdigest()
    (directory/'job.json').write_text(json.dumps(manifest),encoding='utf-8')
    before=set(sys.modules);before_path=list(sys.path)
    with pytest.raises(RuntimeError,match='Synthetic verified helper failure'):execute(directory)
    assert sys.path==before_path and not any(name.startswith('_nx_job_helpers_') for name in set(sys.modules)-before)
    assert not (directory/'output').exists()


@pytest.mark.parametrize('field',['MAX_JOB_BYTES','MAX_HELPER_BYTES'])
def test_bootstrap_bounds_are_checked_without_executing_helpers(local_package,monkeypatch,field):
    from nxserver import nxworker
    directory,_,_=local_package
    monkeypatch.setattr(nxworker,field,10)
    with pytest.raises(RuntimeError,match='byte limit'):nxworker._verified_helpers(directory)
    assert not (directory/'output').exists()

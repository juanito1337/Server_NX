"""The actual collector ZIP starts only its verified helpers; never imports NXOpen."""
import io
import os
import runpy
import shutil
import sys
import types
import uuid
import zipfile

import pytest

from nxserver.results import Results
from nxserver.safety import offline_path
from test_geometry import STEP
from test_results import result_pilot

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Actual standalone Windows collector bootstrap, no NX.')


@pytest.fixture
def collector_files(result_pilot):
    _,store,_,_,job=result_pilot
    parent=offline_path('C:/NX_Migration/ToolJobs');parent.mkdir(parents=True,exist_ok=True)
    directory=offline_path(parent/('collector-bootstrap-'+uuid.uuid4().hex[:10]))
    assert not directory.exists();directory.mkdir()
    raw,_=Results(store).collector(job['id'],'tool-metric','CUSTOM','fixture')
    names=('nx_safety','nx_libraries','nx_result_inventory','stepfiles')
    old_modules={name:sys.modules.get(name) for name in names};old_path=list(sys.path)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:archive.extractall(directory)
        yield directory
    finally:
        sys.path[:]=old_path
        for name,module in old_modules.items():
            if module is None:sys.modules.pop(name,None)
            else:sys.modules[name]=module
        assert directory.parent==parent and directory.name.startswith('collector-bootstrap-')
        shutil.rmtree(offline_path(directory))  # Newly created synthetic fixture only.


def execute(directory):return runpy.run_path(str(directory/'nx_result_collect.py'),run_name='isolated_collector_probe')


def test_standalone_collector_loads_all_its_own_dependencies_without_a_step_package(collector_files):
    directory=collector_files
    assert not (directory/'job.json').exists() and not (directory/'nx_step_import.py').exists()
    before=list(sys.path);old=set(sys.modules)
    result=execute(directory)
    assert 'tool-metric' in result['SPECS'] and callable(result['collect'])
    assert result['inventory_part'].__module__.startswith('_nx_job_helpers_')
    assert sys.path==before and not any(name.startswith('_nx_job_helpers_') for name in set(sys.modules)-old)
    assert not (directory/'prepared-result.zip').exists()


def test_foreign_collector_import_caches_are_ignored_and_preserved(collector_files,monkeypatch):
    directory=collector_files
    for name in ('nx_safety','nx_libraries','nx_result_inventory','stepfiles'):
        monkeypatch.setitem(sys.modules,name,types.ModuleType(name))
    old={name:sys.modules[name] for name in ('nx_safety','nx_libraries','nx_result_inventory','stepfiles')}
    result=execute(directory)
    assert 'tool-metric' in result['SPECS']
    assert all(sys.modules[name] is value for name,value in old.items())


@pytest.mark.parametrize('filename',['nx_safety.py','nx_libraries.py','nx_result_inventory.py','stepfiles.py'])
def test_changed_collector_helper_is_rejected_before_execution(collector_files,filename):
    directory=collector_files;path=directory/filename
    path.write_bytes(b'raise AssertionError("Synthetic collector helper must not execute")\n'+path.read_bytes())
    with pytest.raises(RuntimeError,match='Worker package changed'):execute(directory)
    assert not (directory/'prepared-result.zip').exists()

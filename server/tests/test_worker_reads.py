"""Worker preflight rejects byte drift before ANY NX API call; fake STEP only."""
import io
import zipfile
from types import SimpleNamespace

import pytest

from nxserver import nxworker
from nxserver.safety import offline_path,DomainError
from test_geometry import pilot,draft,create_job,HEADERS,STEP
from test_reader_limits import observe


@pytest.fixture
def job_files(pilot,tmp_path,monkeypatch):
    client,store=pilot;resource,asset=draft(client);job=create_job(client,resource,asset).json()
    directory=tmp_path/'job';directory.mkdir()
    raw=client.get('/api/v1/geometry-jobs/'+job['id']+'/package',headers=HEADERS).content
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:archive.extractall(directory)
    monkeypatch.setattr(nxworker,'job_directory',offline_path)
    monkeypatch.setattr(nxworker,'check_environment',lambda *args:pytest.fail('Invalid preflight may not query NX environment.'))
    return directory,job


def test_step_bytes_are_read_with_explicit_limit_and_pinned_hash(job_files,monkeypatch):
    directory,job=job_files;reads=observe(monkeypatch,directory/'input.step')
    monkeypatch.setattr(nxworker,'check_environment',lambda *args:(_ for _ in ()).throw(DomainError('Synthetic stop after preflight')))
    with pytest.raises(DomainError,match='Synthetic stop after preflight'):nxworker.run_job(directory,SimpleNamespace(),None)
    assert reads==[(nxworker.MAX_STEP_BYTES+1,len(STEP))] and not (directory/'output').exists()


@pytest.mark.parametrize('change',['bytes','short','wrong_descriptor'])
def test_invalid_step_read_never_calls_nx_or_creates_output(job_files,monkeypatch,change):
    directory,job=job_files;source=directory/'input.step'
    if change=='bytes':source.write_bytes(STEP+b'\nSynthetic byte drift')
    elif change=='short':observe(monkeypatch,source,short=True)
    else:
        other=directory/'other.step';other.write_bytes(STEP)
        observe(monkeypatch,source,alternate=other)
    with pytest.raises(DomainError):nxworker.run_job(directory,SimpleNamespace(),None)
    assert not (directory/'output').exists()


@pytest.mark.parametrize('field',['MAX_JOB_BYTES','MAX_HELPER_BYTES','MAX_STEP_BYTES'])
def test_worker_input_bounds_are_enforced_before_nx(job_files,monkeypatch,field):
    directory,_=job_files;monkeypatch.setattr(nxworker,field,10)
    with pytest.raises(DomainError):nxworker.run_job(directory,SimpleNamespace(),None)
    assert not (directory/'output').exists()

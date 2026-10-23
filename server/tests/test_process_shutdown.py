"""A job's zero count is not a process-completion barrier."""
from types import SimpleNamespace

import pytest

from tools.nxlab import processes


@pytest.fixture
def owned(monkeypatch):
    events=[]
    monkeypatch.setattr(processes.c,'WinError',lambda *a:OSError('Synthetic Windows API error'),raising=False)
    monkeypatch.setattr(processes.c,'get_last_error',lambda:5,raising=False)
    job=processes.OwnedJob();job.handle=99;job.started=[SimpleNamespace(_handle=303)]
    def terminate(handle,code):events.append(('terminate',handle));return 1
    def wait(handle,timeout):events.append(('wait',handle));return 0
    job.api=SimpleNamespace(TerminateJobObject=terminate,WaitForSingleObject=wait,
        CloseHandle=lambda handle:events.append(('close',handle)))
    values=iter([SimpleNamespace(active=2,total=2),SimpleNamespace(active=2,total=2),SimpleNamespace(active=0,total=2)])
    job.accounting=lambda:next(values);job.active=lambda:0
    def pins():events.append(('pin',None));return [101,202]
    job.member_handles=pins
    return job,events


def test_forced_shutdown_pins_every_member_before_termination_then_waits_handles(owned):
    job,events=owned
    job.__exit__(None,None,None)
    assert events[:2]==[('pin',None),('terminate',99)]
    assert [handle for action,handle in events if action=='wait']==[101,202,303]
    assert events[-3:]==[('close',101),('close',202),('close',99)]


def test_zero_job_count_does_not_certify_an_unsignaled_owned_root(owned):
    job,events=owned;job.accounting=lambda:SimpleNamespace(active=0,total=1)
    job.api.WaitForSingleObject=lambda *a:258
    with pytest.raises(RuntimeError,match='has not terminated'):job.__exit__(None,None,None)
    assert ('terminate',99) not in events and events[-1]==('close',99)


@pytest.mark.parametrize('when',['before_termination','after_termination'])
def test_new_member_during_shutdown_prevents_a_verified_close(owned,when):
    job,events=owned
    values=[SimpleNamespace(active=2,total=2),SimpleNamespace(active=2,total=3 if when=='before_termination' else 2),
        SimpleNamespace(active=0,total=3)]
    job.accounting=iter(values).__next__
    with pytest.raises(RuntimeError,match='membership'):job.__exit__(None,None,None)
    assert (('terminate',99) in events)==(when=='after_termination')
    assert events[-3:]==[('close',101),('close',202),('close',99)]


@pytest.mark.parametrize('outcome',[258,0xffffffff])
def test_process_timeout_or_failed_wait_never_certifies_cleanup(owned,outcome):
    job,events=owned;job.api.WaitForSingleObject=lambda *a:outcome
    with pytest.raises((RuntimeError,OSError)):job.__exit__(None,None,None)
    assert events[-3:]==[('close',101),('close',202),('close',99)]


def test_reused_pid_outside_our_job_is_never_accepted_as_owned(monkeypatch):
    job=processes.OwnedJob();job.handle=99;job.members=lambda:[42];closed=[]
    def belongs(handle,group,result):result._obj.value=False;return 1
    job.api=SimpleNamespace(OpenProcess=lambda *a:123,IsProcessInJob=belongs,CloseHandle=closed.append)
    with pytest.raises(RuntimeError,match='identity changed'):job.member_handles()
    assert closed==[123]


def test_member_query_grows_only_its_job_buffer_and_includes_nested_members():
    job=processes.OwnedJob();job.handle=99;sizes=[]
    def query(handle,kind,output,size,returned):
        assert handle==99 and kind==3
        value=output._obj;sizes.append(len(value.ids))
        value.assigned=33 if len(sizes)==1 else 2
        value.listed=1 if len(sizes)==1 else 2
        value.ids[0]=42;value.ids[1]=43
        return 1
    job.api=SimpleNamespace(QueryInformationJobObject=query)
    assert job.members()==[42,43] and sizes==[32,128]

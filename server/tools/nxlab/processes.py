"""Windows Job Object confines every descendant to the explicitly owned launch.

Create suspended, assign to job, then resume: there is no child-escape window.
Only this job's member list is inspected; every opened handle is checked for
membership before use. No system process list or user session is managed.
"""
import ctypes as c
from ctypes import wintypes as w
import os
import subprocess
import time


class Basic(c.Structure):
    _fields_=[('per_process',c.c_int64),('per_job',c.c_int64),('flags',w.DWORD),
        ('min_working',c.c_size_t),('max_working',c.c_size_t),('active_limit',w.DWORD),
        ('affinity',c.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]


class IO(c.Structure):
    _fields_=[(name,c.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]


class Extended(c.Structure):
    _fields_=[('basic',Basic),('io',IO),('process_memory',c.c_size_t),('job_memory',c.c_size_t),
              ('peak_process_memory',c.c_size_t),('peak_job_memory',c.c_size_t)]


class Accounting(c.Structure):
    _fields_=[(name,c.c_int64) for name in ('user','kernel','period_user','period_kernel')]+[
        ('faults',w.DWORD),('total',w.DWORD),('active',w.DWORD),('terminated',w.DWORD)]


class OwnedJob:
    def __enter__(self):
        if os.name!='nt':raise RuntimeError('NX process ownership requires Windows Job Objects.')
        self.api=c.WinDLL('kernel32',use_last_error=True)
        self.api.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR];self.api.CreateJobObjectW.restype=w.HANDLE
        self.api.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
        self.api.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        self.api.QueryInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.c_void_p]
        self.api.TerminateJobObject.argtypes=[w.HANDLE,w.UINT]
        self.api.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];self.api.OpenProcess.restype=w.HANDLE
        self.api.IsProcessInJob.argtypes=[w.HANDLE,w.HANDLE,c.POINTER(w.BOOL)]
        self.api.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];self.api.WaitForSingleObject.restype=w.DWORD
        self.api.CloseHandle.argtypes=[w.HANDLE]
        self.started=[]
        self.handle=self.api.CreateJobObjectW(None,None)
        if not self.handle:raise c.WinError(c.get_last_error())
        info=Extended();info.basic.flags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle,9,c.byref(info),c.sizeof(info)):
            error=c.WinError(c.get_last_error());self.api.CloseHandle(self.handle);raise error
        return self

    def start(self,command,**options):
        process=subprocess.Popen(command,creationflags=0x00000004,**options) # CREATE_SUSPENDED
        try:
            if not self.api.AssignProcessToJobObject(self.handle,int(process._handle)):
                raise c.WinError(c.get_last_error())
            self.started.append(process)
            resume=c.WinDLL('ntdll').NtResumeProcess;resume.argtypes=[w.HANDLE];resume.restype=c.c_long
            if resume(int(process._handle))!=0:raise RuntimeError('Cannot resume owned NX launcher.')
            return process
        except BaseException:
            process.kill();process.wait(timeout=15);raise

    def active(self):
        return self.accounting().active

    def accounting(self):
        value=Accounting()
        if not self.api.QueryInformationJobObject(self.handle,1,c.byref(value),c.sizeof(value),None):
            raise c.WinError(c.get_last_error())
        return value

    def members(self):
        # JobObjectBasicProcessIdList includes nested jobs. The buffer can grow
        # while children start; bounded retries never enumerate unrelated PIDs.
        for count in (32,128,512,2048,4096):
            class Members(c.Structure):
                _fields_=[('assigned',w.DWORD),('listed',w.DWORD),('ids',c.c_size_t*count)]
            value=Members()
            if self.api.QueryInformationJobObject(self.handle,3,c.byref(value),c.sizeof(value),None):
                if value.assigned<=count and value.listed==value.assigned:
                    return list(value.ids[:value.listed])
            elif c.get_last_error()!=234:raise c.WinError(c.get_last_error()) # ERROR_MORE_DATA
        raise RuntimeError('Owned job membership exceeds the bounded shutdown snapshot.')

    def member_handles(self):
        handles=[]
        try:
            for pid in self.members():
                handle=self.api.OpenProcess(0x00100000|0x1000,False,pid) # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
                if not handle:raise c.WinError(c.get_last_error())
                handles.append(handle)
                belongs=w.BOOL()
                if not self.api.IsProcessInJob(handle,self.handle,c.byref(belongs)):
                    raise c.WinError(c.get_last_error())
                if not belongs.value:raise RuntimeError('Job member identity changed; shutdown cannot be certified.')
            return handles
        except BaseException:
            for handle in handles:self.api.CloseHandle(handle)
            raise

    def __exit__(self,*args):
        handles=[]
        try:
            before=self.accounting()
            if before.active:
                # TerminateJobObject can remove a member from accounting before
                # that process releases handles. Pin exact member generations
                # BEFORE termination, then wait on those process handles.
                handles=self.member_handles()
                pinned=self.accounting()
                if pinned.total!=before.total or pinned.active>len(handles):
                    raise RuntimeError('Owned job membership changed during shutdown; cannot certify closure.')
                if not self.api.TerminateJobObject(self.handle,1):raise c.WinError(c.get_last_error())
            deadline=time.monotonic()+15
            for handle in handles+[int(process._handle) for process in self.started]:
                milliseconds=max(0,int((deadline-time.monotonic())*1000))
                outcome=self.api.WaitForSingleObject(handle,milliseconds)
                if outcome==0xffffffff:raise c.WinError(c.get_last_error())
                if outcome!=0:raise RuntimeError('Owned process has not terminated; do not start another run.')
            if before.active:
                while self.active() and time.monotonic()<deadline:time.sleep(0.05)
                after=self.accounting()
                if after.active or after.total!=pinned.total:
                    raise RuntimeError('Owned job shutdown membership is uncertain; do not start another run.')
        finally:
            for handle in handles:self.api.CloseHandle(handle)
            self.api.CloseHandle(self.handle)

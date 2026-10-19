"""Regular-handle and byte-limit contracts, no real data or network lookups."""
import hashlib
import os
from pathlib import Path

import pytest

from nxserver import bounded_file
from nxserver.safety import DomainError
from test_reader_limits import observe


@pytest.mark.parametrize('raw',[b'x',b'\x00\xffbinary',b'1234567890123456'])
def test_exact_bytes_and_hash_are_preserved_with_one_bounded_read(tmp_path,monkeypatch,raw):
    path=tmp_path/'synthetic.bin';path.write_bytes(raw);reads=observe(monkeypatch,path)
    assert bounded_file.read(path,max_bytes=16,expected_size=len(raw),expected_sha256=hashlib.sha256(raw).hexdigest())==raw
    assert reads==[(17,len(raw))]


@pytest.mark.parametrize('value',[0,-1,True,1.5,'16'])
def test_bad_limit_is_rejected_before_any_lookup(monkeypatch,value):
    monkeypatch.setattr(bounded_file,'offline_path',lambda *args:pytest.fail('Bad limit must not reach a path lookup.'))
    with pytest.raises(DomainError):bounded_file.read('unused',max_bytes=value)


@pytest.mark.parametrize('case',['size','size_type','size_over_limit','sha','sha_type','wrong_sha','oversize','empty','directory','missing'])
def test_bad_binding_and_unreadable_inputs_never_return_partial_bytes(tmp_path,case):
    path=tmp_path/'synthetic.bin';path.write_bytes(b'12345678')
    values={'max_bytes':16,'expected_size':8,'expected_sha256':hashlib.sha256(b'12345678').hexdigest()}
    if case=='size':values['expected_size']=7
    elif case=='size_type':values['expected_size']=True
    elif case=='size_over_limit':values['expected_size']=17
    elif case=='sha':values['expected_sha256']='not-a-sha'
    elif case=='sha_type':values['expected_sha256']=17
    elif case=='wrong_sha':values['expected_sha256']='0'*64
    elif case=='oversize':path.write_bytes(b'x'*17)
    elif case=='empty':path.write_bytes(b'')
    elif case=='directory':path=tmp_path
    elif case=='missing':path=tmp_path/'missing.bin'
    with pytest.raises(DomainError):bounded_file.read(path,**values)


@pytest.mark.parametrize('stage',['open','read','final_stat'])
def test_io_failures_are_structured_conflicts(tmp_path,monkeypatch,stage):
    path=tmp_path/'synthetic.bin';path.write_bytes(b'12345678')
    if stage=='open':
        original=Path.open
        def fail(candidate,*args,**kwargs):
            if candidate==path:raise PermissionError('Synthetic open failure')
            return original(candidate,*args,**kwargs)
        monkeypatch.setattr(Path,'open',fail)
    elif stage=='read':
        observe(monkeypatch,path,before_read=lambda *args:(_ for _ in ()).throw(OSError('Synthetic read failure')))
    else:
        original=Path.stat;calls=[]
        def fail(candidate,*args,**kwargs):
            if candidate==path and kwargs.get('follow_symlinks',True):
                calls.append(True)
                if len(calls)>1:raise FileNotFoundError('Synthetic final-stat failure')
            return original(candidate,*args,**kwargs)
        monkeypatch.setattr(Path,'stat',fail)
    with pytest.raises(DomainError) as error:bounded_file.read(path,max_bytes=16)
    assert error.value.status==409


def test_replaced_path_after_read_is_rejected_even_if_size_and_mtime_match(tmp_path,monkeypatch):
    path=tmp_path/'synthetic.bin';path.write_bytes(b'12345678');before=path.stat();replacement=tmp_path/'replacement.bin'
    replacement.write_bytes(b'87654321');os.utime(replacement,ns=(before.st_atime_ns,before.st_mtime_ns))
    original=Path.stat;calls=[]
    def replaced(candidate,*args,**kwargs):
        if candidate==path and kwargs.get('follow_symlinks',True):
            calls.append(True)
            if len(calls)>1:return original(replacement,*args,**kwargs)
        return original(candidate,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',replaced)
    with pytest.raises(DomainError) as error:bounded_file.read(path,max_bytes=16)
    assert error.value.status==409


@pytest.mark.parametrize('path',['W:/SiemensShare/nx2406/log.syslog','/mnt/w/forbidden.syslog','//server/share/data.xml'])
def test_protected_references_are_rejected_before_file_access(monkeypatch,path):
    monkeypatch.setattr(Path,'open',lambda *args,**kwargs:pytest.fail('Protected path may not be opened.'))
    with pytest.raises(DomainError):bounded_file.read(path,max_bytes=16)

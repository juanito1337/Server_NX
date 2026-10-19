"""Streaming diagnostic file metadata; all files are small synthetic fixtures."""
import hashlib
import os
from pathlib import Path

import pytest

from nxserver.safety import file_fingerprint,DomainError
from test_reader_limits import observe


@pytest.mark.parametrize('raw',[b'',b'x',b'\x00\xffbinary',b'0123456789'*13])
def test_streaming_size_and_hash_match_original_bytes(tmp_path,monkeypatch,raw):
    path=tmp_path/'synthetic.bin';path.write_bytes(raw);reads=observe(monkeypatch,path)
    result=file_fingerprint(path,chunk_bytes=8)
    assert result=={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    assert reads and all(0<amount<=8 for amount,_ in reads)


@pytest.mark.parametrize('change',['short','wrong_descriptor','growth','same_size_bytes','open_failure','missing','directory','oversize'])
def test_fingerprint_never_returns_misleading_metadata(tmp_path,monkeypatch,change):
    path=tmp_path/'synthetic.bin';path.write_bytes(b'1234567890123456')
    if change=='short':observe(monkeypatch,path,short=True)
    elif change=='wrong_descriptor':
        other=tmp_path/'other.bin';other.write_bytes(b'1234567890123456');observe(monkeypatch,path,alternate=other)
    elif change in ('growth','same_size_bytes'):
        changed=[];before=path.stat()
        def mutate(original):
            if changed:return
            changed.append(True)
            with original(path,'ab' if change=='growth' else 'wb') as stream:stream.write(b'x' if change=='growth' else b'abcdefghijklmnop')
            if change=='same_size_bytes':
                # Some fixture filesystems coalesce writes in one timestamp
                # tick. Test an observable metadata change deterministically;
                # the reader never claims an atomic filesystem snapshot.
                os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns+1_000_000_000))
        observe(monkeypatch,path,before_read=mutate)
    elif change=='open_failure':
        original=Path.open
        def fail(candidate,*args,**kwargs):
            if candidate==path:raise PermissionError('Synthetic fingerprint failure')
            return original(candidate,*args,**kwargs)
        monkeypatch.setattr(Path,'open',fail)
    elif change=='missing':path=tmp_path/'missing.bin'
    elif change=='directory':path=tmp_path
    with pytest.raises(DomainError) as error:file_fingerprint(path,chunk_bytes=16,max_bytes=8 if change=='oversize' else 1024)
    assert error.value.status==409


@pytest.mark.parametrize('options',[{'chunk_bytes':0},{'chunk_bytes':True},{'chunk_bytes':'8'},{'max_bytes':-1},{'max_bytes':True}])
def test_invalid_bounds_do_not_touch_the_path(monkeypatch,options):
    import nxserver.safety as module
    monkeypatch.setattr(module,'offline_path',lambda *args:pytest.fail('Invalid limits must fail before lookup.'))
    with pytest.raises(DomainError):file_fingerprint('unused',**options)


def test_protected_output_reference_is_rejected_before_open(monkeypatch):
    monkeypatch.setattr(Path,'open',lambda *args,**kwargs:pytest.fail('Protected reference must not be read.'))
    with pytest.raises(DomainError):file_fingerprint('W:/SiemensShare/nx2406/file.prt')

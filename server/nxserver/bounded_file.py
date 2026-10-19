"""Bounded, read-only bytes tied to a regular open file; no atomic FS claim."""
from .safety import DomainError,offline_path,file_stamp as stamp,read_bounded_file


def read(path,*,max_bytes,expected_size=None,expected_sha256=None,
         changed_message='File changed while reading.',limit_message='File is empty or exceeds the byte limit.'):
    return read_bounded_file(path,max_bytes=max_bytes,expected_size=expected_size,expected_sha256=expected_sha256,
        changed_message=changed_message,limit_message=limit_message,path_guard=offline_path)

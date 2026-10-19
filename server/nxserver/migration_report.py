"""One bounded SHA-bound JSON reader for migration views and SIDT providers."""
import json
import math
import re

from .bounded_file import read as read_bounded_file
from .safety import DomainError

MAX_BYTES=64*1024*1024


def decode(raw):
    def invalid(value):raise ValueError('Nonfinite JSON')
    def finite(value):
        number=float(value)
        if not math.isfinite(number):raise ValueError('Nonfinite JSON number')
        return number
    def unique(pairs):
        value={}
        for name,item in pairs:
            if name in value:raise ValueError('Duplicate JSON field')
            value[name]=item
        return value
    try:
        result=json.loads(raw,parse_constant=invalid,parse_float=finite,object_pairs_hook=unique)
        if not isinstance(result,dict):raise ValueError('Report object required')
        return result
    except (ValueError,TypeError,RecursionError,OverflowError) as error:
        raise DomainError('Stored migration report is not a unique supported JSON object.',409) from error


def read(path,expected_sha256,*,max_bytes=None):
    if not isinstance(expected_sha256,str) or not re.fullmatch('[a-f0-9]{64}',expected_sha256):
        raise DomainError('Stored migration report has no supported SHA-256 binding.',409)
    raw=read_bounded_file(path,max_bytes=MAX_BYTES if max_bytes is None else max_bytes,expected_sha256=expected_sha256,
        changed_message='Stored migration report changed outside the API.',
        limit_message='Stored migration report exceeds its bounded reader.')
    return decode(raw)

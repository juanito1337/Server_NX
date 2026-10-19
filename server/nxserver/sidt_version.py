"""Read the SIDT's explicit NX version declaration as bounded data, never CMD."""
import hashlib
import re

from .safety import DomainError,offline_path,read_bounded_file

RELATIVE='start_apps/env/nx_inst_dir.cmd'
MAX_BYTES=64*1024


def declaration(sidt,expected=None):
    path=offline_path(sidt/RELATIVE)
    try:raw=read_bounded_file(path,max_bytes=MAX_BYTES,limit_message='SIDT version declaration is empty or exceeds its bounded reader.')
    except DomainError as error:
        raise DomainError('Required SIDT version declaration '+RELATIVE+' could not be read: '+str(error),409) from error
    try:text=raw.removeprefix(b'\xef\xbb\xbf').decode('cp1252')
    except UnicodeError as error:raise DomainError('SIDT version declaration is not supported text.',409) from error
    versions=[]
    for line in text.splitlines():
        line=line.lstrip(' \t')
        if not line.strip():continue
        if any(char in line for char in '&|<>^%!'):
            raise DomainError('Unsupported SIDT version declaration; shell expressions are not evaluated.',409)
        if re.match(r'(?i)^@?rem(?:\s|$)',line) or line.startswith('::') or re.fullmatch(r'(?i)@?echo[ \t]+off[ \t]*',line):continue
        match=re.fullmatch(r'(?i)@?set[ \t]+(?:NX_INST_DIR=NX([0-9]{4})|"NX_INST_DIR=NX([0-9]{4})"[ \t]*)',line)
        if not match:raise DomainError('Unsupported SIDT version declaration; no command file is executed.',409)
        versions.append(match.group(1) or match.group(2))
    if len(versions)!=1:raise DomainError('SIDT must contain exactly one explicit NX_INST_DIR version declaration.',409)
    version=versions[0]
    if expected is not None and version!=expected:
        raise DomainError('SIDT declares NX'+version+', expected NX'+expected+'. Select the matching SIDT input.',409)
    return {'contract_version':1,'scope':'sidt_version_declaration','nx_version':version,
        'path':str(path),'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
        'metadata_only':True,'script_executed':False,'provenance_verified':False}


def evidence(value,expected):
    """Qualify stored metadata without dereferencing historical file paths."""
    return (isinstance(value,dict) and type(value.get('contract_version')) is int and value['contract_version']==1
        and value.get('scope')=='sidt_version_declaration' and value.get('nx_version')==expected
        and isinstance(expected,str) and re.fullmatch('[0-9]{4}',expected) is not None
        and isinstance(value.get('path'),str) and 1<=len(value['path'])<=4096
        and type(value.get('size')) is int and 1<=value['size']<=MAX_BYTES
        and isinstance(value.get('sha256'),str) and re.fullmatch('[a-f0-9]{64}',value['sha256']) is not None
        and value.get('metadata_only') is True and value.get('script_executed') is False
        and value.get('provenance_verified') is False)

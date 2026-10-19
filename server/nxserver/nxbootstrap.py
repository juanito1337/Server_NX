"""Stdlib-only bootstrap embedded into both downloadable NX journals."""
import hashlib as _nx_boot_hashlib
import json as _nx_boot_json
import os as _nx_boot_os
import re as _nx_boot_re
import stat as _nx_boot_stat
import sys as _nx_boot_sys
import types as _nx_boot_types
from pathlib import Path as _nx_boot_Path


def _nx_boot_directory(value):
    package=_nx_boot_Path(value).absolute()
    if (_nx_boot_os.name!='nt' or not package.as_posix().casefold().startswith('c:/nx_migration/tooljobs/') or
            '..' in package.parts or len(str(package))>160):
        raise RuntimeError('Use a short dedicated Windows job directory below C:/NX_Migration/ToolJobs.')
    for item in reversed([package]+list(package.parents)):
        attributes=item.lstat()
        if _nx_boot_stat.S_ISLNK(attributes.st_mode) or getattr(attributes,'st_file_attributes',0)&0x400:
            raise RuntimeError('NX job package directory may not contain links or junctions.')
    return package


def _nx_boot_bytes(path,limit):
    try:
        attributes=path.lstat()
        if not _nx_boot_stat.S_ISREG(attributes.st_mode) or getattr(attributes,'st_file_attributes',0)&0x400:
            raise RuntimeError('NX journal inputs must be ordinary local files.')
        if not 1<=attributes.st_size<=limit:raise RuntimeError('NX journal input exceeds its bootstrap byte limit.')
        with path.open('rb') as stream:raw=stream.read(limit+1)
        if len(raw)!=attributes.st_size or len(raw)>limit:raise RuntimeError('NX journal input changed during bootstrap read.')
        return raw
    except OSError as error:raise RuntimeError('NX journal input is missing or unreadable.') from error


def _nx_verified_helper_modules(value,manifest_name,hash_field,files,module_paths,*,max_job_bytes,max_helper_bytes):
    """Verify every byte/compile every helper before executing any helper."""
    package=_nx_boot_directory(value)
    try:configuration=_nx_boot_json.loads(_nx_boot_bytes(package/manifest_name,max_job_bytes).decode('utf-8-sig'))
    except (ValueError,UnicodeError) as error:raise RuntimeError('NX journal manifest is unreadable.') from error
    job=configuration if manifest_name=='job.json' else configuration.get('job') if isinstance(configuration,dict) else None
    hashes=configuration.get(hash_field) if isinstance(configuration,dict) else None
    if (not isinstance(job,dict) or job.get('nx_version')!='2512' or
            manifest_name=='job.json' and job.get('contract_version')!=1 or
            not isinstance(hashes,dict) or set(hashes)!=set(files) or
            any(not isinstance(item,str) or not _nx_boot_re.fullmatch('[a-f0-9]{64}',item) for item in hashes.values())):
        raise RuntimeError('Unsupported or incomplete NX journal manifest.')
    raw={name:_nx_boot_bytes(package/name,max_helper_bytes) for name in files}
    for name,content in raw.items():
        if _nx_boot_hashlib.sha256(content).hexdigest()!=hashes[name]:raise RuntimeError('Worker package changed: '+name)
    compiled={name:compile(raw[name],str(package/name),'exec') for _,name in module_paths}
    namespace='_nx_job_helpers_'+_nx_boot_hashlib.sha256(''.join(hashes[name] for name in files).encode()).hexdigest()[:24]
    names=[namespace]+[namespace+'.'+name for name,_ in module_paths]
    absent=object();previous={name:_nx_boot_sys.modules.get(name,absent) for name in names}
    root=_nx_boot_types.ModuleType(namespace);root.__path__=[];root.__package__=namespace
    loaded={}
    try:
        _nx_boot_sys.modules[namespace]=root
        for logical,filename in module_paths:
            module=_nx_boot_types.ModuleType(namespace+'.'+logical)
            module.__package__=namespace;module.__file__=str(package/filename)
            _nx_boot_sys.modules[module.__name__]=module
            exec(compiled[filename],module.__dict__);loaded[logical]=module
        return loaded
    finally:
        for name,old in previous.items():
            if old is absent:_nx_boot_sys.modules.pop(name,None)
            else:_nx_boot_sys.modules[name]=old


def journal_source(filename):
    root=_nx_boot_Path(__file__).parent
    if filename not in ('nxworker.py','resultworker.py'):raise ValueError('Unknown NX journal source.')
    return (root/'nxbootstrap.py').read_bytes()+b'\n'+(root/filename).read_bytes()

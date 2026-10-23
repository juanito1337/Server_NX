"""Validate text before filesystem operations; Windows isolation is a separate gate."""
import ctypes
import json
import os
import re
import subprocess
from pathlib import Path

from nxserver.safety import DomainError, offline_path, disjoint


class Blocked(RuntimeError):
    pass


def local(value):
    text=str(value).replace('\\','/')
    if text!=text.strip() or not text or '\x00' in text or '..' in text.split('/'):
        raise DomainError('An ordinary absolute local path is required.')
    if os.name=='nt' and not re.match(r'(?i)^c:/',text):
        raise DomainError('NX lab accepts only absolute C: paths; no other drive is inspected.')
    if os.name!='nt' and not text.startswith('/'):
        raise DomainError('An absolute path is required.')
    return offline_path(value)


def writable_lab_root(value,server,*,development=False):
    root=local(value)
    protected=[server/'nxserver',server/'tools',server/'tests']
    protected += [server/'test-results'] if development else [server/'data']
    if os.name=='nt':protected += [Path('C:/Siemens'),Path('C:/Windows'),Path('C:/Program Files'),Path('C:/Program Files (x86)')]
    for path in protected:disjoint(root,local(path))
    return root


def account_sid():
    if os.name!='nt': raise Blocked('NX requires Windows.')
    result=subprocess.run([r'C:\Windows\System32\whoami.exe','/user','/fo','csv','/nh'],
                          capture_output=True,text=True,check=True)
    match=re.search(r'S-1-5-[0-9-]+',result.stdout)
    if not match: raise Blocked('Cannot establish Windows account SID.')
    return match[0]


def isolation(policy_file, developer_sid):
    policy=json.loads(local(policy_file).read_text(encoding='utf-8'))
    sid=account_sid()
    if (policy.get('schema_version')!=1 or sid==developer_sid or
        policy.get('test_account_sid')!=sid or policy.get('developer_sid')!=developer_sid or
        policy.get('production_file_access_denied') is not True or
        policy.get('dedicated_account') is not True or
        not isinstance(policy.get('reviewed_by'),str) or not policy['reviewed_by'].strip() or
        not isinstance(policy.get('isolation_evidence'),str) or len(policy['isolation_evidence'].strip())<40):
        raise Blocked('Dedicated account plus administrator-reviewed production file-access denial required. No NX started.')
    # This is an administrator attestation, not a probe of W: or proof by drive absence.
    return {'account_sid':sid,'isolation_attested':True,'reviewed_by':policy['reviewed_by']}


def executable_version(path):
    path=local(path)
    if os.name!='nt': raise Blocked('Windows version metadata unavailable.')
    api=ctypes.windll.version
    unused=ctypes.c_ulong()
    size=api.GetFileVersionInfoSizeW(str(path),ctypes.byref(unused))
    if not size: raise Blocked('NX executable has no version metadata.')
    buffer=ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path),0,size,buffer): raise Blocked('NX version metadata unreadable.')
    pointer=ctypes.c_void_p();length=ctypes.c_uint()
    if not api.VerQueryValueW(buffer,'\\',ctypes.byref(pointer),ctypes.byref(length)) or length.value<52:
        raise Blocked('NX fixed version metadata missing.')
    values=ctypes.cast(pointer,ctypes.POINTER(ctypes.c_uint32*13)).contents
    if values[0]!=0xFEEF04BD: raise Blocked('Invalid executable version metadata.')
    return '.'.join(map(str,(values[2]>>16,values[2]&65535,values[3]>>16,values[3]&65535)))


def nx_environment(installation, runtime, inherited=None):
    inherited=os.environ if inherited is None else inherited
    installation,runtime=local(installation),local(runtime)
    result={}
    for name in ('SystemRoot','WINDIR','COMSPEC'):
        value=inherited.get(name)
        if value: result[name]=str(local(value))
    for name in ('SPLM_LICENSE_SERVER','UGS_LICENSE_SERVER'):
        value=inherited.get(name)
        if value:
            if not re.fullmatch(r'[0-9]+@[A-Za-z0-9_.-]+(?:;[0-9]+@[A-Za-z0-9_.-]+)*',value):
                raise Blocked('License setting must be port@host, never a file path.')
            result[name]=value
    for name,folder in [('TEMP','temp'),('TMP','temp'),('UGII_TMP_DIR','temp'),
                        ('UGII_USER_DIR','user'),('USERPROFILE','user'),('APPDATA','user/appdata'),
                        ('LOCALAPPDATA','user/local'),('UGII_CAM_RESOURCE_DIR','cam')]:
        path=local(runtime/folder);path.mkdir(parents=True,exist_ok=True);result[name]=str(path)
    custom=local(runtime/'custom_dirs.dat')
    custom.write_text('# isolated NX lab: no custom directories\n',encoding='utf-8')
    result.update(UGII_BASE_DIR=str(installation),UGII_ROOT_DIR=str(installation/'UGII'),
                  UGII_CUSTOM_DIRECTORY_FILE=str(custom),
                  PATH=str(installation/'NXBIN')+os.pathsep+r'C:\Windows\System32',
                  PYTHONIOENCODING='utf-8')
    return result

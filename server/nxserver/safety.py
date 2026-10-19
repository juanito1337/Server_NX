import ctypes
import hashlib
import os
import re
import stat
from pathlib import Path


class DomainError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def file_stamp(value):
    # CPython on Windows adds extension-derived execute bits to path stat(),
    # not fstat(). They are not ACL rights. Preserve real mode checks on POSIX
    # and all remaining identity/size/timestamp fields on both platforms.
    mode=value.st_mode&~0o111 if os.name=='nt' else value.st_mode
    return (mode,value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)


def read_bounded_file(path,*,max_bytes,expected_size=None,expected_sha256=None,path_guard=None,
                      changed_message='File changed while reading.',limit_message='File is empty or exceeds the byte limit.'):
    """Shared server/journal byte reader; external writers are not locked."""
    if type(max_bytes) is not int or max_bytes<1:raise DomainError('Invalid file byte limit.')
    if expected_size is not None and (type(expected_size) is not int or not 1<=expected_size<=max_bytes):
        raise DomainError(changed_message,409)
    if expected_sha256 is not None and (not isinstance(expected_sha256,str) or not re.fullmatch('[a-f0-9]{64}',expected_sha256)):
        raise DomainError(changed_message,409)
    path=(path_guard or offline_path)(path)
    try:
        before=path.stat()
        if not stat.S_ISREG(before.st_mode):raise DomainError('Only ordinary files can be read.',409)
        if not 1<=before.st_size<=max_bytes:raise DomainError(limit_message)
        if expected_size is not None and before.st_size!=expected_size:raise DomainError(changed_message,409)
        with path.open('rb') as stream:
            opened=os.fstat(stream.fileno())
            if file_stamp(opened)!=file_stamp(before):raise DomainError(changed_message,409)
            raw=stream.read(max_bytes+1)
            finished=os.fstat(stream.fileno())
        after=path.stat()
    except OSError as error:
        raise DomainError('File disappeared or became unreadable while reading.',409) from error
    if (file_stamp(before)!=file_stamp(finished) or file_stamp(before)!=file_stamp(after) or
            not isinstance(raw,bytes) or len(raw)!=before.st_size or len(raw)>max_bytes):
        raise DomainError(changed_message,409)
    if expected_sha256 is not None and hashlib.sha256(raw).hexdigest()!=expected_sha256:
        raise DomainError(changed_message,409)
    return raw


def file_fingerprint(path,*,chunk_bytes=1024*1024,max_bytes=1<<40):
    """Stream size/SHA from one stable regular handle, including empty logs."""
    if type(chunk_bytes) is not int or chunk_bytes<1 or type(max_bytes) is not int or max_bytes<0:
        raise DomainError('Invalid streaming fingerprint bounds.')
    path=offline_path(path)
    try:
        before=path.stat()
        if not stat.S_ISREG(before.st_mode) or not 0<=before.st_size<=max_bytes:
            raise DomainError('Output is not an ordinary file within fingerprint bounds.',409)
        digest=hashlib.sha256();total=0
        with path.open('rb') as stream:
            if file_stamp(os.fstat(stream.fileno()))!=file_stamp(before):raise DomainError('Output file changed before hashing.',409)
            while True:
                raw=stream.read(min(chunk_bytes,before.st_size-total+1))
                if not isinstance(raw,bytes):raise DomainError('Output hash reader returned non-binary data.',409)
                if not raw:break
                total+=len(raw)
                if total>before.st_size:raise DomainError('Output grew while hashing.',409)
                digest.update(raw)
            finished=os.fstat(stream.fileno())
        after=path.stat()
    except OSError as error:raise DomainError('Output disappeared or became unreadable while hashing.',409) from error
    if total!=before.st_size or file_stamp(finished)!=file_stamp(before) or file_stamp(after)!=file_stamp(before):
        raise DomainError('Output file changed or was incompletely read while hashing.',409)
    return {'size':total,'sha256':digest.hexdigest()}


def asset_filename(value):
    """Preserve ordinary component basenames; never rename binary PRT references."""
    if (not isinstance(value,str) or not 1<=len(value)<=120 or value in ('.','..') or
        value.endswith((' ','.')) or any(c in value for c in '/\\:*?"<>|') or
        any(ord(c)<32 for c in value) or
        re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',value)):
        raise DomainError('Invalid or reserved asset filename.')
    return value


def offline_path(value):
    """Reject network/protected paths before even performing filesystem lookups."""
    text = str(value).replace('\\', '/')
    if re.match(r'(?i)^w:', text) or text.startswith('//') or re.search(
        r'(?i)(^|/)siemensshare/nx2406($|/)', text
    ):
        raise DomainError('Protected/network paths are not allowed.')
    if text.lower().startswith('/mnt/w'):
        raise DomainError('The production drive is not allowed.')
    if os.name != 'nt' and re.match(r'^[A-Za-z]:', text):
        raise DomainError('Windows filesystem paths cannot be used directly on Linux; configure explicit local mount paths.')
    path = Path(value).absolute()
    if os.name == 'nt' and ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(path.anchor)) == 4:
        raise DomainError('Mapped network drives are not allowed.')
    # Check ancestors first, before a lookup could follow an intermediate link.
    for item in reversed([path] + list(path.parents)):
        try:
            attributes = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(attributes.st_mode) or getattr(attributes, 'st_file_attributes', 0) & 0x400:
            raise DomainError('Symbolic links and Windows junctions/reparse points are not allowed.')
    return path


def disjoint(first, second):
    a, b = str(first).casefold(), str(second).casefold()
    sep = os.sep
    if a == b or a.startswith(b.rstrip(sep) + sep) or b.startswith(a.rstrip(sep) + sep):
        raise DomainError('Managed storage and source folders must not overlap.')


def tree_files(root):
    root = offline_path(root)
    if not root.is_dir():
        raise DomainError('Source folder does not exist.')
    files = []
    seen = set()
    def unreadable(error):
        raise DomainError('A source directory cannot be enumerated; import aborted.') from error
    for directory, folders, names in os.walk(root, followlinks=False, onerror=unreadable):
        for name in folders + names:
            offline_path(Path(directory) / name)
        for name in names:
            path = Path(directory) / name
            if not path.is_file():
                raise DomainError('Only ordinary files can be imported.')
            relative = path.relative_to(root).as_posix()
            if relative.casefold() in seen:
                raise DomainError('Case-colliding filenames are not portable to NX on Windows.')
            seen.add(relative.casefold())
            files.append((relative, path))
    return sorted(files)


def export_path_casing(paths):
    """Pure-string Windows/Linux plan gate, including shared directory prefixes."""
    known={}
    for path in paths:
        parts=path.split('/')
        for index in range(1,len(parts)+1):
            relative='/'.join(parts[:index]);kind='file' if index==len(parts) else 'directory';key=relative.casefold()
            previous=known.get(key)
            if previous is not None and previous!=(relative,kind):
                raise DomainError('Export path casing or file/directory collision is not portable: '+relative,409)
            known[key]=(relative,kind)

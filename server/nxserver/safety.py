import ctypes
import os
import re
import stat
from pathlib import Path


class DomainError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


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

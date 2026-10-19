"""Relocate only code-owned private artifacts; old paths are never dereferenced."""
import re

from .migration import portable
from .safety import DomainError, offline_path


def private_artifact(store, value, relative):
    portable(relative)
    text=str(value).replace('\\','/').rstrip('/')
    if (text.startswith('//') or re.match(r'(?i)^w:',text) or text.lower().startswith('/mnt/w')
            or re.search(r'(?i)(^|/)siemensshare(/|$)',text)):
        raise DomainError('Protected/network artifact references are not allowed.')
    for root in [str(store.root),*getattr(store,'recovery_origins',[])]:
        expected=str(root).replace('\\','/').rstrip('/')+'/'+relative
        if text==expected or (re.match(r'^[A-Za-z]:',expected) and text.casefold()==expected.casefold()):
            return offline_path(store.root/relative)
    raise DomainError('Stored artifact reference differs from its job-owned path.',409)


def extraction_paths(store, report, identifier,*,expected_version=None):
    if (not isinstance(report,dict) or not isinstance(report.get('version'),str)
            or not re.fullmatch('[0-9]{4}',report['version'])
            or expected_version is not None and report['version']!=expected_version
            or any(not isinstance(report.get(name),str) or not 1<=len(report[name])<=4096 for name in ('folder','sidt_root'))
            or not isinstance(report.get('manifest'),list)
            or any(not isinstance(item,dict) or set(item)!={'path','size','sha256'}
                or not isinstance(item['path'],str) or type(item['size']) is not int or item['size']<0
                or not isinstance(item['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',item['sha256'])
                for item in report['manifest'])
            or report.get('installer_executed') is not False or report.get('automatic_activation') is not False):
        raise DomainError('Stored extraction report metadata is incomplete or contradicts its requested version.',409)
    relative='upgrades/'+identifier+'/extraction/files'
    raw=private_artifact(store,report['folder'],relative)
    original=str(report['folder']).replace('\\','/').rstrip('/')
    sidt=str(report['sidt_root']).replace('\\','/')
    if not sidt.casefold().startswith(original.casefold()+'/'):
        raise DomainError('SIDT reference is outside the job-owned extraction.',409)
    suffix=sidt[len(original)+1:];portable(suffix)
    return raw,offline_path(raw/suffix)

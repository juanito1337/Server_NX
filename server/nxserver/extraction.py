"""Pinned external archive reader. Installer binaries/scripts are never executed."""
import hashlib
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .migration import after_install_profile, portable
from .safety import DomainError, offline_path, tree_files
from .store import sha256

SUPPORTED = {'6.3.0','6.7.0'}
MAX_INSTALLER = 64*1024*1024
MAX_OUTPUT = 2*1024*1024*1024
MAX_ENTRIES = 20000
MAX_READER_OUTPUT = 4*1024*1024


def installer_info(store,version):
    configured=store.installers.get(version)
    if not configured:raise DomainError('No administrator-configured installer for NX'+version+'.')
    path=offline_path(configured['path'])
    if not path.is_file() or path.suffix.lower()!='.exe' or path.stat().st_size>MAX_INSTALLER:
        raise DomainError('Installer must be a local EXE of at most 64 MB.')
    try:
        with path.open('rb') as stream:raw=stream.read(MAX_INSTALLER+1)
    except OSError as error:raise DomainError('Configured installer is not readable.',409) from error
    if len(raw)>MAX_INSTALLER:raise DomainError('Installer exceeded its 64 MB bound while being read.',409)
    digest=hashlib.sha256(raw).hexdigest()
    if digest!=configured['sha256']:raise DomainError('Installer differs from its administrator-pinned SHA-256.',409)
    headers={item.decode('ascii') for item in re.findall(rb'Inno Setup Setup Data \((\d+\.\d+\.\d+)\)',raw)}
    if len(headers)!=1 or not headers<=SUPPORTED:raise DomainError('Unsupported/ambiguous Inno Setup format. No nearest-version fallback.')
    return {'version':version,'filename':path.name,'path':str(path),'sha256':digest,'size':len(raw),'format':next(iter(headers))}


def tool_info(store):
    config=store.extractor or {}
    if config.get('kind')!='innounp' or os.name!='nt':
        raise DomainError('The approved static reader currently requires a configured Windows worker. No Wine/installer execution fallback.',409)
    path=offline_path(config.get('path',''))
    if path.name.lower()!='innounp.exe' or not path.is_file() or sha256(path)!=config.get('sha256'):
        raise DomainError('Configured archive reader is missing or differs from its pinned SHA-256.',409)
    return {'kind':'innounp','path':str(path),'sha256':config['sha256']}


def listing(text):
    entries=[];seen=set()
    for line in text.splitlines():
        match=re.match(r'^\s*(\d+)\s+\d{4}[-.]\d\d[-.]\d\d\s+\d\d:\d\d(?::\d\d)?\s+(.+?)\s*$',line)
        if not match:continue
        size=int(match[1]);raw=match[2].replace('\\','/')
        portable(raw)
        if not raw.startswith('{app}/') and raw!='install_script.iss':
            raise DomainError('Installer contains non-application paths; explicit adapter required.')
        key=raw.casefold()
        if key in seen:raise DomainError('Duplicate/case-colliding installer paths are not automatically overwritten.')
        seen.add(key);entries.append({'path':raw,'size':size})
    if not entries or len(entries)>MAX_ENTRIES or sum(entry['size'] for entry in entries)>MAX_OUTPUT:
        raise DomainError('Installer listing is empty, too large or unsupported.')
    return entries


def run_reader(store,tool,arguments,child=lambda *args:None):
    # Explicit whitelist: API tokens, credential filenames and provider keys
    # are never inherited. CWD is the trusted reader directory, not unpacked code.
    env={key:value for key,value in os.environ.items() if key.upper() in
         {'SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','LANG','LC_ALL'}}
    env['TEMP']=str(store.root);env['TMP']=str(store.root)
    command=[str(offline_path(tool['path'])),*arguments]
    if sha256(offline_path(tool['path']))!=tool['sha256']:raise DomainError('Archive reader changed before use.',409)
    # Disk-backed capture avoids accumulating unbounded PIPE output in memory.
    # While running, stop only our owned reader on deadline/output overflow.
    with tempfile.TemporaryFile(dir=store.root) as out,tempfile.TemporaryFile(dir=store.root) as err:
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=out,stderr=err,
            cwd=str(Path(tool['path']).parent),env=env,shell=False)
        try:
            child(process.pid);deadline=time.monotonic()+300
            while True:
                if os.fstat(out.fileno()).st_size+os.fstat(err.fileno()).st_size>MAX_READER_OUTPUT:
                    raise DomainError('Archive reader output exceeded the allowed bound.',409)
                if time.monotonic()>deadline:
                    raise DomainError('Static archive reader timed out; partial evidence retained.',409)
                try:process.wait(timeout=0.2);break
                except subprocess.TimeoutExpired:continue
            out.seek(0);err.seek(0)
            stdout=out.read(MAX_READER_OUTPUT+1);stderr=err.read(MAX_READER_OUTPUT+1)
            if len(stdout)+len(stderr)>MAX_READER_OUTPUT:raise DomainError('Archive reader output exceeded the allowed bound.',409)
        finally:
            if process.poll() is None:process.kill();process.wait()
            child(None)
    try:output=stdout.decode('utf-8-sig');errors=stderr.decode('utf-8-sig')
    except UnicodeDecodeError as error:raise DomainError('Reader did not return the required UTF-8 output.',409) from error
    if process.returncode!=0:raise DomainError('Static extraction failed ('+str(process.returncode)+'): '+(errors or output)[-1500:],409)
    return output


def extract(store,version,directory,progress=lambda *args:None,child=lambda *args:None):
    info=installer_info(store,version);tool=tool_info(store)
    archive=offline_path(info['path']);directory=offline_path(directory)
    directory.mkdir(parents=True,exist_ok=False)
    copied=offline_path(directory/'input.exe')
    # Snapshot prevents an archive mutation between validation and reader use.
    with archive.open('rb') as source:content=source.read(MAX_INSTALLER+1)
    if len(content)!=info['size'] or hashlib.sha256(content).hexdigest()!=info['sha256']:
        raise DomainError('Installer changed before snapshot.',409)
    copied.write_bytes(content)
    progress('scanning',10,'Statische Dateiliste und Pfade pruefen')
    common=['-a','-b','-n','-u','-o']
    text=run_reader(store,tool,['-v',*common,str(copied)],child)
    offline_path(directory/'listing.txt').write_bytes(text.encode('utf-8'))
    entries=listing(text)
    progress('scanning',25,'Archivintegritaet pruefen; keine Setup-Ausfuehrung')
    run_reader(store,tool,['-t','-q',*common,str(copied)],child)
    raw=offline_path(directory/'files')
    progress('extracting',40,'Dateien in neuen isolierten Ordner entpacken')
    run_reader(store,tool,['-x','-q',*common,'-d'+str(raw),str(copied)],child)
    expected={entry['path'].casefold():entry['size'] for entry in entries}
    observed=dict(tree_files(raw))
    for relative,path in observed.items():
        portable(relative)
        if relative.casefold() not in expected or path.stat().st_size!=expected[relative.casefold()]:
            raise DomainError('Extracted file list/size differs from the reviewed archive listing.',409)
    if set(expected)!={relative.casefold() for relative in observed}:
        raise DomainError('Extraction is incomplete; no input provider activated.',409)
    candidates=[path.parent.parent.parent for relative,path in observed.items()
                if relative.casefold().endswith('/applications/nx/after_install.bat')]
    if len(candidates)!=1 or candidates[0].name.casefold()!='nx'+version:
        raise DomainError('Extracted NX root does not uniquely match the selected version.',409)
    sidt=offline_path(candidates[0]);profile=after_install_profile(sidt,version)
    if sha256(archive)!=info['sha256']:raise DomainError('Original installer changed during extraction.',409)
    progress('verifying',90,'SIDT-Profil und unveraenderlichen SHA-Dateistand binden')
    manifest=store.manifest(raw)
    return {'version':version,'installer':info,'tool':tool,'sidt_root':str(sidt),'folder':str(raw),
            'listing_sha256':sha256(offline_path(directory/'listing.txt')),
            'file_count':len(manifest),'bytes':sum(item['size'] for item in manifest),
            'manifest':manifest,'profile':profile,'installer_executed':False,'automatic_activation':False}

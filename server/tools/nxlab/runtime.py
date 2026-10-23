"""Owned subprocesses, real loopback HTTP, and isolated backup/recovery checks."""
import base64
import contextlib
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

from .guard import Blocked, local

SERVER=local(__file__).parents[2]


def unavailable_license(text):
    return bool(re.search(r'(?i)(?:license|licence|lizenz)[^\r\n]{0,160}(?:not available|unavailable|expired|denied|checkout failed|nicht verf.gbar|abgelaufen)|no [^\r\n]{0,60}(?:license|licence)',text))


class Server:
    def __init__(self, directory, data=None, *, standards=None, sources=None):
        self.directory=local(directory);self.directory.mkdir(parents=True,exist_ok=False)
        self.data=local(data or self.directory/'data')
        self.token=secrets.token_urlsafe(40)
        self.process=None;self.log=None
        self.standards={key:str(local(value)) for key,value in (standards or {}).items()}
        self.sources={key:str(local(value)) for key,value in (sources or {}).items()}
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __enter__(self):
        settings=local(self.directory/'settings.local.json')
        settings.write_text(json.dumps({'data_dir':str(self.data),'sources':self.sources,'standards':self.standards,
                                       'source_roots':{},'target_roots':{},'logs':{}}),encoding='utf-8')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.base='http://127.0.0.1:'+str(port)
        environment={key:value for key,value in os.environ.items()
                     if key.upper() in ('SYSTEMROOT','WINDIR','COMSPEC')}
        environment.update(NX_ADMIN_TOKEN=self.token,PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1',
                           PATH=str(local(sys.executable).parent)+os.pathsep+r'C:\Windows\System32',
                           TEMP=str(self.directory),TMP=str(self.directory))
        self.log=local(self.directory/'server.log').open('wb')
        self.process=subprocess.Popen([str(local(sys.executable)),'-m','nxserver','--settings',str(settings),
                                       '--port',str(port)],cwd=SERVER,env=environment,
                                      stdout=self.log,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if self.process.poll() is not None:raise RuntimeError('Owned server exited during startup; inspect local server.log.')
                try:
                    self.request('/health')
                    self.request('/api/v1/resources') # Random token proves this is our instance.
                    return self
                except (OSError,urllib.error.URLError):time.sleep(0.1)
            raise RuntimeError('Owned server readiness timeout.')
        except BaseException:
            self.__exit__(None,None,None);raise

    def request(self,path,method='GET',value=None,binary=False,*,raw=None):
        if not path.startswith('/') or path.startswith('//'):raise ValueError('Relative API path required.')
        headers={'Authorization':'Bearer '+self.token}
        data=None
        if value is not None:
            data=json.dumps(value).encode();headers['Content-Type']='application/json'
        if raw is not None:
            if value is not None or not isinstance(raw,bytes):raise ValueError('Raw ZIP bytes require a separate request body.')
            data=raw;headers['Content-Type']='application/zip'
        request=urllib.request.Request(self.base+path,data=data,method=method,headers=headers)
        with self.opener.open(request,timeout=30) as response:content=response.read()
        return content if binary else json.loads(content)

    def __exit__(self,*args):
        try:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.process.kill();self.process.wait(timeout=15)
        finally:
            if self.log:self.log.close()


def job(server,response):
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        value=server.request('/api/v1/backup-jobs/'+response['id'])
        if value['status'] in ('completed','failed','cancelled'):
            if value['status']!='completed':raise RuntimeError('Backup/recovery job failed: '+value.get('message',''))
            return server.request('/api/v1/backup-jobs/'+response['id']+'/report')
        time.sleep(0.1)
    raise RuntimeError('Backup/recovery did not complete; outputs retained.')


def seed(server,step,version='2406',tool_type='milling_square',name='NX lab reference'):
    asset=server.request('/api/v1/assets','POST',{'filename':'reference.step',
        'content_base64':base64.b64encode(step).decode()})
    resource=server.request('/api/v1/resources','POST',{'kind':'tool','name':name,
        'cam_status':'draft','cam':{},'asset_ids':[asset['id']]})
    geometry=server.request('/api/v1/geometry-jobs','POST',{'resource_id':resource['id'],
        'expected_revision':resource['revision'],'asset_id':asset['id'],'tool_type':tool_type,'nx_version':version})
    package=server.request('/api/v1/geometry-jobs/'+geometry['id']+'/package',binary=True)
    if hashlib.sha256(package).hexdigest()!=geometry['package_sha256']:raise AssertionError('Downloaded package hash differs.')
    return resource,asset,geometry,package


def persistence(directory,step):
    directory=local(directory);directory.mkdir(parents=True,exist_ok=False)
    from .packet_contract import source,adopt_drafts
    from nxserver.libraries import NativeTable,SPECS
    sources={'fixture':source(directory/'synthetic-source')}
    with Server(directory/'first',sources=sources) as server:
        release=server.request('/api/v1/releases','POST',{'source':'fixture','nx_version':'2406','name':'Synthetic transport baseline'})
        resource,asset,geometry,package=seed(server,step)
        prepared,resources=adopt_drafts(server,release,geometry)
        histories={value['id']:server.request('/api/v1/resources/'+value['id']+'/history') for value in resources}
        assets={identifier:server.request('/api/v1/assets/'+identifier,binary=True)
                for value in resources for identifier in value['asset_ids']}
        preview=server.request('/api/v1/releases/'+release['id']+'/derive-preview','POST',{})
        if not preview['can_export']:raise AssertionError('Unchanged baseline export failed.')
        exported=server.request('/api/v1/releases/'+release['id']+'/derive','POST',
            {'name':'Drafts excluded from synthetic export','preview_hash':preview['preview_hash']})
        # No test helper sets functional checks or approval. The draft must not
        # silently enter the exported tool library after a successful upload.
        table=NativeTable.read(local(exported['folder'])/SPECS['tool-metric']['path'])
        if set(table.groups())!={'STD'}:raise AssertionError('An unqualified imported draft entered an export.')
        data=server.data
        snapshot=job(server,server.request('/api/v1/backups','POST',{}))
        response=server.request('/api/v1/backups/'+snapshot['id']+'/restore-preview','POST',{'name':'NXLabRestore'})
        preview_id=response['id'];preview=job(server,response)
        if not preview['can_restore']:raise AssertionError('Fresh recovery preview blocked.')
        restored=job(server,server.request('/api/v1/backups/'+snapshot['id']+'/restore','POST',
            {'name':'NXLabRestore','preview_id':preview_id,'preview_hash':preview['preview_hash'],'accept_unsigned':True}))
        restored_data=local(restored['target_folder'])
    for name,root in [('restart',data),('restored',restored_data)]:
        with Server(directory/name,root) as server:
            if server.request('/api/v1/resources')!=resources:raise AssertionError(name+': resources/revisions changed.')
            if server.request('/api/v1/geometry-results/'+prepared['id'])!=prepared:
                raise AssertionError(name+': result archive/provenance or adoption receipt changed.')
            for identifier,history in histories.items():
                if server.request('/api/v1/resources/'+identifier+'/history')!=history:
                    raise AssertionError(name+': immutable revision history changed.')
            for identifier,content in assets.items():
                if server.request('/api/v1/assets/'+identifier,binary=True)!=content:
                    raise AssertionError(name+': a model attachment changed.')
            restored_release=server.request('/api/v1/releases/'+exported['id'])
            if restored_release['manifest']!=exported['manifest'] or restored_release['catalog']!=exported['catalog']:
                raise AssertionError(name+': exported release manifest or catalog changed.')
            root=local(restored_release['folder'])
            for item in restored_release['manifest']:
                if hashlib.sha256(local(root/item['path']).read_bytes()).hexdigest()!=item['sha256']:
                    raise AssertionError(name+': exported release bytes changed.')
            result=server.request('/api/v1/geometry-jobs/'+geometry['id'])
            if result['nx_version']!='2406' or result['resource_revision']!=resource['revision']:
                raise AssertionError(name+': job binding changed.')
            if server.request('/api/v1/geometry-jobs/'+geometry['id']+'/package',binary=True)!=package:
                raise AssertionError(name+': immutable journal package changed.')
            if server.request('/api/v1/assets/'+asset['id'],binary=True)!=step:
                raise AssertionError(name+': input attachment changed.')
    return {'status':'passed','real_http':True,'backup_restore':True,'server_restart':True,
            'nx_execution':False,'synthetic_prepared_result':True,'unqualified_drafts_excluded_from_export':True,
            'resources_preserved':len(resources),'attachments_preserved':len(assets),'histories_preserved':len(histories),
            'resource_id':resource['id'],'job_id':geometry['id'],'result_id':prepared['id']}


def run_owned(command,*,directory,environment,timeout=900):
    """Popen handle is owned here; never search, kill by name, or kill a PID tree."""
    directory=local(directory)
    from .processes import OwnedJob
    with local(directory/'nx.log').open('wb') as log, OwnedJob() as owned:
        process=owned.start(command,cwd=directory,env=environment,stdout=log,stderr=subprocess.STDOUT)
        try:
            code=process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError('NX timeout; owned Job Object terminates and verifies all descendants. Run failed.')
        if owned.active():raise RuntimeError('NX launcher exited with descendants still running; owned job is terminated and run fails.')
    if code:
        with local(directory/'nx.log').open('rb') as stream:text=stream.read(1024*1024).decode('utf-8',errors='replace')
        if unavailable_license(text):raise Blocked('NX license unavailable; inspect local nx.log. No qualification achieved.')
        raise RuntimeError('NX journal process failed; inspect nx.log.')

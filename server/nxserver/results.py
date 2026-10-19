"""Revision-bound prepared NX packages. Bytes/inventory are NOT CAM qualification."""
import copy
import hashlib
import io
import json
import re
import uuid
import zipfile
from pathlib import Path,PurePosixPath
from typing import Literal
from contextlib import contextmanager

from pydantic import BaseModel,ConfigDict,Field,ValidationError

from .diagnostics import Part,Component,File,relative_output
from .geometry import GeometryJobs
from .libraries import (SPECS,NativeTable,cam_table,native_cam,project_records,records_hash,
                        reference_targets,reference_report,spec_for,projection_block)
from .safety import DomainError,offline_path,asset_filename,file_fingerprint
from .store import canonical,digest,now,sha256,resource_identity

MAX_ARCHIVE=64*1024*1024
MAX_TOTAL=256*1024*1024
MAX_FILE=20*1024*1024
MAX_MANIFEST=2*1024*1024
ALLOWED_KINDS={'tool','tool_assembly','holder','shank','trackpoint'}


def normalize_column_values(value):
    """Only explicit, exactly representable DAT text; no implicit defaults."""
    if value is None:return {}
    if not isinstance(value,dict):raise DomainError('Column values must be library/object/FORMAT/field mappings.')
    count=0
    def visit(node,depth):
        nonlocal count
        if not isinstance(node,dict) or (depth and not node):raise DomainError('Column values require nonempty nested mappings.')
        for key,item in node.items():
            if not isinstance(key,str) or not key:raise DomainError('Column decision keys must be nonempty text.')
            if depth<3:visit(item,depth+1)
            else:
                count+=1
                if count>100000 or not isinstance(item,str) or item!=item.strip() or any(char in item for char in '|\r\n\x00'):
                    raise DomainError('New column values require exact single-line DAT text (no separators or surrounding whitespace).')
    visit(value,0);return copy.deepcopy(value)


def operation_result_ids(job):
    if job.get('mode')=='result_upload':
        return {value for value in ((job.get('result_allocation') or {}).get('id'),(job.get('domain_result') or {}).get('result_id')) if value}
    return {job['request']['result_id']} if job.get('mode')=='result_adopt' else set()


def validate_operation(job,results):
    """Bind job results to existing immutable archive/application metadata."""
    from .write_requests import key
    mode=job.get('mode')
    if mode not in ('result_upload','result_adopt'):return
    try:
        intent=job.get('result_allocation');domain=job.get('domain_result')
        if intent:
            if mode!='result_upload' or key(intent['id'])!=intent['id'] or intent['sha256']!=job['request']['sha256'] or intent['size']!=job['request']['size']:raise ValueError()
            row=results.get(intent['id'])
            if intent['state']=='reserved':
                if row:raise ValueError()
            elif intent['state']!='stored' or row is None:raise ValueError()
        if domain is None:return
        identifier=domain['result_id'] if mode=='result_upload' else job['request']['result_id']
        row=results.get(identifier)
        if row is None:raise ValueError()
        value=json.loads(row['payload'])
        if mode=='result_upload':
            if row['job_id']!=job['request']['geometry_job_id'] or domain['geometry_job_id']!=row['job_id'] or domain['resources']:raise ValueError()
            if any(value[name]!=job['request'][name] or domain[name]!=value[name] for name in ('sha256','size')):raise ValueError()
            if domain['reused_existing'] is False and (not intent or intent['state']!='stored' or intent['id']!=identifier):raise ValueError()
        else:
            applied=value['applied']
            if not applied or applied['target_release_id']!=job['request']['target_release_id'] or applied['preview_hash']!=job['request']['preview_hash']:raise ValueError()
            if normalize_column_values(applied.get('column_values'))!=normalize_column_values(job['request'].get('column_values')):raise ValueError()
            if [{'id':r['id'],'revision':r['revision']} for r in applied['resources']]!=domain['resources']:raise ValueError()
    except (KeyError,ValueError,TypeError,DomainError) as error:
        raise DomainError('Prepared-result operation differs from its immutable archive/application binding.',409) from error


def _manifest_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise DomainError('Repeated prepared manifest field: '+key)
        result[key]=value
    return result


class Object(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    cam:dict


class PreparedManifest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    contract_version:Literal[1]
    job_id:str
    resource_id:str
    resource_revision:int=Field(ge=1)
    resource_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    input_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    worker_hashes:dict[str,str]
    nx_version:Literal['2512']
    tool_type:str
    automatic_cam_approval:Literal[False]
    root_library_id:str
    root_key:str=Field(min_length=1,max_length=120)
    root_part:str=Field(min_length=1,max_length=120)
    objects:list[Object]=Field(min_length=1,max_length=20)
    parts:list[Part]=Field(min_length=1,max_length=80)
    components:list[Component]=Field(max_length=10000)
    files:list[File]=Field(min_length=1,max_length=80)
    collector:str=Field(min_length=1,max_length=200)


def parse_package(content,job):
    if not content or len(content)>MAX_ARCHIVE:raise DomainError('Prepared ZIP must contain 1 byte to 64 MiB.')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            entries=package.infolist();names=[item.filename for item in entries]
            if len(entries)>81 or len(set(name.casefold() for name in names))!=len(names):raise DomainError('Duplicate/too many result ZIP entries.')
            if any(item.is_dir() or item.flag_bits&1 or item.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED)
                   or (item.external_attr>>16)&0o170000==0o120000 for item in entries):raise DomainError('Unsupported result ZIP entry.')
            for name in names:relative_output(name)
            if 'manifest.json' not in names or package.getinfo('manifest.json').file_size>MAX_MANIFEST:
                raise DomainError('Prepared package requires manifest.json, at most 2 MiB.')
            raw=package.read('manifest.json')
            decoded=json.loads(raw,object_pairs_hook=_manifest_object)
            if not isinstance(decoded,dict):raise DomainError('Prepared manifest must be a JSON object.')
            if type(decoded.get('contract_version')) is not int or type(decoded.get('automatic_cam_approval')) is not bool:
                raise DomainError('Prepared version/approval flags require strict integer/boolean types.')
            try:manifest=PreparedManifest.model_validate(decoded).model_dump()
            except ValidationError as error:raise DomainError('Invalid prepared manifest fields: '+str([list(item['loc']) for item in error.errors()[:10]])) from error
            expected={'job_id':job['id'],'resource_id':job['resource_id'],'resource_revision':job['resource_revision'],
                'resource_hash':job['resource_hash'],'input_sha256':job['input']['sha256'],'worker_hashes':job['worker_hashes'],
                'nx_version':job['nx_version'],'tool_type':job['tool_type']}
            if any(manifest[key]!=value for key,value in expected.items()):raise DomainError('Prepared result does not match the pinned STEP job.',409)
            if manifest['root_part']!=manifest['root_key']+'.prt':raise DomainError('Root graphic must be the unchanged LIBRF.prt basename.')
            asset_filename(manifest['root_part']);files={};total=0
            for item in manifest['files']:
                key=relative_output(item['path'])
                for part in item['path'].split('/'):asset_filename(part)
                if not item['path'].lower().endswith('.prt') or key in files or not 0<item['size']<=MAX_FILE:
                    raise DomainError('Result files must be unique PRT components, 1 byte to 20 MiB each.')
                files[key]=item;total+=item['size']
            if total>MAX_TOTAL or set(names)!={'manifest.json',*['files/'+item['path'] for item in manifest['files']]}:
                raise DomainError('Prepared file list/size differs from the ZIP contract.')
            if manifest['root_part'].casefold() not in files:raise DomainError('Root PRT is absent.')
            parts={}
            for part in manifest['parts']:
                key=relative_output(part['path'])
                if key in parts or not part['fully_loaded']:raise DomainError('All prepared part prototypes must be unique and fully loaded.')
                if len({body['id'] for body in part['bodies']})!=len(part['bodies']):raise DomainError('Repeated body IDs in a part.')
                parts[key]=part
            if set(parts)!=set(files):raise DomainError('PRT inventory must cover exactly every packaged component.')
            if not any(body['solid'] for part in parts.values() for body in part['bodies']):raise DomainError('Prepared inventory contains no reported solid bodies.')
            components={}
            for item in manifest['components']:
                if item['id'] in components or relative_output(item['part']) not in parts:raise DomainError('Duplicate/dangling assembly occurrence.')
                components[item['id']]=item
            edges={key:set() for key in parts};root=manifest['root_part'].casefold()
            for occurrence,item in components.items():
                parent=occurrence.rsplit('/',1)[0]
                if parent and parent not in components:raise DomainError('Missing parent assembly occurrence.')
                parent_part=relative_output(components[parent]['part']) if parent else root
                edges[parent_part].add(relative_output(item['part']))
            visiting=set();visited=set()
            def visit(key):
                if key in visiting:raise DomainError('Cyclic prepared part graph.')
                if key in visited:return
                visiting.add(key)
                for child in edges[key]:visit(child)
                visiting.remove(key);visited.add(key)
            visit(root)
            if visited!=set(parts):raise DomainError('Unreachable PRT prototypes; package must describe one closed model graph.')
            objects={};cells=0
            for item in manifest['objects']:
                cam=item['cam'];spec=spec_for(cam.get('library_id'))
                if spec['kind'] not in ALLOWED_KINDS or cam.get('nx_version')!='2512' or not spec['graphics_dir']:
                    raise DomainError('Unsupported prepared library/version.')
                table=cam_table(cam);key=next(iter(table.groups()));identity=(spec['id'],key)
                if identity in objects:raise DomainError('Duplicate prepared native object.')
                cells+=sum(len(record['values']) for record in table.records)
                if cells>100000:raise DomainError('Prepared native field count exceeds 100,000.')
                objects[identity]=cam
            root_object=(manifest['root_library_id'],manifest['root_key'])
            if root_object not in objects:raise DomainError('Root native object is absent.')
            needed=set();pending=[root_object]
            while pending:
                key=pending.pop()
                if key in needed:continue
                needed.add(key)
                for record in objects[key]['records']:
                    for field,library in reference_targets(key[0]).items():
                        target=(library,record['values'].get(field,''))
                        if target in objects:pending.append(target)
            if needed!=set(objects):raise DomainError('Unrelated native objects must not be smuggled into a tool result.')
            for item in manifest['files']:
                info=package.getinfo('files/'+item['path'])
                if info.file_size!=item['size'] or hashlib.sha256(package.read(info)).hexdigest()!=item['sha256']:
                    raise DomainError('Prepared component bytes differ from their manifest.',409)
            return manifest
    except (zipfile.BadZipFile,RuntimeError,KeyError,TypeError,ValueError,AttributeError,RecursionError) as error:
        raise DomainError('Prepared result ZIP cannot be parsed safely.') from error


class Results:
    def __init__(self,store):self.store=store
    def get(self,key):
        try:
            if str(uuid.UUID(key))!=key:raise ValueError()
        except (ValueError,TypeError,AttributeError):raise DomainError('Invalid prepared result ID.')
        with self.store.db() as db:row=db.execute('SELECT payload FROM geometry_results WHERE id=?',(key,)).fetchone()
        if not row:raise DomainError('Prepared result not found.',404)
        return json.loads(row['payload'])
    def list(self,job_id):
        with self.store.db() as db:keys=[row['id'] for row in db.execute('SELECT id FROM geometry_results WHERE job_id=? ORDER BY rowid DESC',(job_id,))]
        return [self.get(key) for key in keys]
    def archive(self,key):
        value=self.get(key);path=offline_path(self.store.root/'geometry-results'/key/'result.zip')
        try:intact=path.is_file() and path.stat().st_size==value['size'] and sha256(path)==value['sha256']
        except OSError as error:raise DomainError('Stored prepared archive is missing or unreadable.',409) from error
        if not intact:raise DomainError('Stored prepared archive changed or is missing.',409)
        return path
    def validate_assignment(self,resource,assets):
        package=resource.get('model_package')
        if not package:return
        result=self.get(package['result_id']);self.archive(result['id'])
        if package['archive_sha256']!=result['sha256'] or not result.get('applied') or resource['id'] not in {
            item['id'] for item in result['applied']['resources']}:
            raise DomainError('Prepared package provenance is not bound to this saved resource.',409)
        known={item['id']:item for item in assets};actual={}
        prefix=spec_for(package['library_id'])['graphics_dir']+'/'
        for binding in resource.get('file_bindings',[]):
            if binding['asset_id'] not in package['asset_ids'] or not binding['path'].startswith(prefix):
                raise DomainError('Prepared model path is detached from its packet.',409)
            metadata=known.get(binding['asset_id'])
            if not metadata:raise DomainError('Prepared component attachment is absent.',409)
            actual[binding['path'][len(prefix):]]=(metadata['size'],metadata['sha256'])
        expected={item['path']:(item['size'],item['sha256']) for item in result['manifest']['files']}
        if actual!=expected:raise DomainError('Prepared component layout/hashes differ from the immutable result.',409)
    def _current(self,job):
        with self.store.db() as db:row=db.execute('SELECT * FROM resources WHERE id=?',(job['resource_id'],)).fetchone()
        if not row or row['revision']!=job['resource_revision'] or hashlib.sha256(row['payload'].encode()).hexdigest()!=job['resource_hash']:
            raise DomainError('Current resource/ERP revision differs from this preparation job. Create a new job.',409)
        value=json.loads(row['payload'])
        if value.get('cam_status')!='draft':raise DomainError('Prepared results may be applied only to the pinned draft.')
        GeometryJobs(self.store).package(job['id'])  # input + immutable original worker snapshot
        return {**value,'id':row['id'],'revision':row['revision'],'updated_at':row['updated_at']}
    def collector(self,job_id,library_id,key,actor):
        with self.store.lock:
            job=GeometryJobs(self.store).get(job_id);parent=self._current(job);spec=spec_for(library_id)
            if spec['kind']!=parent['kind'] or spec['kind'] not in ('tool','tool_assembly') or not spec['graphics_dir']:
                raise DomainError('Collector root must match this job tool/library kind.')
            asset_filename(key+'.prt');identity=resource_identity(parent)
            if identity and identity!=(library_id,key):raise DomainError('Collector may not change the saved tool library/key.')
            root=Path(__file__).parent
            from .nxbootstrap import journal_source
            workers={'nx_result_collect.py':journal_source('resultworker.py'),
                'nx_libraries.py':(root/'libraries.py').read_bytes(),
                'nx_safety.py':(root/'safety.py').read_bytes(),
                'nx_result_inventory.py':journal_source('nxworker.py'),
                'stepfiles.py':(root/'stepfiles.py').read_bytes()}
            configuration={'job':{k:job[k] for k in ('id','resource_id','resource_revision','resource_hash','input','worker_hashes','nx_version','tool_type')},
                'root_library_id':library_id,'root_key':key,'helper_hashes':{name:hashlib.sha256(data).hexdigest() for name,data in workers.items()}}
            output=io.BytesIO()
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as package:
                def put(name,data):
                    info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
                    package.writestr(info,data)
                for name,data in workers.items():put(name,data)
                put('collector.json',canonical(configuration).encode())
                put('LIESMICH.txt',('''NX2512 Ergebnis-Collector (kein automatischer Assistant)
1. In NEUEN Ordner unter C:\\NX_Migration\\ToolJobs entpacken, getrennt vom STEP-Importpaket.
2. Assistant-Aufbereitung fachlich abschliessen. Nur in eine lokale TEST-Bibliothek exportieren,
   niemals W:/UNC/Original-Installation beschreiben. Herstellerparameter und einfachen
   Ersatzschaft pruefen. Alle Komponenten/Referenzen muessen geschlossen und portabel sein.
3. Den lokalen Bibliotheksexport mit ORIGINALNAMEN in return-library kopieren: unveraenderte
   Siemens-Standardpfade fuer MACH/resource/library/tool/<metric oder english>/*_database.dat
   und MACH/resource/library/tool/graphics/*.prt. Unterordner/Komponentenlayout erhalten.
4. Isolierte lokale NX2512-Sitzung verwenden, Netzwerk-/Produktivzugriff sperren. Das gespeicherte
   return-library/MACH/resource/library/tool/graphics/LIBRF.prt in NX oeffnen, alle Komponenten voll laden und speichern.
   Alte/offene Fremdteile schliessen. Bei externen/absoluten Komponentenverweisen vorher lokal
   korrigieren; dieser Collector darf nur Teile in dieser return-library erfassen.
5. Journal Play: nx_result_collect.py. Er liest fertige PRTs und minimale DAT-Referenzgruppen;
   kein SaveAs, Umbenennen, CAM-Export, DLL-Start oder Koerper-/Montage-Raten.
6. prepared-result.zip zum SAME Auftrag hochladen, Zielvorschau pruefen und als neue Entwuerfe
   uebernehmen. Modellinventar ist externe unbeglaubigte Aussage; sechs Geometriepruefungen,
   NX-Test und fachliche Freigabe bleiben erforderlich. Der Server startet NX nicht.
Ein echter NX-/Assistant-Nachweis fuer diese Journalversion steht noch aus.
'''.replace('SAME','gleichen')).encode('utf-8-sig'))
            data=output.getvalue();value=hashlib.sha256(data).hexdigest()
            folder=offline_path(self.store.root/'geometry-results/collectors'/job_id/value)
            path=offline_path(folder/'collector.zip')
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT payload FROM geometry_jobs WHERE id=?',(job_id,)).fetchone();payload=json.loads(row['payload'])
                current=db.execute('SELECT revision,payload FROM resources WHERE id=?',(job['resource_id'],)).fetchone()
                if not current or current['revision']!=job['resource_revision'] or hashlib.sha256(current['payload'].encode()).hexdigest()!=job['resource_hash']:
                    raise DomainError('Resource changed during collector planning; no new collector saved.',409)
                snapshots=payload.setdefault('collector_packages',{})
                if value not in snapshots and len(snapshots)>=20:
                    raise DomainError('At most 20 collector snapshots for one job; create a new job.')
                if folder.exists():
                    if not path.is_file() or sha256(path)!=value:raise DomainError('Existing collector snapshot changed.',409)
                else:folder.mkdir(parents=True,exist_ok=False);path.write_bytes(data)
                if value not in snapshots:
                    snapshots[value]={'sha256':value,'size':len(data),'root_library_id':library_id,'root_key':key}
                    db.execute('UPDATE geometry_jobs SET payload=? WHERE id=?',(canonical(payload),job_id))
                    self.store.audit(db,actor,'geometry.collector.created',{'id':job_id,'sha256':value})
            return data,value
    def upload(self,job_id,content,actor,operation_job_id=None):
        job=GeometryJobs(self.store).get(job_id);manifest=parse_package(content,job);value=hashlib.sha256(content).hexdigest()
        with self.store.lock:
            from .job_results import result_uploaded,reserve_result
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                for row in db.execute('SELECT payload FROM geometry_results WHERE job_id=?',(job_id,)):
                    previous=json.loads(row['payload'])
                    if previous['sha256']==value:
                        self.archive(previous['id']);binding=result_uploaded(db,operation_job_id,actor,previous,False)
                        if binding:self.store.audit(db,actor,'geometry.result.reused',{'id':previous['id'],**binding})
                        return previous
            self._current(job);key=str(uuid.uuid4());folder=offline_path(self.store.root/'geometry-results'/key)
            reserve_result(self.store,operation_job_id,actor,{'id':key,'sha256':value,'size':len(content)})
            folder.mkdir(parents=True,exist_ok=False);path=offline_path(folder/'result.zip');path.write_bytes(content)
            if sha256(path)!=value:raise DomainError('Stored result bytes differ.',409)
            payload={'id':key,'job_id':job_id,'resource_id':job['resource_id'],'sha256':value,'size':len(content),
                'manifest':manifest,'received_at':now(),'applied':None,'trust':'unverified_external_nx_inventory','automatic_cam_approval':False}
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                current=db.execute('SELECT revision,payload FROM resources WHERE id=?',(job['resource_id'],)).fetchone()
                if not current or current['revision']!=job['resource_revision'] or hashlib.sha256(current['payload'].encode()).hexdigest()!=job['resource_hash']:
                    raise DomainError('Resource changed during result upload; allocation retained.',409)
                if any(json.loads(row['payload'])['sha256']==value for row in db.execute('SELECT payload FROM geometry_results WHERE job_id=?',(job_id,))):
                    raise DomainError('Another writer stored these result bytes; partial allocation retained, inspect instead of duplicating.',409)
                db.execute('INSERT INTO geometry_results(id,job_id,resource_id,payload) VALUES(?,?,?,?)',(key,job_id,job['resource_id'],canonical(payload)))
                binding=result_uploaded(db,operation_job_id,actor,payload,True)
                self.store.audit(db,actor,'geometry.result.received',{'id':key,'job_id':job_id,'sha256':value,**binding})
            return payload
    def preview(self,key,target_release,*,column_values=None):
        decisions=normalize_column_values(column_values)
        result=self.get(key);self.archive(key);job=GeometryJobs(self.store).get(result['job_id']);parent=self._current(job)
        manifest=result['manifest'];release=self.store.get_release(target_release)
        if release['nx_version']!=manifest['nx_version'] or release['status'] in ('failed','building'):
            raise DomainError('Choose a complete managed target of the result NX version.')
        root_identity=(manifest['root_library_id'],manifest['root_key'])
        if spec_for(root_identity[0])['kind']!=parent['kind']:raise DomainError('Prepared root kind differs from its saved resource.')
        tracked=resource_identity(parent)
        if parent.get('origin') and not parent['origin'].get('editable'):
            raise DomainError('An imported standard object cannot be replaced by a prepared result; create your own resource.')
        if tracked and tracked!=root_identity:raise DomainError('Prepared result may not change the saved native library/key.')
        tables={};hashes={};folder=self.store.release_folder(target_release)
        expected={item['path'].casefold():item for item in release['manifest']}
        for identifier,spec in SPECS.items():
            item=expected.get(spec['path'].casefold())
            if not item:continue
            table=NativeTable.read(folder/spec['path'])
            if table.source_sha256!=item['sha256']:raise DomainError('Target schema/reference input changed.',409)
            tables[identifier]=table;hashes[identifier]=table.source_sha256
        catalog=self.store.resources()
        projected={};actions=[];conflicts=[];additions={};owners={resource_identity(value):value for value in catalog if resource_identity(value)}
        for item in manifest['objects']:
            source=cam_table(item['cam']);identity=(item['cam']['library_id'],next(iter(source.groups())))
            if identity[0] not in tables:conflicts.append({'library':identity[0],'key':identity[1],'reason':'Target native library is absent.'});continue
            try:
                for record in source.records:
                    block=projection_block(record,source,tables[identity[0]])
                    for field in block['fields']:
                        if field in record['values']:continue
                        coordinate=(*identity,block['id'],field)
                        new=additions.setdefault(coordinate,{'library':identity[0],'key':identity[1],'block_id':block['id'],
                            'field':field,'affected_rows':0,'confirmed':field in decisions.get(identity[0],{}).get(identity[1],{}).get(block['id'],{}),
                            'value':decisions.get(identity[0],{}).get(identity[1],{}).get(block['id'],{}).get(field)})
                        new['affected_rows']+=1
                records=project_records(source.records,source,tables[identity[0]],decisions.get(identity[0],{}).get(identity[1],{}))
            except DomainError as error:conflicts.append({'library':identity[0],'key':identity[1],'reason':str(error)});continue
            projected[identity]=native_cam(identity[0],manifest['nx_version'],tables[identity[0]],records)
            if spec_for(identity[0])['kind'] in ('tool','tool_assembly') and identity[1]+'.prt' not in {item['path'] for item in manifest['files']}:
                conflicts.append({'library':identity[0],'key':identity[1],'reason':'Cutting tool requires its own unchanged LIBRF.prt graphic.'})
            existing=owners.get(identity);stock=tables[identity[0]].groups().get(identity[1]);mode='update_parent' if identity==root_identity else 'create_draft'
            if identity==root_identity:
                if existing and existing['id']!=parent['id']:conflicts.append({'library':identity[0],'key':identity[1],'reason':'Another resource owns this tool key.'})
                # Let normal export ownership checks decide tracked parent edits.
                if stock and not parent.get('origin') and not any((r.get('catalog') or {}).get('exported_native',{}).get(parent['id']) or (r.get('catalog') or {}).get('exported_tools',{}).get(parent['id']) for r in self.store.releases()):
                    conflicts.append({'library':identity[0],'key':identity[1],'reason':'Root key already exists without this resource ownership.'})
            elif existing:
                try:current=cam_table(existing['cam']);same=records_hash(project_records(current.records,current,tables[identity[0]]))==records_hash(records)
                except DomainError:same=False
                if not same:conflicts.append({'library':identity[0],'key':identity[1],'reason':'Existing related catalog data differs; no automatic overwrite.'})
                mode='reuse_catalog'
            elif stock:
                if records_hash(stock)!=records_hash(records):conflicts.append({'library':identity[0],'key':identity[1],'reason':'Existing standard/parent data differs; no overwrite.'})
                mode='reuse_standard'
            actions.append({'library':identity[0],'key':identity[1],'mode':mode,'resource_id':existing['id'] if existing else None,
                            'revision':existing['revision'] if existing else None})
        for library,objects in decisions.items():
            for object_key,blocks in objects.items():
                for block,fields in blocks.items():
                    if any((library,object_key,block,field) not in additions for field in fields):
                        raise DomainError('Column decisions may fill only declared new target fields of this exact prepared object/FORMAT.')
        virtual={identifier:copy.deepcopy(table) for identifier,table in tables.items()}
        for identity,cam in projected.items():
            table=virtual[identity[0]];groups=table.groups();groups[identity[1]]=cam['records'];virtual[identity[0]]=NativeTable(table.render(groups))
        references=reference_report(folder,self.store.standards,manifest['nx_version'],virtual,complete_override=True)
        conflicts.extend(references['errors'])
        for item in manifest['files']:
            relative=spec_for(root_identity[0])['graphics_dir']+'/'+item['path'];path=offline_path(folder/relative);previous=expected.get(relative.casefold())
            if previous and (previous['sha256']!=item['sha256'] or not path.is_file() or sha256(path)!=previous['sha256']):
                conflicts.append({'path':relative,'reason':'Graphics name is already occupied by different or changed bytes.'})
            elif not previous and path.exists():conflicts.append({'path':relative,'reason':'Untracked graphics file exists outside the target manifest.'})
            for action in actions:
                if action['mode']=='reuse_standard' and item['path']==action['key']+'.prt' and not previous:
                    conflicts.append({'path':relative,'reason':'A result may not invent new graphics for an existing standard object.'})
        if len(parent['asset_ids'])+len(manifest['files'])>100:conflicts.append({'reason':'Combined attachments exceed 100; no files are silently removed.'})
        report={'result_id':key,'target_release_id':target_release,'target_manifest_sha256':digest(release['manifest']),
            'archive_sha256':result['sha256'],'resource_id':parent['id'],'expected_revision':parent['revision'],
            'actions':actions,'files':manifest['files'],'schema_hashes':hashes,'references':references,
            'conflicts':conflicts,'can_apply':not conflicts,'cam_status_after_apply':'draft','automatic_cam_approval':False,
            'trust':'unverified_external_nx_inventory','geometry_checks_reset':True}
        report['catalog_revisions']=[{'id':r['id'],'revision':r['revision']} for r in catalog]
        if additions or decisions:
            report.update(column_decisions_contract=1,column_decisions=[additions[key] for key in sorted(additions)],column_values=decisions)
        report['preview_hash']=digest([report,parent,[[list(identity),cam] for identity,cam in sorted(projected.items())]])
        return report,projected,parent
    def _verify_apply_commit(self,db,report,result,target,job,assets,decisions,baseline):
        """Fresh metadata/byte gate under the same draft/result SQL writer.

        A shallow read facade uses this exact transaction, never a Store
        constructor or another metadata snapshot. External filesystem writers
        are not locked: this is not an atomic filesystem snapshot.
        """
        try:
            row=db.execute('SELECT * FROM releases WHERE id=?',(target['id'],)).fetchone()
            if row is None:raise DomainError('Prepared target disappeared.')
            current=self.store._release_value(row)
            if any(current[name]!=target[name] for name in ('id','folder','nx_version','parent_id','manifest','catalog','status')):
                raise DomainError('Prepared target metadata/folder changed.')
            row=db.execute('SELECT payload FROM geometry_results WHERE id=?',(result['id'],)).fetchone()
            if row is None or json.loads(row['payload'])!=result:raise DomainError('Prepared result metadata changed.')
            row=db.execute('SELECT payload FROM geometry_jobs WHERE id=?',(job['id'],)).fetchone()
            if row is None:raise DomainError('Pinned STEP job disappeared.')
            current_job=json.loads(row['payload'])
            for name in ('id','resource_id','resource_revision','resource_hash','input','worker_hashes','nx_version','tool_type','package_size','package_sha256'):
                if current_job.get(name)!=job.get(name):raise DomainError('Pinned STEP job changed.')
            row=db.execute('SELECT * FROM assets WHERE id=?',(job['input']['id'],)).fetchone()
            if row is None or any(row[name]!=job['input'][name] for name in ('id','filename','sha256','size','created_at')):
                raise DomainError('Pinned STEP metadata changed.')
            if self.store.standards.get(target['nx_version'])!=baseline:raise DomainError('Reference baseline root changed.')

            class ReadConnection:
                def execute(self,sql,*parameters):
                    statement=sql.strip().upper()
                    if statement=='BEGIN':return db.execute('SELECT 1')  # Already inside BEGIN IMMEDIATE.
                    if not statement.startswith('SELECT '):raise DomainError('Final prepared-result replan must be read-only.')
                    return db.execute(sql,*parameters)
            @contextmanager
            def same_transaction():yield ReadConnection()
            view=copy.copy(self.store);view.db=same_transaction
            fresh=Results(view).preview(result['id'],target['id'],column_values=decisions)[0]
            if not fresh['can_apply'] or fresh['preview_hash']!=report['preview_hash']:
                raise DomainError('Prepared plan changed in the final transaction.')
            if self.store.manifest(offline_path(current['folder']))!=current['manifest']:
                raise DomainError('Prepared target bytes/inventory changed.')
            for asset,item in zip(assets,result['manifest']['files']):
                row=db.execute('SELECT * FROM assets WHERE id=?',(asset['id'],)).fetchone()
                if row is None or any(row[name]!=asset[name] for name in ('id','filename','size','sha256')):
                    raise DomainError('Copied prepared attachment metadata changed.')
                if (asset['size'],asset['sha256'])!=(item['size'],item['sha256']):raise DomainError('Copied attachment differs from prepared inventory.')
                actual=file_fingerprint(offline_path(self.store.root/'assets'/asset['id']/asset['filename']),max_bytes=MAX_FILE)
                if actual!={'size':asset['size'],'sha256':asset['sha256']}:raise DomainError('Copied prepared attachment bytes changed.')
        except (DomainError,OSError,ValueError,TypeError,KeyError) as error:
            raise DomainError('Final prepared-result check failed; no drafts/application committed. '+str(error),409) from error

    def apply(self,key,target_release,preview_hash,actor,operation_job_id=None,progress=None,*,column_values=None):
        decisions=normalize_column_values(column_values)
        with self.store.lock:
            result=self.get(key)
            if result.get('applied'):
                if operation_job_id is not None:raise DomainError('This result was already committed by another operation; inspect its application instead.',409)
                if result['applied']['preview_hash']!=preview_hash or result['applied']['target_release_id']!=target_release or result['applied'].get('column_values',{})!=decisions:
                    raise DomainError('This result was already applied with different decisions.',409)
                self.archive(key);return result['applied']
            target=self.store.get_release(target_release);job=GeometryJobs(self.store).get(result['job_id'])
            baseline=self.store.standards.get(target['nx_version'])
            report,projected,parent=self.preview(key,target_release,column_values=decisions)
            if not report['can_apply'] or report['preview_hash']!=preview_hash:raise DomainError('Result preview changed or has conflicts; recheck before applying.',409)
            manifest=result['manifest'];assets=[];bindings=[]
            with zipfile.ZipFile(self.archive(key)) as package:
                for item in manifest['files']:
                    args=(PurePosixPath(item['path']).name,package.read('files/'+item['path']),actor)
                    asset=self.store.add_asset(*args,operation_job_id=operation_job_id) if operation_job_id is not None else self.store.add_asset(*args)
                    assets.append(asset);bindings.append({'asset_id':asset['id'],'path':spec_for(manifest['root_library_id'])['graphics_dir']+'/'+item['path']})
                    if progress:progress('building',20+int(55*len(assets)/len(manifest['files'])),'Kopiere gepruefte PRT-Komponenten als immutable Anhaenge')
            # Files may survive an interrupted transaction as unassigned immutable
            # attachments. Metadata/revisions/result application commit together.
            current_catalog={(r['id'],r['revision']) for r in self.store.resources()}
            if current_catalog!={(r['id'],r['revision']) for r in report['catalog_revisions']}:
                raise DomainError('Catalog ownership/revisions changed during prepared application; no drafts committed.',409)
            checked=self.preview(key,target_release,column_values=decisions)[0]
            if checked['preview_hash']!=preview_hash or not checked['can_apply']:
                raise DomainError('Prepared inputs/target changed during model copy; partial attachments retained.',409)
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE');saved=[]
                current_catalog={(r['id'],r['revision']) for r in db.execute('SELECT id,revision FROM resources')}
                if current_catalog!={(r['id'],r['revision']) for r in report['catalog_revisions']}:
                    raise DomainError('Catalog ownership/revisions changed during prepared application; no drafts committed.',409)
                current=db.execute('SELECT revision,payload FROM resources WHERE id=?',(parent['id'],)).fetchone()
                if not current or current['revision']!=parent['revision'] or hashlib.sha256(current['payload'].encode()).hexdigest()!=job['resource_hash']:
                    raise DomainError('Source revision changed during result application.',409)
                self._verify_apply_commit(db,report,result,target,job,assets,decisions,baseline)
                for action in report['actions']:
                    if action['mode'].startswith('reuse_'):continue
                    identity=(action['library'],action['key']);cam=projected[identity];is_parent=action['mode']=='update_parent'
                    resource_id=parent['id'] if is_parent else str(uuid.uuid4());revision=parent['revision']+1 if is_parent else 1
                    if is_parent and parent['cam'] and 'library_id' not in parent['cam']:
                        if len(cam['records'])!=1:raise DomainError('Legacy tool representation needs a single prepared native row.')
                        cam={k:cam['records'][0][k] for k in ('class_name','values')}
                    spec=spec_for(identity[0]);tool=spec['kind'] in ('tool','tool_assembly')
                    attached=list(parent['asset_ids']) if is_parent else []
                    use_models=is_parent or tool
                    selected_bindings=bindings if use_models else []
                    if use_models:attached.extend(asset['id'] for asset in assets)
                    geometry={'mode':'nx_cam' if use_models else 'none','status':'needs_preparation','nx_version':'2512',
                        'checks':{field:False for field in ('units','orientation','mount_point','cutting_geometry','non_cutting_geometry','collision_geometry')},
                        'evidence':'','asset_hashes':{}}
                    provenance={'result_id':key,'archive_sha256':result['sha256'],'root_part':identity[1]+'.prt',
                        'library_id':identity[0],
                        'bindings_hash':digest(selected_bindings),'asset_ids':[asset['id'] for asset in assets] if use_models else [],
                        'trust':'unverified_external_nx_inventory'} if use_models else None
                    value={k:copy.deepcopy(parent[k]) for k in ('kind','name','erp','origin') if k in parent} if is_parent else {
                        'kind':spec['kind'],'name':identity[1],'erp':None,'origin':None}
                    value.update(cam=cam,cam_status='draft',asset_ids=attached,file_bindings=selected_bindings,model_package=provenance,geometry=geometry)
                    text,stamp=canonical(value),now();external=canonical([value['erp']['company_id'],value['erp']['system_id']]) if value.get('erp') else None
                    db.execute('INSERT INTO resources(id,revision,payload,external_key,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at',
                        (resource_id,revision,text,external,stamp))
                    db.execute('INSERT INTO resource_versions(id,revision,payload,actor,created_at) VALUES(?,?,?,?,?)',(resource_id,revision,text,actor,stamp))
                    self.store.audit(db,actor,'resource.saved',{'id':resource_id,'revision':revision,'payload':value})
                    saved.append({'id':resource_id,'revision':revision,'kind':value['kind'],'name':value['name']})
                result['applied']={'preview_hash':preview_hash,'target_release_id':target_release,'resources':saved,
                    'applied_at':now(),'cam_approval_changed':False,'new_release_created':False}
                if decisions:result['applied']['column_values']=decisions
                db.execute('UPDATE geometry_results SET payload=? WHERE id=?',(canonical(result),key))
                from .job_results import resource_result
                binding=resource_result(db,operation_job_id,actor,'result_adopt',[{'id':r['id'],'revision':r['revision']} for r in saved])
                self.store.audit(db,actor,'geometry.result.applied',{'id':key,'resources':saved,'archive_sha256':result['sha256'],**binding})
            return result['applied']

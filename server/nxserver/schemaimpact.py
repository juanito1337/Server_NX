"""Read-only catalog schema worklist, reusing exact per-object adaptation rules."""
import copy

from . import schemas
from .libraries import SPECS,KINDS,digest
from .safety import DomainError

MAX_RESOURCES=10000
MAX_CELLS=1000000
MAX_TARGET_BYTES=128*1024*1024
MAX_METADATA_BYTES=32*1024*1024
STATES=('current','ready','decisions_required','blocked','read_only','not_applicable')
TARGET_KEYS=('id','nx_version','status','folder','parent_id','manifest','catalog')


def context(release,resources):
    return {'target':{key:release[key] for key in TARGET_KEYS},'resources':sorted(resources,key=lambda value:value['id'])}


class SchemaImpact:
    def __init__(self,store):self.store=store

    def _snapshot(self,release_id):
        with self.store.db() as db:
            db.execute('BEGIN')
            bound=db.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(payload AS BLOB))),0) FROM resources').fetchone()
            target_size=db.execute('SELECT COALESCE(length(CAST(manifest AS BLOB)),0)+COALESCE(length(CAST(catalog AS BLOB)),0) FROM releases WHERE id=?',(release_id,)).fetchone()
            if bound[0]>MAX_RESOURCES:raise DomainError('Catalog schema worklist exceeds its resource bound.')
            if bound[1]+(target_size[0] if target_size else 0)>MAX_METADATA_BYTES:raise DomainError('Schema worklist exceeds its metadata byte bound.')
            row=db.execute('SELECT * FROM releases WHERE id=?',(release_id,)).fetchone()
            if row is None:raise DomainError('Schema target release not found.',404)
            release=self.store._release_value(row)
            rows=db.execute('SELECT * FROM resources ORDER BY id LIMIT ?',(MAX_RESOURCES+1,)).fetchall()
            if len(rows)>MAX_RESOURCES:raise DomainError('Catalog schema worklist exceeds its resource bound.')
            resources=[self.store._resource_value(row) for row in rows]
        if release['status'] in ('building','failed') or not release['manifest']:
            raise DomainError('Choose a completed manifest-bound schema target.',409)
        return release,resources

    def inspect(self,release_id,library_id='',kind='',cam_status='',state='',query='',offset=0,limit=50,context_sha256=None):
        if library_id and library_id not in SPECS or kind and kind not in KINDS or cam_status not in ('','draft','approved','retired') or state not in ('',*STATES):
            raise DomainError('Unknown catalog schema worklist filter.')
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100 or not isinstance(query,str) or len(query)>200:
            raise DomainError('Invalid schema worklist page or query.')
        filters={'library_id':library_id,'kind':kind,'cam_status':cam_status,'state':state,'query':query}
        with self.store.lock:
            release,resources=self._snapshot(release_id);pinned=context(release,resources);pin=digest(pinned)
            if context_sha256 is not None and pin!=context_sha256:raise DomainError('Catalog schema context changed; read a new first page.',409)
            scoped=[value for value in resources if (not kind or value['kind']==kind) and (not cam_status or value['cam_status']==cam_status)]
            targets={};target_bytes=0;cells=0;items=[]
            def target(identifier):
                nonlocal target_bytes
                if identifier not in targets:
                    size=next((item['size'] for item in release['manifest'] if item['path'].casefold()==SPECS[identifier]['path'].casefold()),0)
                    target_bytes+=size
                    if target_bytes>MAX_TARGET_BYTES:raise DomainError('Schema worklist exceeds its total target-library byte bound.')
                    try:
                        loaded=schemas._target(self.store,release_id,identifier)
                        if context(loaded[0],resources)!=pinned:raise DomainError('Target metadata changed while reading schema worklist.',409)
                        targets[identifier]=loaded
                    except DomainError as error:targets[identifier]=error
                value=targets[identifier]
                if isinstance(value,DomainError):raise value
                return value
            for resource in scoped:
                cam=resource.get('cam') or {};identifier=cam.get('library_id') or ('tool-metric' if resource['kind']=='tool' and cam.get('values') else None)
                if library_id and identifier!=library_id:continue
                item={'resource_id':resource['id'],'revision':resource['revision'],'name':resource['name'],'kind':resource['kind'],
                    'cam_status':resource['cam_status'],'resource_sha256':digest(resource),'cam_sha256':digest(cam),
                    'library_id':identifier,'key':None,'source_nx_version':cam.get('nx_version'),'source_representation':None,
                    'state':'not_applicable','reason':'No native CAM object; file packages and metadata use their own workflow.',
                    'rows':0,'new_values':0,'unmapped_fields':0,'ambiguous_rows':0,'class_changes':0,
                    'conflict_count':0,'conflicts':[],'conflicts_truncated':False,'target_library_sha256':None,
                    'projected_cam_sha256':None,'can_open_schema':False}
                if resource['cam_status']=='retired':item['reason']='Retired resource; no schema adaptation proposed.'
                elif resource.get('origin') and not resource['origin'].get('editable'):
                    item.update(state='read_only',reason='Imported Siemens standard object; do not rewrite it as a customer adaptation.')
                elif identifier:
                    try:
                        native,identifier,source,records=schemas._source(resource['kind'],cam)
                        item.update(library_id=identifier,key=next(iter(source.groups())),rows=len(records),source_representation='native' if native else 'legacy_tool')
                        cells+=sum(len(record['values']) for record in records)
                        if cells>MAX_CELLS:raise DomainError('Schema worklist exceeds its total source-cell bound.')
                        loaded=target(identifier)
                        request={'kind':resource['kind'],'cam':cam,'resource_id':resource['id'],'expected_revision':resource['revision'],'target_release_id':release_id,'decisions':{}}
                        report=schemas._plan(request,native,identifier,source,records,*loaded)
                        item.update(target_library_sha256=loaded[2],new_values=sum(not value['confirmed'] for row in report['rows'] for value in row['new_fields']),
                            unmapped_fields=sum(field['status']=='unmapped' for row in report['rows'] for field in row['fields']),
                            ambiguous_rows=sum(row['target_block'] is None for row in report['rows']),class_changes=report['changes']['class_changes'],
                            conflict_count=len(report['conflicts']),conflicts=report['conflicts'][:20],conflicts_truncated=len(report['conflicts'])>20,can_open_schema=True)
                        if report['can_apply']:
                            fingerprint=digest(report['cam']);item.update(projected_cam_sha256=fingerprint,state='current' if report['cam']==cam else 'ready',
                                reason='CAM schema already matches this target.' if report['cam']==cam else 'Projection needs no new values; review explicitly in the schema editor before saving a draft.')
                        else:item.update(state='decisions_required',reason='Explicit per-row values, mappings, omissions or target-block decisions are required.')
                    except DomainError as error:
                        if cells>MAX_CELLS or target_bytes>MAX_TARGET_BYTES:raise
                        item.update(state='blocked',reason=str(error))
                items.append(item)
            # Recheck each successful library once, not once per resource.
            for identifier,loaded in targets.items():
                if isinstance(loaded,DomainError):continue
                current=schemas._target(self.store,release_id,identifier)
                if current[2]!=loaded[2]:raise DomainError('Target schema bytes changed while planning.',409)
            # Final metadata read follows the last bounded byte verification.
            latest_release,latest_resources=self._snapshot(release_id)
            if context(latest_release,latest_resources)!=pinned:raise DomainError('Catalog schema context changed while planning; read a new first page.',409)
            counts={name:sum(item['state']==name for item in items) for name in STATES}
            needle=query.casefold();filtered=[item for item in items if (not state or item['state']==state) and (not needle or needle in '\n'.join(str(item.get(key) or '') for key in ('resource_id','name','kind','library_id','key','reason','state')) .casefold() or needle in str(item['conflicts']).casefold())]
            value={'contract_version':1,'scope':'catalog_schema_impact','release_id':release_id,'target_nx_version':release['nx_version'],
                'context_sha256':pin,'catalog_sha256':digest(pinned['resources']),'target_sha256':digest(pinned['target']),
                'filters':filters,'offset':offset,'limit':limit,'total_resources':len(resources),'scoped_resources':len(items),
                'counts':counts,'filtered':len(filtered),'items':copy.deepcopy(filtered[offset:offset+limit]),
                'read_only':True,'automatic_save':False,'automatic_approval':False,'nx_files_written':False,
                'geometry_validation':False,'reference_validation':False,'file_package_validation':False,
                'limits':{'resources':MAX_RESOURCES,'source_cells':MAX_CELLS,'target_bytes':MAX_TARGET_BYTES,'metadata_bytes':MAX_METADATA_BYTES}}
            value['report_sha256']=digest(value)
            return value

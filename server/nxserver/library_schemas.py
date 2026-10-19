"""Bounded schema-only creation reads; no native object pages or file writes."""
import copy
import json
import re
import sqlite3
from contextlib import closing

from .bounded_file import read
from .libraries import NativeTable,SPECS,decode,digest,spec_for
from .migration import portable
from .safety import DomainError,offline_path

MAX_METADATA=32*1024*1024
MAX_MANIFEST=100000
MAX_LIBRARY=32*1024*1024
MAX_SCHEMAS=1000
MAX_FIELDS=100000
MAX_REPORT=4*1024*1024


def _finish(value):
    value['report_sha256']=digest(value)
    if len(json.dumps(value,ensure_ascii=False).encode('utf-8'))>MAX_REPORT:
        raise DomainError('Schema-only response exceeds its byte bound.',409)
    return value


class LibrarySchemas:
    def __init__(self,store):self.store=store

    def _snapshot(self,release_id):
        path=offline_path(self.store.root/'management.sqlite3')
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=30)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            size=db.execute('SELECT length(CAST(manifest AS BLOB)) FROM releases WHERE id=?',(release_id,)).fetchone()
            if size is None:raise DomainError('Schema release not found.',404)
            if size[0] is None or not 1<=size[0]<=MAX_METADATA:raise DomainError('Schema manifest is missing or exceeds its byte bound.',409)
            row=db.execute('SELECT id,nx_version,status,folder,manifest FROM releases WHERE id=?',(release_id,)).fetchone()
        if row['status'] not in ('draft','validated','approved','blocked'):
            raise DomainError('Choose a completed manifest-bound release for schema creation.',409)
        if not isinstance(row['nx_version'],str) or not re.fullmatch('[0-9]{4}',row['nx_version']):
            raise DomainError('Unsupported schema release version.',409)
        try:
            manifest=json.loads(row['manifest'])
            if not isinstance(manifest,list) or not 1<=len(manifest)<=MAX_MANIFEST:raise ValueError()
            for item in manifest:
                if not isinstance(item,dict) or set(item)!={'path','size','sha256'} or not isinstance(item['path'],str) or type(item['size']) is not int or not 0<=item['size']<=2**53-1 or not isinstance(item['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',item['sha256']):raise ValueError()
                portable(item['path'])
        except (ValueError,TypeError,KeyError,DomainError) as error:
            raise DomainError('Invalid schema release manifest metadata.',409) from error
        folder=str(self.store._release_path(row['id'],row['folder']))
        release={'id':row['id'],'nx_version':row['nx_version'],'status':row['status'],'folder':folder,'manifest':manifest}
        return release,digest(release)

    def _libraries(self,release):
        entries={}
        for item in release['manifest']:
            folded=item['path'].casefold()
            if folded in entries:raise DomainError('Ambiguous case-insensitive schema manifest paths.',409)
            entries[folded]=item
        libraries=[]
        for identifier,spec in SPECS.items():
            item=entries.get(spec['path'].casefold())
            if item is None:continue
            libraries.append({**copy.deepcopy(spec),'manifest_path':item['path'],'input_size':item['size'],
                'input_sha256':item['sha256'],'can_read_schema':1<=item['size']<=MAX_LIBRARY})
        return libraries

    def inventory(self,release_id):
        release,pin=self._snapshot(release_id)
        return _finish({'contract_version':1,'scope':'native_schema_inventory','release_id':release_id,
            'nx_version':release['nx_version'],'inventory_sha256':pin,'manifest_sha256':digest(release['manifest']),
            'release_state':{'status':release['status'],'folder':release['folder']},
            'libraries':self._libraries(release),'read_only':True,'sqlite_read_only':True,'sqlite_read_transaction':True,
            'files_verified':False,'reference_validation':False,'nx_files_written':False,'automatic_approval':False,
            'max_library_bytes':MAX_LIBRARY,'max_metadata_bytes':MAX_METADATA,'max_report_bytes':MAX_REPORT})

    def schema(self,release_id,identifier,inventory_sha256):
        spec_for(identifier)
        if not isinstance(inventory_sha256,str) or not re.fullmatch('[a-f0-9]{64}',inventory_sha256):
            raise DomainError('A canonical schema inventory SHA is required.')
        release,pin=self._snapshot(release_id)
        if pin!=inventory_sha256:raise DomainError('Schema inventory changed; load the library choices again.',409)
        entry=next((item for item in self._libraries(release) if item['id']==identifier),None)
        if entry is None:raise DomainError('Schema library is not present in this release.',404)
        if not entry['can_read_schema']:raise DomainError('Selected schema library is empty or exceeds its byte bound.',409)
        raw=read(offline_path(self.store._release_path(release_id,release['folder'])/entry['manifest_path']),
            max_bytes=MAX_LIBRARY,expected_size=entry['input_size'],expected_sha256=entry['input_sha256'],
            changed_message='Schema library differs from its managed manifest.',limit_message='Selected schema library exceeds its byte bound.')
        table=NativeTable(decode(raw),schema_only=True,max_schemas=MAX_SCHEMAS,max_fields=MAX_FIELDS)
        if len(table.blocks)>MAX_SCHEMAS or sum(len(block['fields']) for block in table.blocks)>MAX_FIELDS:
            raise DomainError('Selected library exceeds the schema definition bound.',409)
        for block in table.blocks:
            if not re.fullmatch('[A-Za-z0-9_]+',block['class_name']) or any(not re.fullmatch('[A-Za-z0-9_]+',field) for field in block['fields']):
                raise DomainError('Schema names cannot be represented by the supported native editor.',409)
        _,latest=self._snapshot(release_id)
        if latest!=pin:raise DomainError('Schema inventory changed during the selected library read.',409)
        return _finish({**entry,'contract_version':1,'scope':'native_library_schema','release_id':release_id,
            'nx_version':release['nx_version'],'inventory_sha256':pin,'manifest_sha256':digest(release['manifest']),
            'release_state':{'status':release['status'],'folder':release['folder']},
            'schemas':copy.deepcopy(table.blocks),'schema_sha256':digest(table.blocks),'schema_count':len(table.blocks),
            'row_count':table.row_count,'integrity_checked':True,'read_only':True,'sqlite_read_only':True,
            'sqlite_read_transaction':True,'object_values_included':False,'reference_validation':False,
            'nx_files_written':False,'automatic_approval':False,'max_library_bytes':MAX_LIBRARY,
            'max_schemas':MAX_SCHEMAS,'max_fields':MAX_FIELDS,'max_report_bytes':MAX_REPORT})

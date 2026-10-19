"""Read-only inspection of exact native object values in a reviewed export plan."""
import re
import hashlib

from .libraries import SPECS,records_hash
from .safety import DomainError

MAX_BYTES=256*1024*1024
MAX_ROWS=500000
MAX_CELLS=5000000
MAX_PAGE_BYTES=4*1024*1024


def page(offset,limit,maximum):
    if type(offset) is not int or not 0<=offset<=1000000 or type(limit) is not int or not 1<=limit<=maximum:
        raise DomainError('Choose a bounded native-export page.')


class ExportView:
    def __init__(self,store):self.store=store

    def _plan(self,release_id,defaults,preview_hash):
        if not isinstance(preview_hash,str) or not re.fullmatch('[a-f0-9]{64}',preview_hash):
            raise DomainError('A current export preview SHA is required.')
        plan,report=self.store._build_export_plan(release_id,defaults)
        if report['preview_hash']!=preview_hash:raise DomainError('Export inputs changed; repeat the export preview before inspecting native values.',409)
        if not report['can_export']:raise DomainError('Resolve export conflicts first; no incomplete native result is presented.',409)
        tables={};rows=cells=size=0
        for identifier,after in sorted(plan['tables'].items()):
            before=plan['load_table'](identifier)
            for table in (before,after):
                size+=len(table.text.encode('utf-8'));rows+=len(table.records)
                cells+=sum(len(record['values']) for record in table.records)
                if size>MAX_BYTES or rows>MAX_ROWS or cells>MAX_CELLS:
                    raise DomainError('Native export inspection exceeds its bounded reader; no partial comparison is reported.',409)
            tables[identifier]=(before,after,before.groups(),after.groups())
        return report,tables

    def _common(self,report):
        return {'contract_version':1,'release_id':report['release_id'],'nx_version':report['nx_version'],
            'preview_hash':report['preview_hash'],'parent_manifest_sha256':report['parent_manifest_sha256'],
            'complete':True,'read_only':True,'nx_files_written':False,'resources_written':False,
            'automatic_approval':False,'scope':'native_object_values','row_alignment':'none',
            'note':'Values only; no guessed RTYPE/SEQ alignment. File hashes, package files and references remain in the main export preview.'}

    def objects(self,release_id,defaults,preview_hash,library_id='',change='',query='',offset=0,limit=50):
        page(offset,limit,100)
        if library_id and library_id not in SPECS:raise DomainError('Unknown native export library.')
        if change not in ('','created','updated','removed'):raise DomainError('Choose created, updated or removed objects.')
        if not isinstance(query,str) or len(query)>200:raise DomainError('Native export search is limited to 200 characters.')
        from .store import canonical
        with self.store.lock:
            report,tables=self._plan(release_id,defaults,preview_hash)
            counts={name:0 for name in ('created','updated','removed','unchanged')};total=filtered=0;items=[];libraries=[];needle=query.casefold()
            for identifier,(before,after,left,right) in tables.items():
                local={name:0 for name in counts}
                for key in sorted(set(left)|set(right)):
                    a=left.get(key);b=right.get(key);old=records_hash(a) if a is not None else None;new=records_hash(b) if b is not None else None
                    action='created' if a is None else 'removed' if b is None else 'unchanged' if old==new else 'updated'
                    counts[action]+=1;local[action]+=1
                    if action=='unchanged':continue
                    total+=1
                    if library_id and library_id!=identifier or change and change!=action:continue
                    if needle and needle not in key.casefold() and not any(needle in field.casefold() or needle in value.casefold() for record in (a or [])+(b or []) for field,value in record['values'].items()):continue
                    if offset<=filtered<offset+limit:
                        items.append({'library_id':identifier,'path':SPECS[identifier]['path'],'key':key,'change':action,
                            'before_rows':len(a or []),'after_rows':len(b or []),'before_sha256':old,'after_sha256':new})
                    filtered+=1
                libraries.append({'library_id':identifier,'path':SPECS[identifier]['path'],'before_sha256':before.source_sha256,
                    'after_sha256':hashlib.sha256(after.text.encode('utf-8-sig')).hexdigest(),
                    'counts':local})
            value={**self._common(report),'library_id':library_id,'change':change,'query':query,'offset':offset,'limit':limit,
                'total':total,'filtered':filtered,'counts':counts,'libraries':libraries,'items':items}
            if len(canonical(value).encode('utf-8'))>MAX_PAGE_BYTES:raise DomainError('Native export page exceeds its byte bound.',409)
            return value

    def rows(self,release_id,defaults,preview_hash,library_id,key,side,group_sha256,offset=0,limit=20):
        page(offset,limit,50)
        if library_id not in SPECS or side not in ('before','after') or not isinstance(key,str) or not 1<=len(key)<=1024:
            raise DomainError('Choose an exact native library/object and before or after side.')
        from .store import canonical
        with self.store.lock:
            report,tables=self._plan(release_id,defaults,preview_hash)
            if library_id not in tables:raise DomainError('Library is not an output of this reviewed export.',409)
            before,after,left,right=tables[library_id];table,groups=(before,left) if side=='before' else (after,right)
            if key not in left and key not in right:raise DomainError('Native object is not in this reviewed output.',404)
            records=groups.get(key)
            expected=records_hash(records) if records is not None else None
            if group_sha256!=expected:raise DomainError('Native object side differs from the selected comparison.',409)
            records=records or [];used={record['block_id'] for record in records};formats=[dict(block) for block in table.blocks if block['id'] in used]
            value={**self._common(report),'library_id':library_id,'key':key,'side':side,'group_sha256':expected,'present':expected is not None,
                'total':len(records),'offset':offset,'limit':limit,'formats':formats,
                'items':[{'row_index':index,**records[index]} for index in range(offset,min(offset+limit,len(records)))]}
            if len(canonical(value).encode('utf-8'))>MAX_PAGE_BYTES:raise DomainError('Native export row page exceeds its byte bound; reduce its page size.',409)
            return value

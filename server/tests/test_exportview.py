"""Exact native export values, all adapters, no source/SQL/approval writes."""
import copy
import hashlib

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.exportview import ExportView
from nxserver.libraries import SPECS,NativeTable,native_cam
from nxserver.store import Store
from nxserver.safety import DomainError
from test_libraries import simple,write
from test_management import tool_payload,ADMIN,ERP,HEADERS,ERP_HEADERS


def save(store,identifier,key,description='',old=None,status='approved'):
    table=NativeTable(simple(key,description or key));value={'kind':SPECS[identifier]['kind'],'name':key,
        'cam_status':status,'cam':native_cam(identifier,'2512',table,table.records),'asset_ids':[],'erp':None,
        'geometry':tool_payload()['geometry']}
    return store.save_resource(value,'fixture',old['id'] if old else None,old['revision'] if old else None)


def derive(store,parent,name='New'):
    preview=store.preview_export(parent['id']);assert preview['can_export'],preview['conflicts']
    return store.import_release('','',name,'fixture',parent=parent['id'],defaults=preview['defaults'],preview_hash=preview['preview_hash'])


@pytest.mark.parametrize('identifier',list(SPECS))
def test_every_adapter_previews_created_updated_removed_and_unchanged_groups(tmp_path_factory,identifier):
    root=tmp_path_factory.mktemp('ev');source=root/'s';write(source,SPECS[identifier]['path'],simple())
    store=Store(root/'m',{'test':source});original=store.import_release('test','2512','Original','fixture')
    first=save(store,identifier,'A');retired=save(store,identifier,'B');parent=derive(store,original)
    save(store,identifier,'A','Changed A',first);save(store,identifier,'B',old=retired,status='retired');save(store,identifier,'C')
    preview=store.preview_export(parent['id']);view=ExportView(store);before=store.manifest(store.release_folder(parent['id']));audit=store.audit_entries();releases=store.releases()
    result=view.objects(parent['id'],{},preview['preview_hash']);assert result['complete'] and result['read_only']
    assert result['counts']=={'created':1,'updated':1,'removed':1,'unchanged':1} and result['total']==result['filtered']==3
    assert [(item['key'],item['change']) for item in result['items']]==[('A','updated'),('B','removed'),('C','created')]
    for item in result['items']:
        for side in ('before','after'):
            rows=view.rows(parent['id'],{},preview['preview_hash'],identifier,item['key'],side,item[side+'_sha256'])
            assert rows['total']==item[side+'_rows'] and rows['present']==(item[side+'_sha256'] is not None)
            if rows['items']:assert rows['items'][0]['values']['LIBRF']==item['key']
    assert store.manifest(store.release_folder(parent['id']))==before and store.audit_entries()==audit and store.releases()==releases
    child=derive(store,parent);file=store.release_folder(child['id'])/SPECS[identifier]['path']
    assert result['libraries'][0]['after_sha256']==hashlib.sha256(file.read_bytes()).hexdigest()
    assert store.active()['release_id'] is None and set(NativeTable.read(source/SPECS[identifier]['path']).groups())=={'STD'}


@pytest.fixture
def view_pilot(tmp_path_factory):
    root=tmp_path_factory.mktemp('ev');source=root/'s';identifier='holder-metric';write(source,SPECS[identifier]['path'],simple())
    store=Store(root/'m',{'test':source});parent=store.import_release('test','2512','Original','fixture')
    for index in range(65):save(store,identifier,'KEY'+str(index).zfill(3),'Only searchable full text '+str(index))
    preview=store.preview_export(parent['id'])
    with TestClient(create_app(store,ADMIN,ERP)) as client:yield client,store,parent,preview


def test_objects_search_and_pages_are_bound_without_creating_release(view_pilot):
    _,store,parent,preview=view_pilot;view=ExportView(store);pin=preview['preview_hash'];audit=store.audit_entries()
    first=view.objects(parent['id'],{},pin,limit=50);last=view.objects(parent['id'],{},pin,offset=50,limit=50)
    assert len(first['items'])==50 and len(last['items'])==15 and first['filtered']==last['filtered']==65
    filtered=view.objects(parent['id'],{},pin,query='full text 64');assert filtered['items'][0]['key']=='KEY064' and filtered['filtered']==1
    assert view.objects(parent['id'],{},pin,change='removed')['filtered']==0
    assert store.audit_entries()==audit and len(store.releases())==1


@pytest.mark.parametrize('drift',['resource','source','defaults'])
def test_stale_view_is_rejected_instead_of_mixing_current_pages(view_pilot,drift):
    _,store,parent,preview=view_pilot;view=ExportView(store);old=view.objects(parent['id'],{},preview['preview_hash'])
    defaults={}
    if drift=='resource':resource=store.resources()[0];save(store,'holder-metric',resource['cam']['records'][0]['values']['LIBRF'],'Changed',resource)
    elif drift=='source':(store.release_folder(parent['id'])/SPECS['holder-metric']['path']).write_text(simple('REPLACED'))
    else:defaults={'holder-metric':{'SAMPLE:1':{'DESCR':'Unreviewed'}}}
    with pytest.raises(DomainError):view.objects(parent['id'],defaults,preview['preview_hash'],offset=50)
    item=old['items'][0]
    with pytest.raises(DomainError):view.rows(parent['id'],defaults,preview['preview_hash'],item['library_id'],item['key'],'after',item['after_sha256'])
    assert len(store.releases())==1


def test_full_multiline_group_future_columns_and_literal_json_names(tmp_path_factory):
    root=tmp_path_factory.mktemp('ev');source=root/'s';identifier='holder-metric'
    target='#CLASS MULTI\nFORMAT LIBRF RTYPE SEQ DESCR FUTURE_2512 __proto__\nDATA |STD|1|1|Standard|existing|literal\n#END_DATA\n'
    write(source,SPECS[identifier]['path'],target);store=Store(root/'m',{'test':source});parent=store.import_release('test','2512','Future','fixture')
    old=NativeTable('#CLASS MULTI\nFORMAT LIBRF RTYPE SEQ DESCR\nDATA |constructor|1|1|Root\nDATA |constructor|2|2|Child\n#END_DATA\n')
    store.save_resource({'kind':'holder','name':'Multi','cam_status':'approved','cam':native_cam(identifier,'2406',old,old.records),'asset_ids':[],'erp':None},'fixture')
    blocked=store.preview_export(parent['id']);assert not blocked['can_export']
    with pytest.raises(DomainError,match='conflicts'):ExportView(store).objects(parent['id'],{},blocked['preview_hash'])
    defaults={identifier:{'MULTI:1':{'FUTURE_2512':'Explicit new value','__proto__':'Literal own property'}}};preview=store.preview_export(parent['id'],defaults)
    item=ExportView(store).objects(parent['id'],defaults,preview['preview_hash'])['items'][0]
    pages=[ExportView(store).rows(parent['id'],defaults,preview['preview_hash'],identifier,'constructor','after',item['after_sha256'],offset=index,limit=1) for index in (0,1)]
    assert [page['items'][0]['row_index'] for page in pages]==[0,1]
    assert [page['items'][0]['values']['SEQ'] for page in pages]==['1','2']
    assert all(page['items'][0]['values']['__proto__']=='Literal own property' for page in pages)
    assert pages[0]['formats'][0]['fields']==['LIBRF','RTYPE','SEQ','DESCR','FUTURE_2512','__proto__']
    assert all(page['row_alignment']=='none' for page in pages)


def test_strict_admin_endpoints_and_exact_object_side_hash(view_pilot):
    client,store,parent,preview=view_pilot;base='/api/v1/releases/'+parent['id']+'/derive-preview/';data={'preview_hash':preview['preview_hash']}
    assert client.post(base+'objects',json=data).status_code==401
    assert client.post(base+'objects',headers=ERP_HEADERS,json=data).status_code==403
    assert client.post(base+'objects',headers=HEADERS,json={**data,'offset':True}).status_code==422
    report=client.post(base+'objects',headers=HEADERS,json=data).json();item=report['items'][0]
    rowdata={**data,'library_id':item['library_id'],'key':item['key'],'side':'after','group_sha256':item['after_sha256']}
    assert client.post(base+'rows',headers=HEADERS,json=rowdata).status_code==200
    assert client.post(base+'rows',headers=HEADERS,json={**rowdata,'group_sha256':'0'*64}).status_code==409
    assert client.post(base+'rows',headers=ERP_HEADERS,json=rowdata).status_code==403
    assert client.post(base+'rows',headers=HEADERS,json={**rowdata,'key':'missing','group_sha256':None}).status_code==404


@pytest.mark.parametrize('limit',['MAX_BYTES','MAX_ROWS','MAX_CELLS','MAX_PAGE_BYTES'])
def test_bounded_reader_never_returns_partial_pages(view_pilot,monkeypatch,limit):
    import nxserver.exportview as module
    _,store,parent,preview=view_pilot;monkeypatch.setattr(module,limit,1)
    with pytest.raises(DomainError):ExportView(store).objects(parent['id'],{},preview['preview_hash'])
    assert len(store.releases())==1

"""Whole planned export files/private attachments; isolated fake bytes only."""
import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.libraries import SPECS,digest,NativeTable,native_cam
from nxserver.exportfiles import ExportFiles
from nxserver.safety import DomainError
from test_filepackages import packages,body as package_body,derive
from test_graphics_bindings import context
from test_libraries import resource_body,write,simple
from test_management import ADMIN,ERP,HEADERS,ERP_HEADERS


def files_setup(tmp_path):
    store,_,resource,part,document,proposal=context(tmp_path)
    root=store.sources['source'];file=root/'keep.txt';file.write_bytes(b'Unchanged inherited fixture')
    write(root,SPECS['holder-metric']['path'],simple())
    parent=store.import_release('source','2512','File preview parent','fixture')
    value=resource_body(resource);value.update(cam_status='approved',asset_ids=proposal['asset_ids'],file_bindings=proposal['file_bindings'])
    resource=store.save_resource(value,'fixture',resource['id'],1)
    return store,parent,resource,part,document


def inspect(client,parent,report,**options):
    return client.post('/api/v1/releases/'+parent['id']+'/derive-preview/files',headers=HEADERS,
        json={'defaults':{},'preview_hash':report['preview_hash'],**options})


def test_file_preview_matches_complete_actual_export_manifest_and_never_writes(tmp_path):
    store,parent,resource,part,document=files_setup(tmp_path);report=store.preview_export(parent['id'])
    before=store.audit_entries();manifest=store.manifest(store.release_folder(parent['id']));releases=store.releases()
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        response=inspect(client,parent,report);assert response.status_code==200,response.text
        value=response.json();assert value['complete'] and value['scope']=='planned_release_files'
        assert value['counts']=={'created':1,'updated':1,'removed':0,'unchanged':1}
        assert value['private_attachments']==1 and value['summary']['before_files']==2 and value['summary']['after_files']==3
        assert value['read_only'] and not value['nx_files_written'] and not value['resources_written']
        private=inspect(client,parent,report,view='private').json()
        assert private['items'][0]['asset_id']==document['id'] and private['items'][0]['resource_id']==resource['id']
        assert private['items'][0]['reason']=='not_selected' and private['private_integrity_checked'] is False
    assert store.audit_entries()==before and store.manifest(store.release_folder(parent['id']))==manifest and store.releases()==releases
    child=store.import_release('','','Inspected copy','fixture',parent=parent['id'],preview_hash=report['preview_hash'])
    actual=store.manifest(store.release_folder(child['id']))
    assert digest(actual)==value['after_manifest_sha256']
    expected=[{'path':item['path'],**item['after']} for item in value['items'] if item['after'] is not None]
    assert sorted(expected,key=lambda item:item['path'])==actual


def test_case_only_graphics_rebinding_is_blocked_before_creating_new_folder(tmp_path):
    store,_,resource,part,document,proposal=context(tmp_path)
    write(store.sources['source'],SPECS['holder-metric']['path'],simple())
    relative=SPECS['holder-metric']['graphics_dir']+'/Part.prt'
    path=store.sources['source']/relative;path.parent.mkdir(parents=True);path.write_bytes(b'Same bytes')
    parent=store.import_release('source','2512','Case-safe parent','fixture')
    asset=store.add_asset('part.prt',b'Same bytes','fixture')
    body=resource_body(resource);body.update(cam_status='approved',asset_ids=[asset['id']])
    store.save_resource(body,'fixture',resource['id'],1);before=store.audit_entries();count=len(store.releases())
    report=store.preview_export(parent['id'])
    assert not report['can_export'] and any('casing' in item['reason'] for item in report['conflicts'])
    assert store.audit_entries()==before and len(store.releases())==count


def test_package_files_update_retirement_and_private_attachment_match_export(packages):
    client,store,parent,*_=packages
    payload=package_body(store);document=store.add_asset('Instructions.pdf',b'Private instructions','fixture')
    payload['asset_ids'].append(document['id']);resource=store.save_resource(payload,'fixture')
    report=store.preview_export(parent['id']);value=inspect(client,parent,report).json()
    assert next(item for item in value['items'] if item['change']=='created')['kind']=='file_package'
    private=inspect(client,parent,report,view='private').json()
    assert private['items'][0]['asset_id']==document['id'] and private['items'][0]['reason']=='not_selected'
    first=derive(store,parent['id']);assert digest(store.manifest(store.release_folder(first['id'])))==value['after_manifest_sha256']
    changed=package_body(store,content=b'Updated fake model');changed['asset_ids'].append(document['id'])
    store.save_resource(changed,'fixture',resource['id'],1)
    report=store.preview_export(first['id']);value=inspect(client,first,report,change='updated').json()
    assert len(value['items'])==1 and value['items'][0]['path']=='UGII/templates/CUSTOM.prt'
    second=derive(store,first['id']);assert digest(store.manifest(store.release_folder(second['id'])))==value['after_manifest_sha256']
    changed['cam_status']='retired';store.save_resource(changed,'fixture',resource['id'],2)
    report=store.preview_export(second['id']);value=inspect(client,second,report,change='removed').json()
    row=value['items'][0];assert row['before'] and row['after'] is None and row['resources'][0]['id']==resource['id']
    assert all(item['reason']=='retired' for item in inspect(client,second,report,view='private').json()['items'])
    third=derive(store,second['id']);assert digest(store.manifest(store.release_folder(third['id'])))==value['after_manifest_sha256']


def test_draft_does_not_remove_inherited_graphic_and_private_rows_do_not_claim_integrity(tmp_path):
    store,parent,resource,part,document=files_setup(tmp_path);report=store.preview_export(parent['id'])
    child=store.import_release('','','First export','fixture',parent=parent['id'],preview_hash=report['preview_hash'])
    body=resource_body(resource);body['cam_status']='draft';store.save_resource(body,'fixture',resource['id'],2)
    # Only this isolated fixture loses a private file; a metadata reader must not claim fresh byte integrity.
    store.asset(document['id'])[1].unlink();report=store.preview_export(child['id'])
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        response=inspect(client,child,report,view='private');assert response.status_code==200,response.text
        value=response.json();assert len(value['items'])==2 and all(item['reason']=='draft' for item in value['items'])
        assert value['private_integrity_checked'] is False
        rows=inspect(client,child,report).json()['items']
        assert all(item['change']=='unchanged' for item in rows) and any(item['path'].endswith('.prt') for item in rows)


def test_search_pages_include_inherited_files_and_private_metadata(tmp_path):
    store,parent,resource,part,document=files_setup(tmp_path)
    for index in range(105):(store.sources['source']/('Ä inherited '+str(index)+'.txt')).write_bytes(str(index).encode())
    parent=store.import_release('source','2512','Large fixture','fixture');report=store.preview_export(parent['id'])
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        first=inspect(client,parent,report,change='unchanged',query='ä INHERITED').json()
        second=inspect(client,parent,report,change='unchanged',query='ä INHERITED',offset=50).json()
        last=inspect(client,parent,report,change='unchanged',query='ä INHERITED',offset=100).json()
        assert [len(value['items']) for value in (first,second,last)]==[50,50,5]
        assert first['filtered']==105 and len({item['path'] for value in (first,second,last) for item in value['items']})==105
        assert inspect(client,parent,report,query=resource['name']).json()['filtered']==2
        assert inspect(client,parent,report,view='private',query=document['sha256']).json()['filtered']==1
        assert inspect(client,parent,report,change='changed').json()['filtered']==2


@pytest.mark.parametrize('case',['parent','catalog','defaults'])
def test_stale_export_context_fails_closed_without_creating_folder(tmp_path,case):
    store,parent,resource,*_=files_setup(tmp_path);report=store.preview_export(parent['id']);options={}
    if case=='parent':(store.release_folder(parent['id'])/'keep.txt').write_bytes(b'Changed fixture')
    elif case=='catalog':
        body=resource_body(resource);body['name']='New name';store.save_resource(body,'fixture',resource['id'],2)
    else:options['defaults']={'holder-metric':{'B1':{'DESCR':'Different default'}}}
    before=store.audit_entries();count=len(store.releases())
    with TestClient(create_app(store,ADMIN,ERP)) as client:assert inspect(client,parent,report,**options).status_code in (400,409)
    assert store.audit_entries()==before and len(store.releases())==count


@pytest.mark.parametrize('options',[{'limit':101},{'offset':-1},{'view':'bad'},{'change':'bad'},{'view':'private','change':'created'},{'query':'x'*201},{'preview_hash':'invalid'},{'offset':True}])
def test_invalid_reader_input_and_roles_do_not_write(tmp_path,options):
    store,parent,*_=files_setup(tmp_path);report=store.preview_export(parent['id']);before=store.audit_entries()
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        endpoint='/api/v1/releases/'+parent['id']+'/derive-preview/files';body={'defaults':{},'preview_hash':report['preview_hash']}
        assert client.post(endpoint,json=body).status_code==401
        assert client.post(endpoint,headers=ERP_HEADERS,json=body).status_code==403
        assert inspect(client,parent,report,**options).status_code in (400,422)
    assert store.audit_entries()==before


@pytest.mark.parametrize('name,bound',[('MAX_FILES',1),('MAX_ATTACHMENTS',1),('MAX_PAGE_BYTES',1)])
def test_reader_bounds_never_return_partial_success(tmp_path,monkeypatch,name,bound):
    from nxserver import exportfiles
    store,parent,*_=files_setup(tmp_path);report=store.preview_export(parent['id']);before=store.audit_entries()
    monkeypatch.setattr(exportfiles,name,bound)
    with pytest.raises(DomainError):ExportFiles(store).inspect(parent['id'],{},report['preview_hash'])
    assert store.audit_entries()==before


def test_missing_private_sql_metadata_fails_closed(tmp_path):
    store,parent,resource,part,document=files_setup(tmp_path);report=store.preview_export(parent['id'])
    with store.db() as db:db.execute('DELETE FROM assets WHERE id=?',(document['id'],))
    # Draft suppresses physical export validation; the reader must still reject missing attachment metadata.
    body=resource_body(resource);body['cam_status']='draft'
    with store.db() as db:
        from nxserver.store import canonical
        db.execute('UPDATE resources SET payload=? WHERE id=?',(canonical(body),resource['id']))
    report=store.preview_export(parent['id'])
    with pytest.raises(DomainError,match='metadata is missing'):ExportFiles(store).inspect(parent['id'],{},report['preview_hash'])


def test_shared_file_owners_are_bounded_but_search_covers_all_and_no_false_private_rows(tmp_path):
    store,parent,resource,part,document=files_setup(tmp_path)
    for index in range(12):
        body=resource_body(resource);body['name']='Shared owner '+str(index)
        body['file_bindings']=resource['file_bindings']
        table=NativeTable('#CLASS SAMPLE\nFORMAT LIBRF DESCR\nDATA |SHARED'+str(index)+'|Shared fixture\n#END_DATA\n')
        body['cam']=native_cam('holder-metric','2512',table,table.records);store.save_resource(body,'fixture')
    report=store.preview_export(parent['id'])
    with TestClient(create_app(store,ADMIN,ERP)) as client:
        value=inspect(client,parent,report,query='.prt').json();row=value['items'][0]
        assert row['resource_count']==13 and len(row['resources'])==10 and row['owners_complete'] is False
        hidden=next(value for value in store.resources() if value['id'] not in {owner['id'] for owner in row['resources']})
        assert inspect(client,parent,report,query=hidden['name']).json()['filtered']==2
        private=inspect(client,parent,report,view='private').json();assert private['total']==13
        assert all(item['asset_id']==document['id'] for item in private['items'])


@pytest.mark.parametrize('change',[{'view':[]},{'offset':True},{'limit':0},{'query':None},{'preview_hash':'x'}])
def test_direct_reader_rejects_unbounded_or_wrong_shapes(tmp_path,change):
    store,parent,*_=files_setup(tmp_path);report=store.preview_export(parent['id'])
    with pytest.raises(DomainError):ExportFiles(store).inspect(parent['id'],{},**{'preview_hash':report['preview_hash'],**change})


def test_case_only_directory_alias_is_blocked_even_for_different_graphics_names(tmp_path):
    store,parent,resource,part,document=files_setup(tmp_path)
    relative=SPECS['holder-metric']['graphics_dir']+'/Sub/Old.prt'
    source=store.sources['source']/relative;source.parent.mkdir(parents=True);source.write_bytes(b'Old synthetic model')
    parent=store.import_release('source','2512','Directory-safe parent','fixture')
    report=store.preview_export(parent['id'])
    assert not report['can_export'] and any('casing' in item['reason'] for item in report['conflicts'])


@pytest.mark.parametrize('paths',[['a/B/one.prt','a/b/two.prt'],['a/part.prt','a/part.prt/other.prt'],['A.txt','a.txt']])
def test_portable_plan_casing_is_pure_and_catches_directory_prefixes(paths):
    from nxserver.safety import export_path_casing
    with pytest.raises(DomainError,match='not portable'):export_path_casing(paths)

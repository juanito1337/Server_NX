"""NX2406 KRX/file lifecycle on own synthetic bytes, never NX or production data."""
import copy
from pathlib import Path
import pytest

from nxserver.fixture_refs import references,validate_package,verify
from nxserver.filepackages import CHECKS,FilePackages,path_for,preflight
from nxserver.safety import DomainError
from nxserver.store import Store,TOOL_DATABASE
from nxserver.integrity import scan
from test_management import DATABASE
from test_upgrades import upgrade_pilot

ROOT='MACH/resource/library/fixture/CustomerVise/'
KRX=(b'<RootItemCollection><RootItem Name="vise" DisplayName="Synthetic vise" Icon="vise.bmp" '
     b'RootPart="/graphics/vise.prt" Folder="vise" Subtype="Vise" /></RootItemCollection>')
FILES={ROOT+'FixtureConf.krx':KRX,ROOT+'vise/vise.bmp':b'BM synthetic icon; not a graphical qualification',
       ROOT+'vise/graphics/vise.prt':b'Synthetic assembly bytes; NOT an NX model',
       ROOT+'vise/graphics/jaw.prt':b'Synthetic component bytes; NOT an NX model'}


@pytest.fixture
def fixture_store(tmp_path):
    source=tmp_path/'source';(source/TOOL_DATABASE).parent.mkdir(parents=True)
    (source/TOOL_DATABASE).write_text(DATABASE)
    standard=tmp_path/'standard';(standard/'MACH/resource/library/fixture').mkdir(parents=True)
    store=Store(tmp_path/'managed',{'local':source},standards={'2406':standard})
    release=store.import_release('local','2406','Synthetic local source','fixture')
    return store,release,source,standard


def package(store,files=None,status='draft'):
    assets=[];entries=[]
    for path,content in (FILES if files is None else files).items():
        asset=store.add_asset(Path(path).name,content,'fixture')
        assets.append(asset);entries.append({'path':path,'asset_id':asset['id']})
    return {'kind':'fixture','name':'Synthetic NX2406 vise','cam':{},'erp':None,'asset_ids':[a['id'] for a in assets],
        'cam_status':status,'file_package':{'nx_version':'2406','entries':entries,
            'checks':{name:True for name in CHECKS} if status=='approved' else {},
            'evidence':'Synthetic checks only, not actual NX/CAM qualification' if status=='approved' else '',
            'asset_hashes':{a['id']:a['sha256'] for a in assets} if status=='approved' else {}}}


def derive(store,release):
    preview=store.preview_export(release['id']);assert preview['can_export'],preview['conflicts']
    return store.import_release('','','Synthetic export','fixture',parent=release['id'],preview_hash=preview['preview_hash'])


def test_declared_rootpart_and_icon_resolve_only_inside_the_explicit_folder():
    refs=references(KRX,ROOT+'FixtureConf.krx')
    assert [r['target'] for r in refs]==[ROOT+'vise/graphics/vise.prt',ROOT+'vise/vise.bmp']
    report=validate_package([{'path':p} for p in FILES],{ROOT+'FixtureConf.krx':KRX})
    assert not report['binary_prt_dependencies_verified'] and not report['nx_execution_verified']


@pytest.mark.parametrize('field,value',[
    ('RootPart','W:/production/model.prt'),('RootPart','//host/share/model.prt'),
    ('RootPart','/graphics/../../outside.prt'),('RootPart','/graphics/%UGII_USER_DIR%/model.prt'),
    ('RootPart','/graphics/model.py'),('Folder','../outside'),('Folder','W:relative'),('Folder','%UGII_BASE_DIR%'),
    ('Icon','//host/share/icon.bmp'),('Icon','../image.bmp'),('Icon','launch.exe'),('Icon','${ICON}.bmp')])
def test_external_traversal_and_implicit_krx_references_are_rejected_before_lookup(monkeypatch,field,value):
    original={'RootPart':'/graphics/vise.prt','Folder':'vise','Icon':'vise.bmp'}[field]
    raw=KRX.replace((field+'="'+original+'"').encode(),(field+'="'+value+'"').encode())
    monkeypatch.setattr(Path,'open',lambda *a,**k:pytest.fail('KRX references must be validated without file access.'))
    monkeypatch.setattr(Path,'stat',lambda *a,**k:pytest.fail('KRX reference may not reach stat.'))
    with pytest.raises(DomainError):references(raw,ROOT+'FixtureConf.krx')


@pytest.mark.parametrize('raw',[
    b'<wrong/>',b'<RootItemCollection/>',b'<!DOCTYPE RootItemCollection SYSTEM "W:/never.dtd"><RootItemCollection/>',
    KRX.replace(b'Name="vise"',b'ExtraPath="W:/never" Name="vise"'),
    KRX.replace(b'<RootItemCollection>',b'<RootItemCollection>unexpected text'),
    KRX.replace(b'</RootItemCollection>',KRX.split(b'<RootItemCollection>')[1]),
])
def test_invalid_extended_and_duplicate_descriptors_are_not_accepted(raw):
    with pytest.raises(DomainError):references(raw,ROOT+'FixtureConf.krx')


@pytest.mark.parametrize('version,kind',[('2512','fixture'),('2606','fixture'),('2406','machine'),('2406','template')])
def test_legacy_layout_is_not_silently_allowed_for_other_versions_or_kinds(version,kind):
    with pytest.raises(DomainError):path_for(kind,ROOT+'FixtureConf.krx',version)


@pytest.mark.parametrize('missing',[ROOT+'FixtureConf.krx',ROOT+'vise/vise.bmp',ROOT+'vise/graphics/vise.prt'])
def test_incomplete_legacy_package_cannot_be_bound_or_saved(fixture_store,missing):
    store,*_=fixture_store;body=package(store,{p:b for p,b in FILES.items() if p!=missing})
    report=preflight(store,{'kind':body['kind'],'nx_version':'2406','entries':body['file_package']['entries'],'asset_ids':body['asset_ids']})
    assert not report['can_bind'] and any(error['code']=='fixture_reference' for error in report['errors'])
    with pytest.raises(DomainError):store.save_resource(body,'fixture')
    assert store.resources()==[]


def test_krx_fixture_create_export_retire_preserves_historical_files_and_stock(fixture_store):
    store,release,source,standard=fixture_store;body=package(store,status='approved')
    before=store.manifest(store.release_folder(release['id']))
    resource=store.save_resource(body,'fixture');first=derive(store,release)
    first_files=store.manifest(store.release_folder(first['id']))
    for path,content in FILES.items():assert (store.release_folder(first['id'])/path).read_bytes()==content
    retired=store.save_resource({**body,'cam_status':'retired'},'fixture',resource['id'],1)
    second=derive(store,first)
    assert all(not (store.release_folder(second['id'])/p).exists() for p in FILES)
    assert store.manifest(store.release_folder(first['id']))==first_files
    assert store.manifest(store.release_folder(release['id']))==before
    assert store.active()['release_id'] is None and retired['revision']==2
    assert scan(store)['error_count']==0


def test_unmanaged_krx_cannot_be_left_dangling_when_owned_models_are_retired(fixture_store):
    store,release,source,standard=fixture_store
    watcher=source/(ROOT+'Other.krx');watcher.parent.mkdir(parents=True);watcher.write_bytes(KRX)
    release=store.import_release('local','2406','Unmanaged descriptor referencing a future customer model','fixture')
    body=package(store,status='approved');resource=store.save_resource(body,'fixture');first=derive(store,release)
    store.save_resource({**body,'cam_status':'retired'},'fixture',resource['id'],1)
    report=store.preview_export(first['id'])
    assert not report['can_export'] and report['fixture_references']['errors']
    assert 'missing' in report['fixture_references']['errors'][0]['reason']
    assert all((store.release_folder(first['id'])/p).is_file() for p in FILES)


def test_final_krx_byte_gate_rejects_tampering_in_the_derived_copy(fixture_store):
    store,release,*_=fixture_store;store.save_resource(package(store,status='approved'),'fixture')
    first=derive(store,release);report=store.preview_export(first['id'])['fixture_references']
    path=store.release_folder(first['id'])/(ROOT+'vise/graphics/vise.prt')
    path.write_bytes(b'Test-owned corruption')
    with pytest.raises(DomainError):verify(store.release_folder(first['id']),report)


def test_customer_krx_adoption_needs_no_invented_dat_record(fixture_store):
    store,release,source,standard=fixture_store
    for path,content in FILES.items():
        dest=source/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(content)
    release=store.import_release('local','2406','Existing synthetic customer fixture','fixture')
    manager=FilePackages(store);paths=list(FILES)
    inventory=manager.inventory(release['id'],'fixture')
    assert set(item['path'] for item in inventory['files'])==set(paths)
    preview,_=manager.adoption_preview(release['id'],'fixture',paths)
    assert preview['can_adopt'],preview['conflicts']
    resource=manager.adopt(release['id'],'fixture',paths,'Customer vise',None,preview['preview_hash'],'fixture')
    assert resource['kind']=='fixture' and resource['cam']=={} and resource['cam_status']=='draft'
    assert len(resource['asset_ids'])==len(FILES) and resource['file_package']['provenance']
    assert store.active()['release_id'] is None and scan(store)['error_count']==0


@pytest.mark.parametrize('path',[ROOT+'FixtureConf.krx',ROOT+'vise/graphics/vise.prt',ROOT+'vise/vise.bmp'])
def test_final_export_transaction_rejects_changed_krx_and_target_bytes(fixture_store,monkeypatch,path):
    store,release,*_=fixture_store;store.save_resource(package(store,status='approved'),'fixture')
    report=store.preview_export(release['id']);original=store._create_test_launcher
    before=store.manifest(store.release_folder(release['id']))
    def corrupt(identifier,version,folder,manifest,**kwargs):
        (folder/path).write_bytes(b'Synthetic late corruption in owned derived copy')
        return original(identifier,version,folder,manifest,**kwargs)
    monkeypatch.setattr(store,'_create_test_launcher',corrupt)
    with pytest.raises(DomainError,match='KRX dependency'):
        store.import_release('','','Must not commit','fixture',parent=release['id'],preview_hash=report['preview_hash'])
    assert store.manifest(store.release_folder(release['id']))==before
    assert all(r['status']=='failed' for r in store.releases() if r['parent_id']==release['id'])
    assert store.active()['release_id'] is None


def test_2406_krx_upgrade_retains_bytes_and_ownership_but_never_new_version_qualification(upgrade_pilot):
    from test_upgrades import preview,finish
    from test_management import HEADERS
    client,store,request,source=upgrade_pilot
    old=store.import_release('source','2406','Synthetic old fixture base','fixture')
    resource=store.save_resource(package(store,status='approved'),'fixture')
    tracked=derive(store,old);before=store.manifest(store.release_folder(tracked['id']))
    history=store.history(resource['id'])
    request.update(source=None,source_path=None,source_release_id=tracked['id'])
    job,plan=preview(client,request);assert plan['can_build'],plan['conflicts']
    result=finish(client,client.post('/api/v1/migrations/build',headers=HEADERS,json={
        **request,'preview_id':job['id'],'preview_hash':plan['preview_hash']}))
    assert result['status']=='completed',result
    migrated=store.get_release(result['release_id'])
    ownership=migrated['catalog']['exported_file_packages'][resource['id']]
    assert ownership['qualification_retained'] is False
    for path,raw in FILES.items():assert (store.release_folder(migrated['id'])/path).read_bytes()==raw
    assert store.manifest(store.release_folder(tracked['id']))==before and store.history(resource['id'])==history
    assert not store.preview_export(migrated['id'])['can_export']
    body=package(store);body['file_package'].update(nx_version='2512')
    with pytest.raises(DomainError):store.save_resource(body,'fixture',resource['id'],1)
    assert store.resources()[0]['revision']==1 and store.active()['release_id'] is None


def test_krx_report_bound_blocks_export_instead_of_certifying_a_partial_graph(fixture_store,monkeypatch):
    store,release,*_=fixture_store;store.save_resource(package(store,status='approved'),'fixture')
    monkeypatch.setattr('nxserver.fixture_refs.MAX_REFERENCES',1)
    report=store.preview_export(release['id'])
    assert not report['can_export'] and not report['fixture_references']['complete']
    assert report['fixture_references']['references']==[] and report['fixture_references']['errors']
    assert len(store.releases())==1 and store.active()['release_id'] is None


def test_krx_descriptor_byte_bound_is_checked_before_xml_parsing(monkeypatch):
    from nxserver import fixture_refs
    monkeypatch.setattr(fixture_refs,'parse_xml',lambda *a,**k:pytest.fail('Oversize descriptor reached XML parser.'))
    with pytest.raises(DomainError,match='1 MiB'):
        references(b'x'*(fixture_refs.MAX_DESCRIPTOR_BYTES+1),ROOT+'FixtureConf.krx')


def test_imported_dangling_krx_is_blocked_by_release_validation_not_only_export(fixture_store):
    store,release,source,*_=fixture_store
    path=source/(ROOT+'FixtureConf.krx');path.parent.mkdir(parents=True);path.write_bytes(KRX)
    imported=store.import_release('local','2406','Dangling fixture source','fixture')
    result=store.validate_release(imported['id'],'fixture')
    assert not result['integrity_passed'] and not result['fixture_references']['complete']
    assert store.get_release(imported['id'])['status']=='blocked'
    assert any('KRX fixture reference' in reason for reason in result['errors'])
    assert store.active()['release_id'] is None


def test_valid_krx_release_validation_still_requires_functional_nx_acceptance(fixture_store):
    store,release,*_=fixture_store;store.save_resource(package(store,status='approved'),'fixture')
    exported=derive(store,release)
    result=store.validate_release(exported['id'],'fixture')
    assert result['integrity_passed'] and result['functional_validation_required']
    assert result['fixture_references']['complete'] and not result['fixture_references']['nx_execution_verified']
    assert store.get_release(exported['id'])['status']=='validated' and store.active()['release_id'] is None


@pytest.mark.parametrize('root',[ROOT.replace('MACH','mach'),ROOT.replace('/fixture/','/FiXtUrE/')])
def test_imported_root_case_variant_cannot_bypass_krx_validation(tmp_path,root):
    source=tmp_path/'case-source'
    for name,content in FILES.items():
        path=source/(root+name[len(ROOT):]);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
    store=Store(tmp_path/'case-managed',{'case':source})
    release=store.import_release('case','2406','Noncanonical fixture root','fixture')
    result=store.validate_release(release['id'],'fixture')
    assert not result['integrity_passed'] and not result['fixture_references']['complete']
    assert result['fixture_references']['errors'] and store.active()['release_id'] is None

"""Prepared packet contracts: synthetic PRT bytes do not prove actual CAD/CAM."""
import copy
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.geometry import GeometryJobs
from nxserver.integrity import scan
from nxserver.libraries import SPECS,NativeTable,native_cam
from nxserver.results import Results,parse_package
from nxserver.store import Store
from test_geometry import STEP
from test_libraries import write,resource_body

ADMIN='isolated-result-admin-'+'a'*40
ERP='isolated-result-erp-'+'e'*40
HEADERS={'Authorization':'Bearer '+ADMIN}


def table(fields,rows):return '#CLASS SAMPLE\nFORMAT '+' '.join(fields)+'\n'+''.join('DATA | '+' | '.join(row)+'\n' for row in rows)+'#END_DATA\n'


def bundle(job,changes=None):
    contents={'CUSTOM.prt':b'Synthetic root, not NX CAD','sub/holder (1)#.prt':b'Synthetic component, not NX CAD'}
    root=NativeTable(table(['LIBRF','DESCR','HLDREF'],[('CUSTOM','Prepared','HLD_CUSTOM')]))
    holder=NativeTable(table(['LIBRF','DESCR'],[('HLD_CUSTOM','Related holder')]))
    value={'contract_version':1,'job_id':job['id'],'resource_id':job['resource_id'],'resource_revision':job['resource_revision'],
        'resource_hash':job['resource_hash'],'input_sha256':job['input']['sha256'],'worker_hashes':job['worker_hashes'],
        'nx_version':'2512','tool_type':job['tool_type'],'automatic_cam_approval':False,
        'root_library_id':'tool-metric','root_key':'CUSTOM','root_part':'CUSTOM.prt',
        'objects':[{'cam':native_cam('tool-metric','2512',root,root.records)},
                   {'cam':native_cam('holder-metric','2512',holder,holder.records)}],
        'parts':[{'path':name,'units':'Millimeters','fully_loaded':True,'bodies':[{'id':'B1','name':'Synthetic',
            'solid':True,'convergent':False,'face_count':6,'layer':1,'color':42}]} for name in contents],
        'components':[{'id':'/0','name':'Synthetic holder occurrence','part':'sub/holder (1)#.prt'}],
        'files':[{'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in contents.items()],
        'collector':'Synthetic inventory, not actual NX proof'}
    if changes:changes(value,contents)
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json',json.dumps(value).encode())
        for name,data in contents.items():z.writestr('files/'+name,data)
    return output.getvalue(),value


@pytest.fixture
def result_pilot(tmp_path):
    source=tmp_path/'source'
    write(source,SPECS['tool-metric']['path'],table(['LIBRF','DESCR','HLDREF'],[('STD','Stock','')]))
    write(source,SPECS['holder-metric']['path'],table(['LIBRF','DESCR'],[('STDH','Stock holder')]))
    store=Store(tmp_path/'managed',{'test':source})
    release=store.import_release('test','2512','Target fixture','fixture')
    asset=store.add_asset('input.step',STEP,'fixture')
    resource=store.save_resource({'kind':'tool','name':'Draft','erp':None,'cam':{},'cam_status':'draft',
        'asset_ids':[asset['id']],'geometry':{'mode':'step','status':'needs_preparation'},'origin':None},'fixture')
    job=GeometryJobs(store).create(resource['id'],1,asset['id'],'drilling_solid','fixture')
    with TestClient(create_app(store,ADMIN,ERP)) as client:yield client,store,release,resource,job


def upload(client,job,content):
    response=client.post('/api/v1/geometry-jobs/'+job['id']+'/prepared-result',headers={**HEADERS,'Content-Type':'application/zip'},content=content)
    return response


def test_complete_related_package_import_new_drafts_and_nested_graphics_export(result_pilot):
    client,store,release,resource,job=result_pilot;content,manifest=bundle(job);old=store.manifest(store.release_folder(release['id']))
    response=upload(client,job,content);assert response.status_code==201,response.text
    result=response.json();assert store.resources()[0]==resource
    url='/api/v1/geometry-results/'+result['id'];body={'target_release_id':release['id']}
    preview=client.post(url+'/preview',headers=HEADERS,json=body)
    assert preview.status_code==200,preview.text
    report=preview.json();assert report['can_apply'],report['conflicts']
    response=client.post(url+'/apply',headers=HEADERS,json={**body,'preview_hash':report['preview_hash']})
    assert response.status_code==200,response.text
    assert len(store.resources())==2
    parent=next(r for r in store.resources() if r['id']==resource['id']);holder=next(r for r in store.resources() if r['kind']=='holder')
    assert parent['revision']==2 and parent['cam_status']==holder['cam_status']=='draft'
    assert parent['erp']==resource['erp'] and not any(parent['geometry']['checks'].values())
    assert parent['model_package']['trust']=='unverified_external_nx_inventory'
    assert parent['file_bindings'][1]['path'].endswith('sub/holder (1)#.prt')
    assert scan(store)['error_count']==0
    # Explicit synthetic manual checks exercise the contract, not a real tool.
    for value in (parent,holder):
        payload=resource_body(value);payload['file_bindings']=value.get('file_bindings',[]);payload['model_package']=value.get('model_package')
        payload['cam_status']='approved'
        if value['kind']=='tool':
            payload['geometry']={**parent['geometry'],'status':'validated','checks':{key:True for key in parent['geometry']['checks']},
                'evidence':'Synthetic checks for contract test, NOT real NX approval',
                'asset_hashes':{key:store.asset(key)[0]['sha256'] for key in parent['asset_ids']}}
        response=client.put('/api/v1/resources/'+value['id'],headers=HEADERS,json={**payload,'expected_revision':value['revision']})
        assert response.status_code==200,response.text
    plan=store.preview_export(release['id']);assert plan['can_export'],plan['conflicts']
    exported=store.import_release('','','Export synthetic prepared package','fixture',parent=release['id'],preview_hash=plan['preview_hash'])
    root=Path(exported['folder'])/SPECS['tool-metric']['graphics_dir']
    assert (root/'CUSTOM.prt').read_bytes()==b'Synthetic root, not NX CAD'
    assert (root/'sub/holder (1)#.prt').read_bytes()==b'Synthetic component, not NX CAD'
    assert not (root/'input.step').exists()
    assert store.manifest(store.release_folder(release['id']))==old and store.active()['release_id'] is None


@pytest.mark.parametrize('mutation',['version_bool','approval_int','approval_true','revision','hash','input','worker','kind',
    'root_name','missing_part','part_unloaded','duplicate_body','component_parent','cycle','unrelated_object','file_sha','file_size',
    'traversal','absolute','reserved','case_collision','unlisted','empty','nonprt'])
def test_malformed_untrusted_packets_do_not_create_assets_or_change_resources(result_pilot,mutation):
    client,store,release,resource,job=result_pilot
    def mutate(value,contents):
        if mutation=='version_bool':value['contract_version']=True
        elif mutation=='approval_int':value['automatic_cam_approval']=0
        elif mutation=='approval_true':value['automatic_cam_approval']=True
        elif mutation=='revision':value['resource_revision']+=1
        elif mutation=='hash':value['resource_hash']='0'*64
        elif mutation=='input':value['input_sha256']='0'*64
        elif mutation=='worker':value['worker_hashes']={}
        elif mutation=='kind':value['root_library_id']='machine'
        elif mutation=='root_name':value['root_part']='renamed.prt'
        elif mutation=='missing_part':value['parts'].pop()
        elif mutation=='part_unloaded':value['parts'][0]['fully_loaded']=False
        elif mutation=='duplicate_body':value['parts'][0]['bodies']*=2
        elif mutation=='component_parent':value['components'][0]['id']='/0/0'
        elif mutation=='cycle':value['components'][0]['part']='CUSTOM.prt'
        elif mutation=='unrelated_object':value['objects'][1]['cam']['records'][0]['values']['LIBRF']='UNRELATED'
        elif mutation=='file_sha':value['files'][0]['sha256']='0'*64
        elif mutation=='file_size':value['files'][0]['size']+=1
        elif mutation in ('traversal','absolute','reserved','nonprt'):
            value['files'][0]['path']={'traversal':'../bad.prt','absolute':'W:/bad.prt','reserved':'CON.prt','nonprt':'script.exe'}[mutation]
        elif mutation=='case_collision':contents['custom.PRT']=b'Collision'
        elif mutation=='unlisted':contents['extra.prt']=b'Extra'
        else:value['files'][0]['size']=0
    content,_=bundle(job,mutate)
    response=upload(client,job,content);assert response.status_code in (400,409),response.text
    assert store.resources()==[resource] and Results(store).list(job['id'])==[]
    with store.db() as db:assert db.execute('SELECT count(*) FROM assets').fetchone()[0]==1


def test_changed_resource_or_erp_revision_and_changed_preview_block_application(result_pilot):
    client,store,release,resource,job=result_pilot;content,_=bundle(job);result=upload(client,job,content).json()
    url='/api/v1/geometry-results/'+result['id'];body={'target_release_id':release['id']}
    report=client.post(url+'/preview',headers=HEADERS,json=body).json()
    assert client.post(url+'/apply',headers=HEADERS,json={**body,'preview_hash':'0'*64}).status_code==409
    payload=resource_body(resource);payload['name']='Newer resource'
    store.save_resource(payload,'fixture',resource['id'],1)
    assert client.post(url+'/apply',headers=HEADERS,json={**body,'preview_hash':report['preview_hash']}).status_code==409
    assert len(store.resources())==1


def test_result_upload_and_application_retry_are_idempotent_and_provenance_is_not_forgeable(result_pilot):
    client,store,release,resource,job=result_pilot;content,_=bundle(job)
    first=upload(client,job,content).json();assert upload(client,job,content).json()['id']==first['id']
    report=Results(store).preview(first['id'],release['id'])[0]
    result=Results(store).apply(first['id'],release['id'],report['preview_hash'],'fixture')
    assert Results(store).apply(first['id'],release['id'],report['preview_hash'],'fixture')==result
    assert upload(client,job,content).json()['id']==first['id']
    parent=next(r for r in store.resources() if r['id']==resource['id']);payload=resource_body(parent)
    payload['file_bindings']=parent['file_bindings'];payload['model_package']={**parent['model_package'],'archive_sha256':'0'*64}
    response=client.put('/api/v1/resources/'+parent['id'],headers=HEADERS,json={**payload,'expected_revision':2})
    assert response.status_code==400
    payload['model_package']=parent['model_package'];payload['cam']['records'][0]['values']['LIBRF']='RENAMED'
    assert client.put('/api/v1/resources/'+parent['id'],headers=HEADERS,json={**payload,'expected_revision':2}).status_code==400


def test_roles_content_type_and_bound_collector_helpers(result_pilot):
    client,store,release,resource,job=result_pilot;content,_=bundle(job);url='/api/v1/geometry-jobs/'+job['id']
    assert client.post(url+'/prepared-result',headers={'Content-Type':'application/zip'},content=content).status_code==401
    assert client.post(url+'/prepared-result',headers={'Authorization':'Bearer '+ERP,'Content-Type':'application/zip'},content=content).status_code==403
    assert client.post(url+'/prepared-result',headers=HEADERS,content=content).status_code==400
    response=client.post(url+'/collector-package',headers=HEADERS,json={'root_library_id':'tool-metric','root_key':'CUSTOM'})
    assert response.status_code==200,response.text
    assert hashlib.sha256(response.content).hexdigest()==response.headers['X-Content-SHA256']
    with zipfile.ZipFile(io.BytesIO(response.content)) as package:
        configuration=json.loads(package.read('collector.json'))
        assert all(hashlib.sha256(package.read(name)).hexdigest()==value for name,value in configuration['helper_hashes'].items())
        assert configuration['job']['resource_hash']==job['resource_hash']
        assert ADMIN.encode() not in response.content
    assert scan(store)['error_count']==0


@pytest.mark.parametrize('case',['related_differs','missing_library','graphic_collision','root_owned_elsewhere','standard_root'])
def test_target_and_ownership_conflicts_do_not_overwrite_existing_data(result_pilot,case):
    client,store,release,resource,job=result_pilot
    if case=='related_differs':
        holder=NativeTable(table(['LIBRF','DESCR'],[('HLD_CUSTOM','Different catalog value')]))
        store.save_resource({'kind':'holder','name':'Existing holder','cam':native_cam('holder-metric','2512',holder,holder.records),
            'cam_status':'draft','asset_ids':[],'erp':None},'fixture')
    elif case=='root_owned_elsewhere':
        root=NativeTable(table(['LIBRF','DESCR','HLDREF'],[('CUSTOM','Other catalog owner','')]))
        store.save_resource({'kind':'tool','name':'Existing tool','cam':native_cam('tool-metric','2512',root,root.records),
            'cam_status':'draft','asset_ids':[],'erp':None},'fixture')
    elif case in ('missing_library','graphic_collision'):
        # A separate target built before creating the preparation job is valid.
        folder=store.release_folder(release['id'])
        if case=='missing_library':(folder/SPECS['holder-metric']['path']).unlink()
        else:write(folder,SPECS['tool-metric']['graphics_dir']+'/CUSTOM.prt','Existing protected geometry')
        with store.db() as db:
            from nxserver.store import canonical
            db.execute('UPDATE releases SET manifest=? WHERE id=?',(canonical(store.manifest(folder)),release['id']))
            store.audit(db,'fixture','release.imported',{'id':release['id']})
    else:
        def stock(value,files):
            value['root_key']='STD';value['root_part']='STD.prt';value['objects'][0]['cam']['records'][0]['values']['LIBRF']='STD'
            files['STD.prt']=files.pop('CUSTOM.prt');value['files'][0]['path']='STD.prt';value['parts'][0]['path']='STD.prt'
        content,_=bundle(job,stock)
    if case!='standard_root':content,_=bundle(job)
    saved=Results(store).upload(job['id'],content,'fixture');before=copy.deepcopy(store.resources())
    report=Results(store).preview(saved['id'],release['id'])[0]
    assert not report['can_apply'] and report['conflicts']
    with pytest.raises(Exception):Results(store).apply(saved['id'],release['id'],report['preview_hash'],'fixture')
    assert store.resources()==before and Results(store).get(saved['id'])['applied'] is None


def test_exact_existing_related_definition_is_reused_without_revising_it(result_pilot):
    client,store,release,resource,job=result_pilot;content,manifest=bundle(job)
    holder=store.save_resource({'kind':'holder','name':'Existing holder','cam':manifest['objects'][1]['cam'],
        'cam_status':'draft','asset_ids':[],'erp':None},'fixture')
    result=Results(store).upload(job['id'],content,'fixture');report=Results(store).preview(result['id'],release['id'])[0]
    assert report['can_apply'] and report['actions'][1]['mode']=='reuse_catalog'
    applied=Results(store).apply(result['id'],release['id'],report['preview_hash'],'fixture')
    assert len(applied['resources'])==1 and next(r for r in store.resources() if r['id']==holder['id'])==holder


def test_mutated_archive_and_target_schema_do_not_apply_old_preview(result_pilot):
    client,store,release,resource,job=result_pilot;content,_=bundle(job);result=Results(store).upload(job['id'],content,'fixture')
    report=Results(store).preview(result['id'],release['id'])[0]
    source=store.release_folder(release['id'])/SPECS['tool-metric']['path'];source.write_bytes(source.read_bytes()+b'# Changed\n')
    with pytest.raises(Exception):Results(store).apply(result['id'],release['id'],report['preview_hash'],'fixture')
    assert store.resources()==[resource]
    archive=Results(store).archive(result['id']);archive.write_bytes(archive.read_bytes()+b'Changed')
    assert upload(client,job,content).status_code==409  # Idempotent upload does not hide damaged storage.


def test_result_artifacts_and_provenance_survive_full_backup_restore(result_pilot):
    from nxserver.backups import Backups,FUTURES
    client,store,release,resource,job=result_pilot;content,_=bundle(job)
    result=Results(store).upload(job['id'],content,'fixture');report=Results(store).preview(result['id'],release['id'])[0]
    Results(store).apply(result['id'],release['id'],report['preview_hash'],'fixture')
    manager=Backups(store);backup=manager.create('backup','fixture');FUTURES[backup['id']].result(timeout=30)
    assert manager.get_job(backup['id'])['status']=='completed',manager.get_job(backup['id'])['message']
    receipt=manager.report(backup['id']);preview=manager.create('preview','fixture',{'target':'local','name':'R'},receipt['id']);FUTURES[preview['id']].result(timeout=30)
    plan=manager.report(preview['id']);restore=manager.create('restore','fixture',{'target':'local','name':'R',
        'preview_id':preview['id'],'preview_hash':plan['preview_hash'],'accept_unsigned':True},receipt['id']);FUTURES[restore['id']].result(timeout=30)
    assert manager.get_job(restore['id'])['status']=='completed',manager.get_job(restore['id'])['message']
    restored=Store(plan['target_folder']);assert Results(restored).get(result['id'])==Results(store).get(result['id'])
    assert Results(restored).archive(result['id']).read_bytes()==content
    assert scan(restored)['error_count']==0


def test_collector_reads_closed_local_parts_without_saving_or_running_nx(result_pilot,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from nxserver import resultworker
    client,store,release,resource,job=result_pilot
    packet,_=Results(store).collector(job['id'],'tool-metric','CUSTOM','fixture')
    collector=tmp_path/'collector';collector.mkdir()
    with zipfile.ZipFile(io.BytesIO(packet)) as z:
        for item in z.infolist():(collector/item.filename).write_bytes(z.read(item))
    local=collector/'return-library';graphics=local/SPECS['tool-metric']['graphics_dir'];graphics.mkdir(parents=True)
    (graphics/'CUSTOM.prt').write_bytes(b'Prepared synthetic part')
    write(local,SPECS['tool-metric']['path'],table(['LIBRF','DESCR','HLDREF'],[('CUSTOM','Prepared','')]))
    part=SimpleNamespace(FullPath=str(graphics/'CUSTOM.prt'),IsModified=False,ComponentAssembly=SimpleNamespace(RootComponent=None))
    class Parts(list):Work=part
    monkeypatch.setattr(resultworker,'job_directory',lambda p:p)
    monkeypatch.setattr(resultworker,'check_environment',lambda session:None)
    monkeypatch.setattr(resultworker,'inventory_part',lambda part,root:{'path':'CUSTOM.prt','units':'Millimeters','fully_loaded':True,
        'bodies':[{'id':'B','name':'Synthetic','solid':True,'convergent':False,'face_count':6,'layer':1,'color':42}]})
    value=resultworker.collect(collector,SimpleNamespace(Parts=Parts([part])))
    parsed=parse_package(Path(value['file']).read_bytes(),job)
    assert parsed['root_key']=='CUSTOM' and parsed['components']==[] and not value['automatic_cam_approval']
    with pytest.raises(Exception):resultworker.collect(collector,SimpleNamespace(Parts=Parts([part])))
    part.IsModified=True
    with pytest.raises(Exception,match='Save all'):resultworker.collect(collector,SimpleNamespace(Parts=Parts([part])))


def test_damaged_source_result_blocks_approved_export_even_if_extracted_assets_are_intact(result_pilot):
    client,store,release,resource,job=result_pilot;content,_=bundle(job)
    results=Results(store);result=results.upload(job['id'],content,'fixture');report=results.preview(result['id'],release['id'])[0]
    results.apply(result['id'],release['id'],report['preview_hash'],'fixture')
    parent=next(r for r in store.resources() if r['id']==resource['id'])
    assets=[store.asset(key)[0] for key in parent['asset_ids']]
    results.validate_assignment(parent,assets)
    archive=results.archive(result['id']);archive.write_bytes(archive.read_bytes()+b'External corruption')
    with pytest.raises(Exception,match='archive changed'):results.validate_assignment(parent,assets)


def test_pre_results_database_snapshot_can_be_restored_with_new_empty_table(result_pilot):
    from nxserver.backups import Backups,FUTURES
    client,store,release,resource,job=result_pilot
    with store.db() as db:db.execute('DROP TABLE geometry_results')  # Own synthetic old-format fixture only.
    manager=Backups(store);backup=manager.create('backup','fixture');FUTURES[backup['id']].result(timeout=30)
    assert manager.get_job(backup['id'])['status']=='completed'
    receipt=manager.report(backup['id']);preview=manager.create('preview','fixture',{'target':'local','name':'Old'},receipt['id']);FUTURES[preview['id']].result(timeout=30)
    plan=manager.report(preview['id']);restore=manager.create('restore','fixture',{'target':'local','name':'Old',
        'preview_id':preview['id'],'preview_hash':plan['preview_hash'],'accept_unsigned':True},receipt['id']);FUTURES[restore['id']].result(timeout=30)
    assert manager.get_job(restore['id'])['status']=='completed',manager.get_job(restore['id'])['message']
    restored=Store(plan['target_folder'])
    with restored.db() as db:assert db.execute('SELECT count(*) FROM geometry_results').fetchone()[0]==0
    assert scan(restored)['error_count']==0


def test_metadata_application_rolls_back_as_one_unit_and_retains_unassigned_evidence(result_pilot,monkeypatch):
    client,store,release,resource,job=result_pilot;content,_=bundle(job);results=Results(store)
    result=results.upload(job['id'],content,'fixture');report=results.preview(result['id'],release['id'])[0]
    audit=store.audit
    def fail(db,actor,action,payload):
        if action=='geometry.result.applied':raise RuntimeError('Synthetic final transaction failure')
        return audit(db,actor,action,payload)
    monkeypatch.setattr(store,'audit',fail)
    with pytest.raises(RuntimeError,match='transaction'):results.apply(result['id'],release['id'],report['preview_hash'],'fixture')
    assert store.resources()==[resource] and len(store.history(resource['id']))==1
    assert results.get(result['id'])['applied'] is None
    with store.db() as db:assert db.execute('SELECT count(*) FROM assets').fetchone()[0]==3
    assert scan(store)['error_count']==0  # The orphan immutable attachments are visible/reusable, not silently deleted.


@pytest.mark.parametrize('case',['array','null','string','duplicate_key','deep_json','native_values_array'])
def test_invalid_manifest_shapes_are_client_errors_without_partial_writes(result_pilot,case):
    client,store,release,resource,job=result_pilot
    content,manifest=bundle(job)
    raw=json.dumps(manifest)
    if case=='array':raw='[]'
    elif case=='null':raw='null'
    elif case=='string':raw='"not a manifest"'
    elif case=='duplicate_key':raw=raw[:-1]+',"contract_version":1}'
    elif case=='deep_json':raw='['*2000+'0'+']'*2000
    else:
        manifest['objects'][0]['cam']['records'][0]['values']=[]
        raw=json.dumps(manifest)
    output=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as original, zipfile.ZipFile(output,'w') as changed:
        for name in original.namelist():changed.writestr(name,raw.encode() if name=='manifest.json' else original.read(name))
    response=upload(client,job,output.getvalue())
    assert response.status_code==400,response.text
    assert store.resources()==[resource] and Results(store).list(job['id'])==[]
    assert not (store.root/'geometry-results').exists()


def test_missing_result_archive_is_a_storage_conflict_not_an_unhandled_error(result_pilot):
    client,store,release,resource,job=result_pilot
    content,_=bundle(job);result=upload(client,job,content).json()
    path=Results(store).archive(result['id']);path.rename(path.with_suffix('.retained'))
    response=upload(client,job,content)
    assert response.status_code==409,response.text
    assert client.post('/api/v1/geometry-results/'+result['id']+'/preview',headers=HEADERS,
        json={'target_release_id':release['id']}).status_code==409
    assert store.resources()==[resource]


def test_collector_snapshot_limit_does_not_leave_an_unregistered_archive(result_pilot):
    from nxserver.store import canonical
    from nxserver.safety import DomainError
    client,store,release,resource,job=result_pilot
    with store.db() as db:
        row=db.execute('SELECT payload FROM geometry_jobs WHERE id=?',(job['id'],)).fetchone()
        payload=json.loads(row['payload']);payload['collector_packages']={format(i,'064x'):{} for i in range(20)}
        db.execute('UPDATE geometry_jobs SET payload=? WHERE id=?',(canonical(payload),job['id']))
    with pytest.raises(DomainError,match='20 collector'):
        Results(store).collector(job['id'],'tool-metric','CUSTOM','fixture')
    assert not (store.root/'geometry-results').exists()

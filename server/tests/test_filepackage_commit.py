"""Final file adoption fence, with synthetic local bytes only."""
import copy

import pytest

from nxserver.filepackages import FilePackages
from nxserver.safety import DomainError
from nxserver.store import Store,TOOL_DATABASE,canonical
from test_management import DATABASE
from test_filepackages import packages

PATHS={'template':'UGII/templates/CUSTOM.prt','ui_standard':'UGII/menus/roles/CUSTOM.mtx',
       'machine':'MACH/resource/library/machine/installed_machines/CUSTOM.xml',
       'fixture':'MACH/resource/library/fixture_automation/graphics/CUSTOM.prt'}


def setup(root,kind):
    source,standard=root/'source',root/'standard';path=PATHS[kind]
    file=source/path;file.parent.mkdir(parents=True);file.write_bytes(b'<Synthetic/>' if file.suffix in ('.xml','.mtx') else b'Synthetic PRT; no real CAD')
    (standard/path).parent.mkdir(parents=True)
    database=source/TOOL_DATABASE;database.parent.mkdir(parents=True,exist_ok=True);database.write_text(DATABASE)
    store=Store(root/'managed',{'test':source},standards={'2512':standard});release=store.import_release('test','2512','Synthetic file source','fixture')
    parent=None
    if kind in ('machine','fixture'):
        parent=store.save_resource({'kind':kind,'name':'Existing draft','cam':{},'erp':None,'cam_status':'draft','asset_ids':[]},'fixture')
    return store,release,source,standard,parent


@pytest.mark.parametrize('kind',list(PATHS))
@pytest.mark.parametrize('drift',['source','baseline','version','manifest','folder','failed','catalog'])
def test_changed_inputs_after_last_preview_cannot_create_file_ownership(tmp_path_factory,monkeypatch,kind,drift):
    store,release,source,standard,parent=setup(tmp_path_factory.mktemp('fc'),kind);manager=FilePackages(store);path=PATHS[kind]
    report,_=manager.adoption_preview(release['id'],kind,[path],parent['id'] if parent else None);assert report['can_adopt']
    original=store.save_resource;before_versions=len(store.history(parent['id'])) if parent else 0
    def mutate(*args,**kwargs):
        if drift=='source':(store.release_folder(release['id'])/path).write_bytes(b'Changed source after preview')
        elif drift=='baseline':(standard/path).write_bytes((source/path).read_bytes())
        else:
            with store.db() as db:
                if drift=='version':db.execute("UPDATE releases SET nx_version='2406' WHERE id=?",(release['id'],))
                elif drift=='manifest':db.execute("UPDATE releases SET manifest='[]' WHERE id=?",(release['id'],))
                elif drift=='folder':db.execute('UPDATE releases SET folder=? WHERE id=?',(str(source),release['id']))
                elif drift=='failed':db.execute("UPDATE releases SET status='failed' WHERE id=?",(release['id'],))
                else:db.execute('UPDATE releases SET catalog=? WHERE id=?',(canonical({'marker':'Changed before commit'}),release['id']))
        return original(*args,**kwargs)
    monkeypatch.setattr(store,'save_resource',mutate)
    with pytest.raises(DomainError) as failure:manager.adopt(release['id'],kind,[path],'Customer files',parent['id'] if parent else None,report['preview_hash'],'fixture')
    assert failure.value.status==409
    assert len(store.resources())==(1 if parent else 0)
    assert len(store.history(parent['id']))==before_versions if parent else True
    assert all(not resource.get('file_package') for resource in store.resources())
    assert len(store.audit_entries())>=1  # Already copied immutable attachment remains evidence.
    with store.db() as db:assert db.execute('SELECT COUNT(*) FROM assets').fetchone()[0]==1
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('kind',list(PATHS))
@pytest.mark.parametrize('drift',['new_owner','removed_source','baseline_root','baseline_directory'])
def test_claims_and_baseline_presence_are_rechecked_in_commit(tmp_path_factory,monkeypatch,kind,drift):
    store,release,source,standard,parent=setup(tmp_path_factory.mktemp('fc'),kind);manager=FilePackages(store);path=PATHS[kind]
    report,_=manager.adoption_preview(release['id'],kind,[path],parent['id'] if parent else None);original=store.save_resource;others=[]
    def mutate(*args,**kwargs):
        if drift=='new_owner':
            value=copy.deepcopy(args[0]);value['name']='Other file owner';value['file_package']['provenance']=None
            others.append(original(value,'different writer'))
        elif drift=='removed_source':(store.release_folder(release['id'])/path).unlink()  # Own disposable fixture only.
        elif drift=='baseline_root':
            another=standard.parent/'another';another.mkdir();store.standards['2512']=another
        else:(standard/path).parent.rmdir()  # Empty fixture baseline directory only.
        return original(*args,**kwargs)
    monkeypatch.setattr(store,'save_resource',mutate)
    with pytest.raises(DomainError) as failure:manager.adopt(release['id'],kind,[path],'Package',parent['id'] if parent else None,report['preview_hash'],'fixture')
    assert failure.value.status==409 and len(store.resources())==(1 if parent else 0)+len(others)
    assert all(resource['name']=='Other file owner' for resource in store.resources() if resource.get('file_package'))


@pytest.mark.parametrize('kind',['machine','fixture'])
def test_same_revision_parent_payload_change_cannot_be_overwritten_by_file_adoption(tmp_path_factory,monkeypatch,kind):
    store,release,_,_,parent=setup(tmp_path_factory.mktemp('fc'),kind);manager=FilePackages(store);path=PATHS[kind]
    report,_=manager.adoption_preview(release['id'],kind,[path],parent['id']);original=store.save_resource
    def mutate(*args,**kwargs):
        value={name:copy.deepcopy(data) for name,data in parent.items() if name not in ('id','revision','updated_at')};value['name']='Independent current parent';raw=canonical(value)
        with store.db() as db:
            db.execute('UPDATE resources SET payload=? WHERE id=?',(raw,parent['id']));db.execute('UPDATE resource_versions SET payload=? WHERE id=? AND revision=1',(raw,parent['id']))
        return original(*args,**kwargs)
    monkeypatch.setattr(store,'save_resource',mutate)
    with pytest.raises(DomainError,match='parent contents'):manager.adopt(release['id'],kind,[path],'Package',parent['id'],report['preview_hash'],'fixture')
    assert store.resources()[0]['name']=='Independent current parent' and not store.resources()[0].get('file_package') and len(store.history(parent['id']))==1


@pytest.mark.parametrize('kind',list(PATHS))
def test_successful_adoption_records_checked_draft_and_does_not_change_source_or_baseline(tmp_path_factory,kind):
    store,release,source,standard,parent=setup(tmp_path_factory.mktemp('fc'),kind);manager=FilePackages(store);path=PATHS[kind]
    report,_=manager.adoption_preview(release['id'],kind,[path],parent['id'] if parent else None)
    before=store.manifest(store.release_folder(release['id']));stock=store.manifest(standard);incoming=store.manifest(source)
    result=manager.adopt(release['id'],kind,[path],'Customer files',parent['id'] if parent else None,report['preview_hash'],'fixture')
    assert result['cam_status']=='draft' and result['file_package']['checks']=={} and result['file_package']['asset_hashes']=={}
    assert result['file_package']['provenance']['owned_files']==report['entries'] and result['revision']==(2 if parent else 1)
    assert store.manifest(store.release_folder(release['id']))==before and store.manifest(standard)==stock and store.manifest(source)==incoming
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('kind',['template','ui_standard'])
@pytest.mark.parametrize('drift',['overlay_added','sidt_root'])
def test_effective_sidt_overlay_is_reselected_at_final_commit(tmp_path_factory,monkeypatch,kind,drift):
    root=tmp_path_factory.mktemp('fc');store,release,source,standard,parent=setup(root,kind);sidt=root/'sidt';sidt.mkdir()
    store.sidts['2512']=sidt;monkeypatch.setattr(store,'sidt_root',lambda version:store.sidts[version])
    manager=FilePackages(store);path=PATHS[kind];report,_=manager.adoption_preview(release['id'],kind,[path]);original=store.save_resource
    def mutate(*args,**kwargs):
        if drift=='overlay_added':
            overlay=sidt/'applications/nx/ugii'/path[len('UGII/'):];overlay.parent.mkdir(parents=True);overlay.write_bytes((source/path).read_bytes())
        else:
            another=root/'different-sidt';another.mkdir();store.sidts['2512']=another
        return original(*args,**kwargs)
    monkeypatch.setattr(store,'save_resource',mutate)
    with pytest.raises(DomainError,match='baseline'):manager.adopt(release['id'],kind,[path],'Package',None,report['preview_hash'],'fixture')
    assert store.resources()==[] and store.active()['release_id'] is None


@pytest.mark.parametrize('drift',['source','baseline','failed'])
def test_final_gate_failure_retains_asset_intent_but_no_result_and_receipt_does_not_restart(packages,monkeypatch,drift):
    import uuid
    from test_adoption_jobs import file_job
    from test_release_jobs import complete
    from test_management import HEADERS
    from nxserver.migration_requests import get
    client,store,_,source,standard=packages;release,body=file_job(packages,1);original=store.save_resource;path=body['paths'][0]
    def mutate(*args,**kwargs):
        if drift=='source':(store.release_folder(release['id'])/path).write_bytes(b'After preview changed source')
        elif drift=='baseline':(standard/path).write_bytes((source/path).read_bytes())
        else:
            with store.db() as db:db.execute("UPDATE releases SET status='failed' WHERE id=?",(release['id'],))
        return original(*args,**kwargs)
    monkeypatch.setattr(store,'save_resource',mutate);key=str(uuid.uuid4())
    first,job=complete(client,client.post('/api/v1/release-jobs/file_adopt',headers={**HEADERS,'Idempotency-Key':key},json=body))
    assert job['status']=='failed' and 'before ownership commit' in job['message'] and not job.get('domain_result') and store.resources()==[]
    assert len(job['asset_allocations'])==1 and job['asset_allocations'][0]['state']=='stored' and get(store,'admin',key)['result']==first
    before=store.audit_entries()
    def forbidden(*args,**kwargs):pytest.fail('Old receipt must not replan or submit a second asset writer.')
    monkeypatch.setattr(client.app.state.upgrades.pool,'submit',forbidden);monkeypatch.setattr(FilePackages,'adoption_preview',forbidden)
    replay=client.post('/api/v1/release-jobs/file_adopt',headers={**HEADERS,'Idempotency-Key':key},json=body)
    assert replay.status_code==202 and replay.json()==first and store.audit_entries()==before

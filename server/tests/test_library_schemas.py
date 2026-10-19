"""Schema-only creation reads metadata first and never returns native objects."""
import json
from pathlib import Path

import pytest

from nxserver.libraries import SPECS,NativeTable
from nxserver.library_schemas import LibrarySchemas
from nxserver.safety import DomainError
from nxserver.store import Store
from test_management import pilot,HEADERS,ERP_HEADERS,imported
from test_libraries import write


def test_schema_inventory_does_not_parse_any_library_or_scan_references(pilot,monkeypatch):
    client,store,*_=pilot;target=imported(client);before=store.audit_entries()
    def forbidden(*args,**kwargs):raise AssertionError('Schema inventory must not scan native files or references.')
    monkeypatch.setattr(NativeTable,'read',forbidden);monkeypatch.setattr(store,'manifest',forbidden)
    monkeypatch.setattr('nxserver.store.reference_report',forbidden)
    response=client.get('/api/v1/releases/'+target['id']+'/library-schemas',headers=HEADERS)
    assert response.status_code==200,response.text
    report=response.json();assert report['scope']=='native_schema_inventory' and report['read_only'] is True
    assert report['files_verified'] is False and [item['id'] for item in report['libraries']]==['tool-metric']
    assert all('schemas' not in item and 'items' not in item for item in report['libraries'])
    assert store.audit_entries()==before


def test_selected_schema_has_all_format_fields_but_no_object_values(pilot,monkeypatch):
    client,store,*_=pilot;target=imported(client)
    inventory=client.get('/api/v1/releases/'+target['id']+'/library-schemas',headers=HEADERS)
    assert inventory.status_code==200,inventory.text
    pin=inventory.json()['inventory_sha256'];before=store.audit_entries()
    def forbidden(*args,**kwargs):raise AssertionError('Schema-only read must not build object groups.')
    monkeypatch.setattr(NativeTable,'groups',forbidden)
    response=client.get('/api/v1/releases/'+target['id']+'/library-schemas/tool-metric?inventory_sha256='+pin,headers=HEADERS)
    assert response.status_code==200,response.text
    report=response.json();assert report['scope']=='native_library_schema' and report['integrity_checked'] is True
    assert report['id']=='tool-metric' and report['kind']==SPECS['tool-metric']['kind']
    assert report['schemas'][0]['fields']==['LIBRF','DESCR','DIA'] and 'items' not in report and 'records' not in report
    assert 'STD_1' not in response.text and store.audit_entries()==before


@pytest.mark.parametrize('identifier',list(SPECS))
def test_every_adapter_has_a_read_only_schema_with_source_order_and_new_fields(tmp_path,identifier):
    spec=SPECS[identifier];source=tmp_path/'source'
    text='#CLASS A\nFORMAT LIBRF NEW_COLUMN __proto__\nDATA |CUSTOM|retained|literal\n#CLASS A\nFORMAT NAME SEQ\nDATA |CUSTOM|9\n#END_DATA\n'
    write(source,spec['path'],text);store=Store(tmp_path/'managed',{'test':source})
    target=store.import_release('test','2512','Schema source','fixture');manager=LibrarySchemas(store);before=store.audit_entries()
    inventory=manager.inventory(target['id']);schema=manager.schema(target['id'],identifier,inventory['inventory_sha256'])
    full=NativeTable(text)
    assert schema['schemas']==full.blocks and schema['row_count']==len(full.records)==2
    assert schema['id']==identifier and schema['kind']==spec['kind'] and 'CUSTOM' not in json.dumps(schema)
    assert store.audit_entries()==before and store.manifest(source)==target['manifest'] and store.active()['release_id'] is None


@pytest.mark.parametrize('line_end',['\n','\r\n','\r','\u2028'])
def test_schema_parser_matches_native_continuations_without_retaining_objects(line_end):
    text=line_end.join(['#CLASS A','FORMAT LIBRF',' NAME SEQ','DATA |CUSTOM',' |Description|4|||','# comment','#CLASS A','FORMAT NAME RTYPE','DATA |CUSTOM|BODY','#END_DATA',''])
    full=NativeTable(text);schema=NativeTable(text,schema_only=True,max_schemas=2,max_fields=5)
    assert schema.blocks==full.blocks and schema.row_count==len(full.records)==2
    assert schema.records==[] and schema.tokens==[] and schema.text==''
    with pytest.raises(DomainError,match='object values'):schema.groups()
    assert ''.join(token['raw'] for token in full.tokens)==text


@pytest.mark.parametrize('body',['|ID|description','ID|description','| ID | description |||','ID|description'+'|'*100000],ids=['leading','plain','spaces','large_empty_tail'])
def test_data_pipe_semantics_and_large_empty_tail_are_unchanged(body):
    text='#CLASS A\nFORMAT LIBRF DESCR\nDATA '+body+'\n'
    full=NativeTable(text);schema=NativeTable(text,schema_only=True)
    assert full.records[0]['values']=={'LIBRF':'ID','DESCR':'description'} and schema.row_count==1


@pytest.mark.parametrize('body',['|ID','|ID|description|EXTRA','|||',''])
def test_schema_reader_also_rejects_malformed_data(body):
    text='#CLASS A\nFORMAT LIBRF DESCR\nDATA '+body+'\n'
    for mode in (False,True):
        with pytest.raises(DomainError):NativeTable(text,schema_only=mode)


@pytest.mark.parametrize('bound,value',[('max_schemas',1),('max_fields',2)])
def test_definition_limits_apply_before_collecting_data(bound,value):
    text='#CLASS A\nFORMAT LIBRF DESCR\nDATA |ID|Description\nFORMAT NAME SEQ\nDATA |ID|4\n'
    with pytest.raises(DomainError,match='definition bound'):NativeTable(text,schema_only=True,**{bound:value})


def test_large_schema_only_library_does_not_collect_rows():
    text='#CLASS A\nFORMAT LIBRF DESCR\n'+''.join('DATA |KEY_'+str(index)+'|Description\n' for index in range(10000))
    table=NativeTable(text,schema_only=True,max_schemas=1,max_fields=2)
    assert table.row_count==10000 and table.records==table.tokens==[] and len(table.blocks)==1


@pytest.mark.parametrize('suffix',['library-schemas','library-schemas/tool-metric?inventory_sha256='+'a'*64])
def test_schema_routes_are_admin_only(pilot,suffix):
    client,store,*_=pilot;target=imported(client);before=store.audit_entries();path='/api/v1/releases/'+target['id']+'/'+suffix
    assert client.get(path).status_code==401 and client.get(path,headers=ERP_HEADERS).status_code==403
    assert store.audit_entries()==before


def test_selected_schema_requires_a_fresh_valid_inventory_pin(pilot):
    client,store,*_=pilot;target=imported(client);base='/api/v1/releases/'+target['id']+'/library-schemas'
    inventory=client.get(base,headers=HEADERS).json();path=base+'/tool-metric'
    for query in ('','?inventory_sha256=invalid'):assert client.get(path+query,headers=HEADERS).status_code==422
    assert client.get(path+'?inventory_sha256='+'0'*64,headers=HEADERS).status_code==409
    with store.db() as db:db.execute("UPDATE releases SET status='blocked' WHERE id=?",(target['id'],))
    assert client.get(path+'?inventory_sha256='+inventory['inventory_sha256'],headers=HEADERS).status_code==409
    fresh=client.get(base,headers=HEADERS).json();assert client.get(path+'?inventory_sha256='+fresh['inventory_sha256'],headers=HEADERS).status_code==200


def test_inventory_and_selected_schema_never_call_legacy_full_readers(pilot,monkeypatch):
    client,store,*_=pilot;target=imported(client);manager=LibrarySchemas(store)
    def forbidden(*args,**kwargs):raise AssertionError('Unbounded/legacy inventory reader used.')
    monkeypatch.setattr(store,'get_release',forbidden);monkeypatch.setattr(store,'manifest',forbidden)
    monkeypatch.setattr(NativeTable,'read',forbidden);monkeypatch.setattr(NativeTable,'groups',forbidden)
    inventory=manager.inventory(target['id']);assert manager.schema(target['id'],'tool-metric',inventory['inventory_sha256'])['integrity_checked']


def test_schema_input_bound_is_checked_before_file_read(pilot,monkeypatch):
    import nxserver.library_schemas as module
    client,store,*_=pilot;target=imported(client);monkeypatch.setattr(module,'MAX_LIBRARY',1)
    manager=LibrarySchemas(store);inventory=manager.inventory(target['id']);assert inventory['libraries'][0]['can_read_schema'] is False
    def forbidden(*args,**kwargs):raise AssertionError('Oversized schema was opened.')
    monkeypatch.setattr(module,'read',forbidden)
    with pytest.raises(DomainError,match='byte bound'):manager.schema(target['id'],'tool-metric',inventory['inventory_sha256'])


def test_final_metadata_change_invalidates_a_successful_bounded_read(pilot,monkeypatch):
    import nxserver.library_schemas as module
    client,store,*_=pilot;target=imported(client);manager=LibrarySchemas(store);inventory=manager.inventory(target['id']);original=module.read
    def drift(*args,**kwargs):
        raw=original(*args,**kwargs)
        with store.db() as db:db.execute("UPDATE releases SET status='blocked' WHERE id=?",(target['id'],))
        return raw
    monkeypatch.setattr(module,'read',drift)
    with pytest.raises(DomainError,match='changed during'):manager.schema(target['id'],'tool-metric',inventory['inventory_sha256'])


@pytest.mark.parametrize('mode',['same_size','missing'])
def test_changed_or_missing_library_is_not_a_schema_success(pilot,mode):
    client,store,*_=pilot;target=imported(client);manager=LibrarySchemas(store);inventory=manager.inventory(target['id'])
    path=Path(target['folder'])/SPECS['tool-metric']['path']
    if mode=='same_size':path.write_bytes(path.read_bytes().replace(b'STD_1',b'OTHER'))
    else:path.unlink()  # Only this test's disposable file.
    with pytest.raises(DomainError):manager.schema(target['id'],'tool-metric',inventory['inventory_sha256'])


def test_manifest_and_response_bounds_are_enforced(pilot,monkeypatch):
    import nxserver.library_schemas as module
    client,store,*_=pilot;target=imported(client);manager=LibrarySchemas(store)
    with monkeypatch.context() as patch:
        patch.setattr(module,'MAX_METADATA',1)
        with pytest.raises(DomainError,match='manifest'):manager.inventory(target['id'])
    with monkeypatch.context() as patch:
        patch.setattr(module,'MAX_MANIFEST',0)
        with pytest.raises(DomainError,match='manifest'):manager.inventory(target['id'])
    with monkeypatch.context() as patch:
        patch.setattr(module,'MAX_REPORT',1)
        with pytest.raises(DomainError,match='response'):manager.inventory(target['id'])

"""Single SQLite read-point contracts. Metadata snapshot is not NX/audit qualification."""
import json
import hashlib
from contextlib import contextmanager

import pytest

from nxserver.store import Store,canonical,digest
from test_management import HEADERS,ERP_HEADERS,imported,pilot


def state(store):
    with store.db() as db:
        return {name:db.execute('SELECT * FROM "'+name+'" ORDER BY rowid').fetchall()
            for(name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def test_workspace_admin_only_read_only_shape_and_repeatable_state_digest(pilot):
    client,store,source,log=pilot;release=imported(client)
    assert client.get('/api/v1/workspace').status_code==401
    assert client.get('/api/v1/workspace',headers=ERP_HEADERS).status_code==403
    before=state(store);first=client.get('/api/v1/workspace',headers=HEADERS)
    assert first.status_code==200,first.text
    value=first.json();second=store.workspace()
    assert value['resources']==store.resources() and value['releases']==store.releases() and value['active']==store.active()
    assert 'manifest' not in value['releases'][0]
    assert value['releases'][0]['test_launcher']==store.get_release(release['id'])['test_launcher']
    assert value['snapshot']['state_sha256']==second['snapshot']['state_sha256']
    assert value['snapshot']['state_sha256']==digest([value['snapshot']['audit_head'],{key:value[key] for key in ('resources','releases','active')}])
    assert value['snapshot']['consistency']=='sqlite_read_transaction'
    assert not value['snapshot']['files_verified'] and not value['snapshot']['audit_verified'] and not value['snapshot']['signed']
    assert state(store)==before


def test_empty_snapshot_has_bootstrap_anchor_and_no_audit_write(pilot):
    client,store,*_=pilot;before=state(store);value=store.workspace()
    assert value['resources']==value['releases']==[] and value['active']['release_id'] is None
    assert value['snapshot']['audit_head']=={'sequence':0,'hash':'0'*64}
    assert state(store)==before


@pytest.mark.parametrize('cam', [{}, {'tolerance':0.015,'exact_float':1.0,'small':1e-20},
    {'integer':9007199254740993,'label':'🔧 new column','__proto__':'literal'}])
def test_workspace_wire_checksum_covers_actual_utf8_json_without_numeric_reencoding(pilot, cam):
    client,store,*_=pilot
    store.save_resource({'kind':'gauge','name':'Synthetic free CAM JSON','cam':cam,
        'cam_status':'draft','asset_ids':[],'erp':None},'fixture')
    before=state(store);response=client.get('/api/v1/workspace',headers=HEADERS)
    assert response.status_code==200 and response.json()['resources'][0]['cam']==cam
    assert response.headers['X-NX-Workspace-Contract']=='1'
    assert response.headers['X-NX-Workspace-Bytes']==str(len(response.content))
    assert response.headers['X-NX-Workspace-SHA256']==hashlib.sha256(response.content).hexdigest()
    assert response.headers['content-length']==str(len(response.content))
    assert response.headers['cache-control']=='no-store'
    if 'exact_float' in cam:assert b'"exact_float":1.0' in response.content
    if 'label' in cam:assert '🔧'.encode('utf-8') in response.content
    assert not response.json()['snapshot']['files_verified'] and not response.json()['snapshot']['signed']
    assert state(store)==before
    for headers in ({},ERP_HEADERS):
        denied=client.get('/api/v1/workspace',headers=headers)
        assert denied.status_code in (401,403)
        assert 'X-NX-Workspace-SHA256' not in denied.headers and 'X-NX-Workspace-Bytes' not in denied.headers


def test_external_committed_writer_cannot_mix_resource_release_launcher_pointer_or_head(pilot,monkeypatch):
    client,store,*_=pilot;release=imported(client)
    body={'kind':'gauge','name':'Before writer','cam':{},'cam_status':'draft','asset_ids':[],'erp':None}
    resource=store.save_resource(body,'fixture');before=store.workspace();original=store.db;triggered=[]
    writer=Store(store.root)
    class Proxy:
        def __init__(self,connection):self.connection=connection
        def execute(self,sql,*arguments):
            cursor=self.connection.execute(sql,*arguments)
            if sql.startswith('SELECT sequence,hash FROM audit') and not triggered:
                triggered.append(True)
                writer.save_resource({**body,'name':'After writer'},'fixture',resource['id'],1)
                with writer.db() as changed:
                    changed.execute('UPDATE releases SET name=? WHERE id=?',('After writer release',release['id']))
                    changed.execute('UPDATE active SET release_id=?,generation=2 WHERE singleton=1',(release['id'],))
                    launcher=json.loads(changed.execute('SELECT payload FROM test_launchers WHERE release_id=?',(release['id'],)).fetchone()[0])
                    launcher['fixture_marker']='after writer'
                    changed.execute('UPDATE test_launchers SET payload=? WHERE release_id=?',(canonical(launcher),release['id']))
                    writer.audit(changed,'fixture','fixture.snapshot.race',{'id':release['id']})
            return cursor
    @contextmanager
    def pinned_db():
        with original() as connection:yield Proxy(connection)
    monkeypatch.setattr(store,'db',pinned_db)
    captured=store.workspace()
    assert triggered and captured['snapshot']['audit_head']==before['snapshot']['audit_head']
    for key in ('resources','releases','active'):assert captured[key]==before[key]
    assert captured['snapshot']['state_sha256']==before['snapshot']['state_sha256']
    monkeypatch.setattr(store,'db',original)
    after=store.workspace()
    assert after['resources'][0]['revision']==2 and after['releases'][0]['name']=='After writer release'
    assert after['active']['generation']==2 and not after['active']['usable']
    assert after['releases'][0]['test_launcher']['fixture_marker']=='after writer'
    assert after['snapshot']['audit_head']['sequence']>captured['snapshot']['audit_head']['sequence']


@pytest.mark.parametrize('mutation',['missing_active','dangling_active','resource_json','resource_identity','release_json','launcher_json'])
def test_invalid_metadata_fails_closed_without_adding_audit_or_resetting_state(pilot,mutation):
    client,store,*_=pilot;release=imported(client)
    store.save_resource({'kind':'gauge','name':'Fixture','cam':{},'cam_status':'draft','asset_ids':[]},'fixture')
    with store.db() as db:
        if mutation=='missing_active':db.execute('DELETE FROM active')
        elif mutation=='dangling_active':db.execute('UPDATE active SET release_id=?',('00000000-0000-0000-0000-000000000001',))
        elif mutation=='resource_json':db.execute('UPDATE resources SET payload=?',('[1]',))
        elif mutation=='resource_identity':
            value=json.loads(db.execute('SELECT payload FROM resources').fetchone()[0]);value['revision']=999
            db.execute('UPDATE resources SET payload=?',(canonical(value),))
        elif mutation=='release_json':db.execute('UPDATE releases SET catalog=?',('{',))
        else:db.execute('UPDATE test_launchers SET payload=?',('{',))
    before=state(store);response=client.get('/api/v1/workspace',headers=HEADERS)
    assert response.status_code==409,response.text
    assert state(store)==before


def test_workspace_never_looks_up_a_protected_folder(pilot,monkeypatch):
    from pathlib import Path
    client,store,*_=pilot;release=imported(client)
    with store.db() as db:db.execute('UPDATE releases SET folder=?',(r'W:\SiemensShare\nx2406',))
    original=Path.lstat
    def guard(path,*args,**kwargs):
        assert not str(path).replace('\\','/').casefold().startswith('w:')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'lstat',guard)
    before=state(store);response=client.get('/api/v1/workspace',headers=HEADERS)
    assert response.status_code==400,response.text
    assert state(store)==before

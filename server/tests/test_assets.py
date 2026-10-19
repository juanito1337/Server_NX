"""Attachment inventory/reuse/download contracts; synthetic files in disposable storage."""
import base64
import hashlib
import json
import uuid
from urllib.parse import quote

import pytest

from test_management import HEADERS, ERP_HEADERS, pilot


def uploaded(client, filename='picture.png', content=b'Synthetic content, not a real model'):
    response=client.post('/api/v1/assets',headers=HEADERS,
        json={'filename':filename,'content_base64':base64.b64encode(content).decode()})
    assert response.status_code==201,response.text
    return response.json()


def body(ids, name='Fixture'):
    return {'kind':'fixture','name':name,'cam_status':'draft','asset_ids':ids,'cam':{}}


def test_inventory_search_pagination_and_roles(pilot):
    client,store,*_=pilot
    one=uploaded(client,'Bohrer ü.prt')
    two=uploaded(client,'holder.png')
    inventory=client.get('/api/v1/assets',headers=HEADERS).json()
    assert inventory['total']==2 and inventory['filtered']==2 and not inventory['integrity_checked']
    assert all(asset['current_resource_count']==0 for asset in inventory['items'])
    filtered=client.get('/api/v1/assets?query=BOHRER&limit=1',headers=HEADERS).json()
    assert filtered['filtered']==1 and filtered['items'][0]['id']==one['id']
    page=client.get('/api/v1/assets?offset=1&limit=1',headers=HEADERS).json()
    assert len(page['items'])==1 and page['items'][0]['id']==one['id']
    assert client.get('/api/v1/assets').status_code==401
    assert client.get('/api/v1/assets',headers=ERP_HEADERS).status_code==403
    for query in ('limit=501','offset=-1','query='+'x'*201):
        assert client.get('/api/v1/assets?'+query,headers=HEADERS).status_code==422


def test_attachment_reuse_and_unlink_are_revisioned_without_deleting_bytes(pilot):
    client,store,*_=pilot
    asset=uploaded(client,'shared.prt')
    first=client.post('/api/v1/resources',headers=HEADERS,json=body([asset['id']])).json()
    second=client.post('/api/v1/resources',headers=HEADERS,json=body([asset['id']],'Second fixture')).json()
    assert client.get('/api/v1/assets',headers=HEADERS).json()['items'][0]['current_resource_count']==2
    metadata,path=store.asset(asset['id']);before=path.read_bytes()
    assert client.put('/api/v1/resources/'+first['id'],headers=HEADERS,
        json={**body([]),'expected_revision':first['revision']}).status_code==200
    history=store.history(first['id'])
    assert json.loads(history[0]['payload'])['asset_ids']==[asset['id']]
    assert json.loads(history[1]['payload'])['asset_ids']==[]
    assert path.read_bytes()==before
    assert client.get('/api/v1/assets',headers=HEADERS).json()['items'][0]['current_resource_count']==1
    assert client.put('/api/v1/resources/'+second['id'],headers=HEADERS,
        json={**body([],'Second fixture'),'expected_revision':second['revision']}).status_code==200
    assert client.get('/api/v1/assets',headers=HEADERS).json()['items'][0]['current_resource_count']==0
    assert client.get('/api/v1/assets/'+asset['id'],headers=HEADERS).content==before


def test_unicode_download_is_bounded_hash_verified_and_requires_credentials(pilot):
    client,store,*_=pilot
    content=b'Isolated binary fixture, not an NX part\x00\xff'
    asset=uploaded(client,'Werkzeug ü.prt',content)
    response=client.get('/api/v1/assets/'+asset['id'],headers=HEADERS)
    assert response.status_code==200 and response.content==content
    assert response.headers['x-asset-sha256']==hashlib.sha256(content).hexdigest()
    assert "filename*=UTF-8''"+quote(asset['filename'],safe='') in response.headers['content-disposition']
    assert response.headers['cache-control']=='no-store'
    assert client.get('/api/v1/assets/'+asset['id']).status_code==401
    # ERP keeps its existing authenticated binary download contract, not inventory access.
    assert client.get('/api/v1/assets/'+asset['id'],headers=ERP_HEADERS).content==content


@pytest.mark.parametrize('damage',['missing','changed','oversized'])
def test_damaged_files_block_download_and_even_draft_assignment(pilot,damage):
    client,store,*_=pilot
    asset=uploaded(client)
    _,path=store.asset(asset['id'])
    if damage=='missing':path.unlink()  # Test-owned file only.
    elif damage=='changed':path.write_bytes(b'Outside API')
    else:
        with path.open('ab') as stream:stream.truncate(20*1024*1024+1)
    assert client.get('/api/v1/assets/'+asset['id'],headers=HEADERS).status_code==409
    assert client.get('/api/v1/assets/'+asset['id']+'/metadata',headers=HEADERS).status_code==409
    assert client.post('/api/v1/resources',headers=HEADERS,json=body([asset['id']])).status_code==409
    assert store.resources()==[]
    # Listing metadata cannot falsely report an integrity check or delete damaged evidence.
    assert client.get('/api/v1/assets',headers=HEADERS).json()['total']==1


def test_duplicate_unknown_and_noncanonical_identifiers_are_rejected(pilot):
    client,store,*_=pilot
    asset=uploaded(client)
    assert client.post('/api/v1/resources',headers=HEADERS,json=body([asset['id'],asset['id']])).status_code==400
    assert client.post('/api/v1/resources',headers=HEADERS,json=body([str(uuid.uuid4())])).status_code==400
    assert client.get('/api/v1/assets/'+asset['id'].replace('-',''),headers=HEADERS).status_code==400
    assert store.resources()==[]


def test_file_changed_between_validation_and_download_is_not_returned(pilot,monkeypatch):
    client,store,*_=pilot
    asset=uploaded(client)
    real_asset=store.asset
    def racing_asset(identifier):
        metadata,path=real_asset(identifier)
        path.write_bytes(b'Tampered after metadata check')
        return metadata,path
    monkeypatch.setattr(store,'asset',racing_asset)
    response=client.get('/api/v1/assets/'+asset['id'],headers=HEADERS)
    assert response.status_code==409 and 'during download' in response.json()['detail']

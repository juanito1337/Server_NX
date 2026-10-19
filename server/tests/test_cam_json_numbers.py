"""Reject nonfinite CAM values before any resource/history/audit/receipt commit."""
import copy
import json
import uuid

import pytest

from nxserver.auditstate import encoded
from nxserver.integrity import scan, snapshot
from nxserver.libraries import validate_catalog_cam
from nxserver.safety import DomainError
from nxserver.write_requests import get
from test_management import pilot, HEADERS


def body(number=0.015, nested=True):
    cam={'metadata':[{'measurement':number}]} if nested else {'measurement':number}
    return {'kind':'gauge','name':'Synthetic finite CAM contract','cam_status':'draft',
            'asset_ids':[],'erp':None,'cam':cam}


def state(store):
    with store.db() as db:
        counts={table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                for table in ('resources','resource_versions','audit','write_requests')}
    return snapshot(store)['sha256'],counts


@pytest.mark.parametrize('number',[float('nan'),float('inf'),float('-inf')],ids=['nan','positive_infinity','negative_infinity'])
@pytest.mark.parametrize('nested',[False,True],ids=['root','nested'])
@pytest.mark.parametrize('mode',['create','update'])
def test_direct_store_nonfinite_rejection_is_atomic(pilot,number,nested,mode):
    _,store,*_=pilot
    old=store.save_resource(body(),'fixture') if mode=='update' else None
    before=state(store);key=str(uuid.uuid4())
    with pytest.raises(DomainError,match='finite'):
        store.save_resource(body(number,nested),'fixture',old['id'] if old else None,
                            old['revision'] if old else None,request_key=key)
    assert state(store)==before


@pytest.mark.parametrize('literal',['NaN','Infinity','-Infinity','1e400'])
@pytest.mark.parametrize('receipted',[False,True])
@pytest.mark.parametrize('mode',['create','update'])
def test_raw_http_nonfinite_cannot_poison_a_readable_workspace(pilot,literal,receipted,mode):
    client,store,*_=pilot;old=store.save_resource(body(),'fixture') if mode=='update' else None
    before=state(store);value=body('NUMERIC_LITERAL')
    value['cam']['private_note']='synthetic-input-not-for-errors'
    if old:value['expected_revision']=old['revision']
    raw=json.dumps(value).replace('"NUMERIC_LITERAL"',literal)
    headers={**HEADERS,'Content-Type':'application/json'}
    if receipted:headers['Idempotency-Key']=str(uuid.uuid4())
    response=(client.put('/api/v1/resources/'+old['id'],headers=headers,content=raw) if old
              else client.post('/api/v1/resources',headers=headers,content=raw))
    assert response.status_code==400,response.text
    assert 'finite' in response.json()['detail'] and 'synthetic-input-not-for-errors' not in response.text
    assert state(store)==before and client.get('/api/v1/workspace',headers=HEADERS).status_code==200


@pytest.mark.parametrize('number',[-0.0,1.0,0.015,1e-20,1e20,2**53+1,10**400],
                         ids=['negative-zero','float-one','fraction','small-float','large-float','large-int','huge-int'])
def test_finite_values_and_arbitrary_size_integer_keep_existing_types_hashes_and_receipt(pilot,number):
    client,store,*_=pilot;value=body(number);key=str(uuid.uuid4());headers={**HEADERS,'Idempotency-Key':key}
    response=client.post('/api/v1/resources',headers=headers,json=value)
    assert response.status_code==201,response.text
    saved=response.json();assert encoded(saved['cam'])==encoded(value['cam'])
    before=state(store);again=client.post('/api/v1/resources',headers=headers,json=value)
    assert again.status_code==201 and again.json()==saved and state(store)==before
    receipt=client.get('/api/v1/write-requests/'+key,headers=HEADERS)
    assert receipt.status_code==200 and encoded(receipt.json()['result']['cam'])==encoded(value['cam'])
    assert client.get('/api/v1/workspace',headers=HEADERS).status_code==200


@pytest.mark.parametrize('status',['draft','approved','retired'])
@pytest.mark.parametrize('number',[float('nan'),float('inf'),float('-inf')])
def test_catalog_cam_finite_rule_does_not_depend_on_cam_status(status,number):
    value=body(number);value['cam_status']=status
    with pytest.raises(DomainError,match='finite'):validate_catalog_cam(value)


def test_numeric_looking_texts_remain_texts_not_nonfinite_numbers(pilot):
    client,store,*_=pilot;value=body()
    value['cam']={'labels':['NaN','Infinity','-Infinity','1e400','-0.0'],'flags':[True,False,None]}
    response=client.post('/api/v1/resources',headers=HEADERS,json=value)
    assert response.status_code==201 and encoded(response.json()['cam'])==encoded(value['cam'])
    assert client.get('/api/v1/workspace',headers=HEADERS).status_code==200


def test_legacy_nonfinite_is_reported_without_rewriting_payload_audit_or_old_receipt(pilot,monkeypatch):
    _,store,*_=pilot;value=body(float('inf'));key=str(uuid.uuid4())
    with monkeypatch.context() as patch:
        patch.setattr('nxserver.store.validate_catalog_cam',lambda payload:None)
        legacy=store.save_resource(copy.deepcopy(value),'fixture',request_key=key)
    def rows():
        with store.db() as db:
            return {table:[tuple(row) for row in db.execute('SELECT * FROM '+table)]
                    for table in ('resources','resource_versions','audit','write_requests')}
    before=rows();report=scan(store,include_files=False)
    assert 'catalog_cam_contract' in {item['code'] for item in report['errors']}
    assert rows()==before
    assert encoded(get(store,'fixture',key)['result'])==encoded(legacy)
    assert encoded(store.save_resource(copy.deepcopy(value),'fixture',request_key=key))==encoded(legacy)
    assert rows()==before

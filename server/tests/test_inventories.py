"""Complete metadata inventories and byte-bound library pages. Isolated fixtures only."""
import stat
from contextlib import contextmanager
from pathlib import Path

import pytest

from nxserver.filepackages import FilePackages
from nxserver.store import Store,canonical
from test_filepackages import packages,body,derive
from test_management import HEADERS,ERP_HEADERS,TOOL_DATABASE,DATABASE


def many_files(store,source,count=505):
    root=source/'UGII/templates';root.mkdir(parents=True,exist_ok=True)
    for index in range(count):(root/f'FILE_{index:04}.prt').write_bytes(b'Synthetic PRT '+str(index).encode())
    (root/'Straße_Halter.prt').write_bytes(b'Unicode synthetic PRT')
    return store.import_release('test','2512','Large metadata inventory','fixture')


def test_inventory_pages_search_all_files_and_never_read_assets_or_baseline(packages,monkeypatch):
    client,store,release,source,standard=packages;target=many_files(store,source)
    before=store.audit_entries();manager=FilePackages(store)
    def forbidden(*args,**kwargs):raise AssertionError('Inventory must not scan artifacts, assets or baseline.')
    monkeypatch.setattr(store,'manifest',forbidden);monkeypatch.setattr(store,'get_release',forbidden)
    monkeypatch.setattr(store,'asset',forbidden);monkeypatch.setattr('nxserver.filepackages.sha256',forbidden)
    pages=[manager.inventory(target['id'],'template',offset=offset,limit=100) for offset in range(0,506,100)]
    all_files=[item for page in pages for item in page['files']]
    assert len(all_files)==len({item['path'] for item in all_files})==506
    assert all(item['selectable'] and item['owner'] is None for item in all_files)
    assert all(page['total']==page['filtered']==506 and len(page['files'])<=100 for page in pages)
    assert len({page['manifest_sha256'] for page in pages})==len({page['ownership_sha256'] for page in pages})==1
    assert pages[0]['snapshot']['consistency']=='sqlite_read_transaction' and not pages[0]['snapshot']['files_verified']
    assert not pages[0]['snapshot']['baseline_compared'] and not pages[0]['snapshot']['automatic_approval']
    match=manager.inventory(target['id'],'template','file_0504')
    assert match['filtered']==1 and match['files'][0]['path'].endswith('FILE_0504.prt')
    assert manager.inventory(target['id'],'template','STRASSE')['filtered']==1
    assert manager.inventory(target['id'],'template',offset=999)['files']==[]
    assert store.audit_entries()==before


@pytest.mark.parametrize('querystring',['offset=-1','limit=0','limit=501','offset=1000001','query='+('a'*201)])
def test_inventory_api_bounds_and_authority(packages,querystring):
    client,store,release,*_=packages;path='/api/v1/releases/'+release['id']+'/file-packages?kind=template&'+querystring
    before=store.audit_entries()
    assert client.get(path,headers=HEADERS).status_code==422
    assert client.get(path,headers=ERP_HEADERS).status_code==403
    assert client.get(path).status_code==401
    assert store.audit_entries()==before


def test_inventory_retains_historical_owner_until_a_new_copy_removes_the_file(packages):
    client,store,release,*_=packages
    value=body(store);extra=store.add_asset('OTHER.prt',b'Synthetic additional PRT','fixture')
    value['asset_ids'].append(extra['id']);value['file_package']['entries'].append({'asset_id':extra['id'],'path':'UGII/templates/OTHER.prt'})
    value['file_package']['asset_hashes'][extra['id']]=extra['sha256'];resource=store.save_resource(value,'fixture')
    first=derive(store,release['id'])
    value['file_package']['entries']=value['file_package']['entries'][1:];value['file_package']['asset_hashes']={extra['id']:extra['sha256']}
    store.save_resource(value,'fixture',resource['id'],1);manager=FilePackages(store)
    page=manager.inventory(first['id'],'template','CUSTOM')
    assert page['files'][0]['owner']['resource_id']==resource['id'] and not page['files'][0]['selectable']
    report,_=manager.adoption_preview(first['id'],'template',['UGII/templates/CUSTOM.prt'])
    assert not report['can_adopt'] and 'already claimed' in str(report['conflicts'])
    second=derive(store,first['id']);assert manager.inventory(second['id'],'template','CUSTOM')['filtered']==0


def test_inventory_uses_one_read_snapshot_even_with_a_separate_writer(packages,monkeypatch):
    client,store,release,source,standard=packages;target=many_files(store,source,2)
    writer=Store(store.root);value=body(store,path='UGII/templates/FILE_0000.prt',filename='FILE_0000.prt',status='draft')
    before=FilePackages(store).inventory(target['id'],'template');original=store.db;triggered=[]
    class Proxy:
        def __init__(self,connection):self.connection=connection
        def execute(self,sql,*args):
            cursor=self.connection.execute(sql,*args)
            if sql.startswith('SELECT * FROM releases WHERE') and not triggered:
                triggered.append(True);writer.save_resource(value,'fixture')
            return cursor
    @contextmanager
    def pinned():
        with original() as connection:yield Proxy(connection)
    monkeypatch.setattr(store,'db',pinned)
    captured=FilePackages(store).inventory(target['id'],'template')
    assert triggered and captured==before
    monkeypatch.setattr(store,'db',original);after=FilePackages(store).inventory(target['id'],'template')
    assert after['files'][0]['owner'] and after['ownership_sha256']!=before['ownership_sha256']
    assert after['snapshot']['audit_head']['sequence']>before['snapshot']['audit_head']['sequence']


def test_unselectable_entries_are_explained_not_silently_hidden(packages):
    client,store,release,source,*_=packages;root=source/'UGII/templates';root.mkdir(parents=True)
    (root/'EMPTY.prt').write_bytes(b'')
    target=store.import_release('test','2512','Unselectable file sizes/names','fixture')
    # Metadata-only inventory must explain this name without materializing a
    # path beyond the Windows fixture's overall path-length budget.
    with store.db() as db:
        manifest=[*target['manifest'],{'path':'UGII/templates/'+('a'*121)+'.prt','size':1,'sha256':'0'*64}]
        db.execute('UPDATE releases SET manifest=? WHERE id=?',(canonical(manifest),target['id']))
    report=FilePackages(store).inventory(target['id'],'template')
    assert report['total']==2 and all(not item['selectable'] and item['unavailable_reason'] for item in report['files'])


@pytest.mark.parametrize('mutation',['manifest','resource','catalog','failed'])
def test_invalid_inventory_metadata_fails_without_writes(packages,mutation):
    client,store,release,*_=packages
    if mutation=='resource':store.save_resource({'kind':'gauge','name':'Synthetic','cam':{},'cam_status':'draft','asset_ids':[]},'fixture')
    with store.db() as db:
        if mutation=='resource':db.execute('UPDATE resources SET payload=?',('[1]',))
        elif mutation=='failed':db.execute('UPDATE releases SET status=? WHERE id=?',('failed',release['id']))
        else:db.execute('UPDATE releases SET '+mutation+'=? WHERE id=?',('{',release['id']))
    before=store.audit_entries();response=client.get('/api/v1/releases/'+release['id']+'/file-packages?kind=template',headers=HEADERS)
    assert response.status_code==409,response.text
    assert store.audit_entries()==before


def test_native_pages_preserve_complete_groups_and_are_manifest_bound(packages):
    client,store,release,source,*_=packages
    text='#CLASS MULTI\nFORMAT LIBRF RTYPE DESCR\n'
    text+=''.join(f'DATA |KEY_{index:04}|1|Root {index}\nDATA |KEY_{index:04}|2|Child {index}\n' for index in range(205))+'#END_DATA\n'
    (source/TOOL_DATABASE).write_text(text,encoding='utf-8');target=store.import_release('test','2512','Grouped native inventory','fixture')
    prefix='/api/v1/releases/'+target['id']+'/libraries/tool-metric';before=store.audit_entries()
    pages=[client.get(prefix,headers=HEADERS,params={'offset':offset,'limit':100}).json() for offset in (0,100,200)]
    assert [len(page['items']) for page in pages]==[100,100,5]
    assert all(len(item['cam']['records'])==2 for page in pages for item in page['items'])
    assert len({item['key'] for page in pages for item in page['items']})==205
    assert all(page['integrity_checked'] and page['scope']=='selected_native_library_bytes' and not page['automatic_approval'] for page in pages)
    assert client.get(prefix,headers=HEADERS,params={'query':'KEY_0204'}).json()['filtered']==1
    assert store.audit_entries()==before


@pytest.mark.parametrize('endpoint',['libraries/tool-metric','tool-schemas','tools'])
def test_changed_or_untracked_library_is_not_shown_as_managed(packages,endpoint):
    client,store,release,*_=packages;path=store.release_folder(release['id'])/TOOL_DATABASE
    before=store.audit_entries();path.write_text(DATABASE.replace('|10','|12'),encoding='utf-8')
    response=client.get('/api/v1/releases/'+release['id']+'/'+endpoint,headers=HEADERS)
    assert response.status_code==409,response.text
    assert store.audit_entries()==before


@pytest.mark.parametrize('endpoint',['libraries/tool-metric','tool-schemas','tools'])
def test_library_link_is_rejected_before_any_following_file_lookup(packages,monkeypatch,endpoint):
    client,store,release,*_=packages;candidate=store.release_folder(release['id'])/TOOL_DATABASE;linked=candidate.parent
    original=Path.lstat;is_file=Path.is_file;read=Path.read_bytes
    def lstat(path,*args,**kwargs):
        if path==linked:return type('LinkStat',(),{'st_mode':stat.S_IFLNK,'st_file_attributes':0})()
        return original(path,*args,**kwargs)
    def existence(path,*args,**kwargs):
        assert path!=candidate,'Following is_file happened before link guard'
        return is_file(path,*args,**kwargs)
    def content(path,*args,**kwargs):
        assert path!=candidate,'Following read happened before link guard'
        return read(path,*args,**kwargs)
    monkeypatch.setattr(Path,'lstat',lstat);monkeypatch.setattr(Path,'is_file',existence);monkeypatch.setattr(Path,'read_bytes',content)
    response=client.get('/api/v1/releases/'+release['id']+'/'+endpoint,headers=HEADERS)
    assert response.status_code==400 and 'links' in response.json()['detail']

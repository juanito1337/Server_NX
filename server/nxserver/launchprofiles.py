"""Explicit, release-bound UI-role selection; never changes a user's NX profile."""
import copy

from .filepackages import validate as validate_package, path_for
from .safety import DomainError


def validate_profile(profile, manifest):
    """Validate the immutable launcher contract, including historical refreshes."""
    if profile is None:
        return None
    required={'contract_version','resource_id','revision','name','nx_version','path','size','sha256','package_sha256'}
    if not isinstance(profile,dict) or set(profile)!=required or type(profile['contract_version']) is not int or profile['contract_version']!=1 or type(profile['revision']) is not int or profile['revision']<1 or profile['nx_version']!='2512':
        raise DomainError('Invalid stored NX UI-role contract.',409)
    import re,uuid
    try:
        if str(uuid.UUID(profile['resource_id']))!=profile['resource_id']:raise ValueError()
    except (ValueError,TypeError,AttributeError) as error:
        raise DomainError('Invalid stored NX UI-role resource.',409) from error
    if not isinstance(profile['name'],str) or not 1<=len(profile['name'])<=200 or type(profile['size']) is not int or not 1<=profile['size']<=20*1024*1024 or any(not isinstance(profile[key],str) or not re.fullmatch('[a-f0-9]{64}',profile[key]) for key in ('sha256','package_sha256')):
        raise DomainError('Invalid stored NX UI-role metadata.',409)
    if not isinstance(profile['path'],str):raise DomainError('Invalid stored NX UI-role path.',409)
    path_for('ui_standard',profile['path'])
    if not profile['path'].startswith('UGII/menus/roles/') or not profile['path'].lower().endswith('.mtx'):
        raise DomainError('Select an MTX role in UGII/menus/roles, not an arbitrary profile file.',409)
    rows=[row for row in manifest if row['path']==profile['path']]
    if len(rows)!=1 or rows[0]['size']!=profile['size'] or rows[0]['sha256']!=profile['sha256']:
        raise DomainError('NX UI role is not bound to exactly this release manifest.',409)
    return copy.deepcopy(profile)


def context(release,resource):
    """The precise metadata whose replacement would change this selection."""
    return [*[release[key] for key in ('id','nx_version','status','folder','parent_id','manifest','catalog')],
            release.get('test_launcher'),resource]


class LaunchProfiles:
    def __init__(self,store):self.store=store

    def preview(self,release_id,resource_id=None,path=None):
        from .store import digest
        with self.store.lock:
            release=self.store.get_release(release_id)
            if release['nx_version']!='2512' or release['status'] in ('building','failed'):
                raise DomainError('Select a completed NX2512 release for the UI-role test.',409)
            folder=self.store.release_folder(release_id)
            if self.store.manifest(folder)!=release['manifest']:
                raise DomainError('Release changed outside the API; UI-role selection blocked.',409)
            resource=None;profile=None
            if (resource_id is None)!=(path is None):raise DomainError('Choose both a UI-standard resource and its role path, or explicitly neither.')
            if resource_id is not None:
                resource=next((item for item in self.store.resources() if item['id']==resource_id),None)
                if resource is None:raise DomainError('UI-standard resource not found.',404)
                if resource['kind']!='ui_standard' or resource['cam_status']!='approved':
                    raise DomainError('Only an explicitly approved UI-standard resource can supply a test role.',409)
                package=validate_package(self.store,resource,old=resource)
                if not package or package['nx_version']!='2512':raise DomainError('Qualify the UI package for NX2512 before selecting its role.',409)
                entry=next((item for item in package['entries'] if item['path']==path),None)
                if not entry:raise DomainError('The selected role is not in this UI package.',409)
                metadata,_=self.store.asset(entry['asset_id'])
                profile={'contract_version':1,'resource_id':resource_id,'revision':resource['revision'],'name':resource['name'],
                    'nx_version':'2512','path':path,'size':metadata['size'],'sha256':metadata['sha256'],'package_sha256':digest(package)}
                validate_profile(profile,release['manifest'])
                exported=(release['catalog'] or {}).get('exported_file_packages',{}).get(resource_id)
                if not exported or exported.get('revision')!=resource['revision'] or exported.get('nx_version')!='2512' or exported.get('qualification_retained') is False or not any(item['path']==path and item['size']==metadata['size'] and item['sha256']==metadata['sha256'] for item in exported.get('entries',[])):
                    raise DomainError('Export this qualified UI revision into a new release before selecting it; ownership alone is not qualification.',409)
            report={'contract_version':1,'release_id':release_id,'nx_version':'2512','ui_profile':profile,
                'manifest_sha256':digest(release['manifest']),'context_sha256':digest(context(release,resource)),
                'previous_package_revision':(release.get('test_launcher') or {}).get('package_revision'),
                'can_prepare':True,'automatic_nx_start':False,'automatic_approval':False,
                'user_profiles_changed':False,'nx_files_changed':False,'mode':'isolated_ui_role_test'}
            report['preview_hash']=digest(report)
            return report

    def prepare(self,release_id,resource_id,path,preview_hash,actor):
        from .store import canonical,digest
        with self.store.lock:
            report=self.preview(release_id,resource_id,path)
            release=self.store.get_release(release_id)
            previous=release['test_launcher']
            # An exact retry reuses its immutable artifact; no second audit/write.
            if previous and previous.get('profile_preview_hash')==preview_hash and previous.get('ui_profile')==report['ui_profile']:
                self.store.test_launcher_package(release_id)
                return previous
            if report['preview_hash']!=preview_hash:raise DomainError('UI-role selection changed; repeat the preview.',409)
            launcher=self.store._create_test_launcher(release_id,'2512',self.store.release_folder(release_id),release['manifest'],ui_profile=report['ui_profile'])
            launcher['profile_preview_hash']=preview_hash
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT * FROM releases WHERE id=?',(release_id,)).fetchone()
                saved=db.execute('SELECT payload FROM test_launchers WHERE release_id=?',(release_id,)).fetchone()
                current=self.store._release_value(row,saved['payload'] if saved else None) if row else None
                resource=None
                if resource_id:
                    row=db.execute('SELECT * FROM resources WHERE id=?',(resource_id,)).fetchone()
                    resource=self.store._resource_value(row) if row else None
                if current is None or digest(context(current,resource))!=report['context_sha256'] or self.store.manifest(self.store.release_folder(release_id))!=release['manifest']:
                    raise DomainError('UI-role context changed before package commit; the previous launcher remains selected.',409)
                db.execute('INSERT INTO test_launchers(release_id,payload) VALUES(?,?) ON CONFLICT(release_id) DO UPDATE SET payload=excluded.payload',(release_id,canonical(launcher)))
                self.store.audit(db,actor,'release.test_launcher.ui_profile',{'previous':previous,'current':launcher,'preview_hash':preview_hash})
            return launcher

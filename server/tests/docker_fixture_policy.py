"""Pure allowlist for the disposable Docker integration test, not a daemon check."""
import copy
import re


def fixture_profile(project, port, root, build_root):
    root = str(root).replace('\\', '/').rstrip('/')
    service = {
        'image': project + ':isolated',
        'build': {'context': str(build_root), 'dockerfile': 'Dockerfile'},
        'ports': [{'host_ip':'127.0.0.1', 'published':str(port), 'target':8765, 'protocol':'tcp', 'mode':'ingress'}],
        'environment': {'NX_ADMIN_TOKEN_FILE':'/run/secrets/nx_admin_token'},
        'secrets': [{'source':'nx_admin_token', 'target':'nx_admin_token'}],
        'volumes': [{'type':'volume', 'source':'management-data', 'target':'/data', 'read_only':False}],
        'read_only': True, 'user':'10001:10001', 'tmpfs':['/tmp:size=64m,mode=1777'],
        'cap_drop':['ALL'], 'security_opt':['no-new-privileges:true'], 'init':True,
        'restart':'no', 'stop_grace_period':'30s', 'networks':{'default':None},
        'logging': {'driver':'json-file', 'options':{'max-size':'10m', 'max-file':'3'}},
    }
    for source, target, readonly in [('settings.json','/app/config/settings.json',True),
                                     ('source','/inputs/fixture',True), ('test-releases','/data/r',False)]:
        service['volumes'].append({'type':'bind','source':root+'/'+source,'target':target,
                                  'read_only':readonly,'bind':{'create_host_path':False}})
    return {'name':project, 'services':{'nxserver':service},
            'volumes':{'management-data':{'name':project+'_management-data'}},
            'networks':{'default':{'name':project+'_default'}},
            'secrets':{'nx_admin_token':{'file':root+'/token.txt'}}}


def validate_fixture_profile(configuration, project, port, root, build_root):
    """Return a fresh exact profile only after checking names, mounts and runtime.

    No filesystem/daemon access; fixture roots come from the test's own tmp_path.
    This does not claim symlink detection or authorize a production deployment.
    """
    def fail():
        raise ValueError('Unapproved Docker integration-test profile; no start or cleanup authorized.')

    def keys(value, allowed):
        if type(value) is not dict or set(value) - set(allowed):
            fail()

    def path(value):
        if not isinstance(value, str) or not value or '$' in value or '\x00' in value:
            fail()
        value = value.replace('\\', '/')
        if value.startswith('//') or '..' in value.split('/') or re.match(r'(?i)^W:', value) or value.lower().startswith('/mnt/w'):
            fail()
        if re.match(r'^[A-Za-z]:/', value):
            return value.rstrip('/').casefold()
        if not value.startswith('/'):
            fail()
        return value.rstrip('/')

    if not isinstance(project,str) or not re.fullmatch(r'nx-docker-test-[a-f0-9]{12}', project) or type(port) is not int or not 1 <= port <= 65535:
        fail()
    expected = fixture_profile(project, port, root, build_root)
    value = copy.deepcopy(configuration)
    keys(value, expected)
    for group in ('services','volumes','networks','secrets'):
        keys(value.get(group), expected[group])
        if set(value[group]) != set(expected[group]):
            fail()
    service = value['services']['nxserver']
    keys(service, expected['services']['nxserver'])
    for flag in ('init','read_only'):
        if type(service.get(flag)) is not bool:
            fail()
    keys(service.get('build'), ('context','dockerfile'))
    if path(service['build'].get('context')) != path(str(build_root)):
        fail()
    service['build']['context'] = str(build_root)
    if service.get('security_opt') == ['no-new-privileges']:
        service['security_opt'] = ['no-new-privileges:true']
    ports = service.get('ports')
    if type(ports) is not list or len(ports) != 1:
        fail()
    keys(ports[0], expected['services']['nxserver']['ports'][0])
    if type(ports[0].get('target')) is not int or type(ports[0].get('published')) not in (str,int):
        fail()
    ports[0]['published'] = str(ports[0]['published'])
    if ports[0].get('mode') is None:
        ports[0]['mode'] = 'ingress'
    for group, name, driver in [('volumes','management-data','local'), ('networks','default','bridge')]:
        item = value[group][name]
        keys(item, ('name','driver'))
        if 'driver' in item:
            if item.pop('driver') != driver:
                fail()
    secret = value['secrets']['nx_admin_token']
    keys(secret, ('file','name'))
    if 'name' in secret and secret.pop('name') != project+'_nx_admin_token':
        fail()
    if path(secret.get('file')) != path(str(root).replace('\\','/').rstrip('/')+'/token.txt'):
        fail()
    secret['file'] = expected['secrets']['nx_admin_token']['file']
    secrets = service.get('secrets')
    if type(secrets) is not list or len(secrets) != 1:
        fail()
    keys(secrets[0], ('source','target'))
    if secrets[0].get('target') in (None,'/run/secrets/nx_admin_token'):
        secrets[0]['target'] = 'nx_admin_token'
    mounts = service.get('volumes')
    if type(mounts) is not list or len(mounts) != 4:
        fail()
    allowed = {item['target']:item for item in expected['services']['nxserver']['volumes']}
    seen = set()
    for item in mounts:
        keys(item, ('type','source','target','read_only','bind'))
        target = item.get('target')
        if not isinstance(target,str) or target not in allowed or target in seen:
            fail()
        seen.add(target)
        if 'read_only' in item and type(item['read_only']) is not bool:
            fail()
        item.setdefault('read_only',False)
        if target != '/data':
            if path(item.get('source')) != path(allowed[target]['source']):
                fail()
            item['source'] = allowed[target]['source']
            keys(item.get('bind'), ('create_host_path',))
            if type(item['bind'].get('create_host_path')) is not bool:
                fail()
    service['volumes'].sort(key=lambda item:item['target'])
    expected['services']['nxserver']['volumes'].sort(key=lambda item:item['target'])
    if value != expected:
        fail()
    return expected

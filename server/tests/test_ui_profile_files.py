"""Versioned NX UI profile bytes at their designated paths, no real NX."""
import base64
import copy
from pathlib import Path

import pytest

from nxserver.filepackages import CHECKS, FilePackages, UI_PROFILE_SUFFIXES, path_for
from nxserver.safety import DomainError
from test_filepackages import packages, derive
from test_libraries import write
from test_management import HEADERS

EXTENSIONS = ['.ddb', '.rtb', '.grb', '.gly', '.csb', '.abr', '.stb', '.dtx', '.crb']


def upload(client, filename, content):
    response = client.post('/api/v1/assets', headers=HEADERS,
        json={'filename': filename, 'content_base64': base64.b64encode(content).decode('ascii')})
    assert response.status_code == 201, response.text
    return response.json()


def payload(asset, path):
    return {'kind': 'ui_standard', 'name': 'Synthetic UI profile package', 'cam': {}, 'erp': None,
        'cam_status': 'draft', 'asset_ids': [asset['id']],
        'file_package': {'nx_version': '2512', 'entries': [{'asset_id': asset['id'], 'path': path}],
            'checks': {key: False for key in CHECKS}, 'asset_hashes': {}, 'evidence': '', 'provenance': None}}


def test_profile_policy_is_exactly_the_observed_nine_data_types():
    assert UI_PROFILE_SUFFIXES == set(EXTENSIONS)


@pytest.mark.parametrize('extension', EXTENSIONS)
def test_profile_upload_preflight_draft_qualification_export_and_retirement_preserve_bytes(packages, extension):
    client, store, parent, *_ = packages
    name = 'Synthetic' + extension
    path = 'UGII/menus/profiles/All/' + name
    content = b'\x00\x81Synthetic UI definition; not NX code or qualification\r\n'
    before = store.manifest(store.release_folder(parent['id']))
    asset = upload(client, name, content)
    value = payload(asset, path)
    check = client.post('/api/v1/file-packages/preflight', headers=HEADERS, json={
        'kind': value['kind'], 'nx_version': '2512', 'asset_ids': value['asset_ids'], 'entries': value['file_package']['entries']})
    assert check.status_code == 200, check.text
    report = check.json()
    assert report['can_bind'] and report['files'][0]['bytes_verified'] and report['files'][0]['xml_checked'] is False
    assert not report['automatic_approval'] and not report['nx_validation'] and not report['dependency_validation']
    response = client.post('/api/v1/resources', headers=HEADERS, json=value)
    assert response.status_code == 201, response.text
    resource = response.json()
    assert resource['cam_status'] == 'draft'
    unqualified = {**copy.deepcopy(value), 'cam_status': 'approved', 'expected_revision': resource['revision']}
    response = client.put('/api/v1/resources/' + resource['id'], headers=HEADERS, json=unqualified)
    assert response.status_code == 400 and store.get_resource(resource['id']) == resource
    qualified = copy.deepcopy(unqualified)
    qualified['file_package'].update(checks={key: True for key in CHECKS},
        asset_hashes={asset['id']: asset['sha256']}, evidence='Synthetic four-check contract only; NOT a real NX qualification.')
    response = client.put('/api/v1/resources/' + resource['id'], headers=HEADERS, json=qualified)
    assert response.status_code == 200, response.text
    resource = response.json()
    exported = derive(store, parent['id'])
    destination = store.release_folder(exported['id']) / path
    assert destination.read_bytes() == content
    assert store.manifest(store.release_folder(parent['id'])) == before
    retired = copy.deepcopy(qualified)
    retired.update(cam_status='retired', expected_revision=resource['revision'])
    response = client.put('/api/v1/resources/' + resource['id'], headers=HEADERS, json=retired)
    assert response.status_code == 200, response.text
    newer = derive(store, exported['id'])
    assert not (store.release_folder(newer['id']) / path).exists()
    assert destination.read_bytes() == content  # Historical release and attachment retained.
    assert store.asset(asset['id'])[1].read_bytes() == content
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('extension', EXTENSIONS)
def test_existing_profile_inventory_and_adoption_include_the_new_data_types(packages, extension):
    _, store, _, source, standard = packages
    path = 'UGII/menus/profiles/All/Synthetic' + extension
    write(source, path, 'Synthetic existing UI definition')
    release = store.import_release('test', '2512', 'Synthetic profile source', 'fixture')
    before = store.manifest(store.release_folder(release['id']))
    service = FilePackages(store)
    inventory = service.inventory(release['id'], 'ui_standard')
    assert inventory['total'] == 1 and inventory['files'][0]['path'] == path
    assert inventory['files'][0]['selectable'] and not inventory['snapshot']['files_verified']
    report, _ = service.adoption_preview(release['id'], 'ui_standard', [path])
    assert not report['can_adopt']
    assert 'baseline directory is missing' in report['conflicts'][0]['reason']
    assert store.resources() == [] and store.asset_inventory()['total'] == 0
    # An explicitly configured, present baseline is still required. The new
    # formats must not turn absent stock into assumed customer ownership.
    (standard / 'UGII/menus/profiles').mkdir(parents=True)
    report, _ = service.adoption_preview(release['id'], 'ui_standard', [path])
    assert report['can_adopt'], report['conflicts']
    resource = service.adopt(release['id'], 'ui_standard', [path], 'Synthetic adopted UI', None, report['preview_hash'], 'fixture')
    assert resource['cam_status'] == 'draft' and resource['file_package']['entries'][0]['path'] == path
    assert not any(resource['file_package']['checks'].values())
    assert store.manifest(store.release_folder(release['id'])) == before and store.active()['release_id'] is None


@pytest.mark.parametrize('kind,root', [
    ('ui_standard', 'UGII/menus/roles/'), ('ui_standard', 'UGII/menus/profiles_unapproved/'),
    ('template', 'UGII/templates/'), ('machine', 'MACH/resource/library/machine/installed_machines/'),
    ('fixture', 'MACH/resource/library/fixture_automation/graphics/')])
@pytest.mark.parametrize('extension', EXTENSIONS)
def test_profile_extensions_do_not_broaden_other_package_roots(kind, root, extension):
    with pytest.raises(DomainError):
        path_for(kind, root + 'Synthetic' + extension)


@pytest.mark.parametrize('extension', EXTENSIONS)
def test_stock_profile_bytes_and_changed_baseline_cannot_be_claimed(packages, extension):
    _, store, _, source, standard = packages
    path = 'UGII/menus/profiles/All/Stock' + extension
    for root in (source, standard):
        write(root, path, 'Synthetic stock UI definition')
    release = store.import_release('test', '2512', 'Synthetic stock profiles', 'fixture')
    before = store.manifest(store.release_folder(release['id']))
    service = FilePackages(store)
    report, _ = service.adoption_preview(release['id'], 'ui_standard', [path])
    assert not report['can_adopt']
    assert 'Unchanged Siemens/SIDT standard' in report['conflicts'][0]['reason']
    write(standard, path, 'Synthetic different stock')
    reviewed, _ = service.adoption_preview(release['id'], 'ui_standard', [path])
    assert reviewed['can_adopt']
    write(standard, path, 'Synthetic stock UI definition')
    with pytest.raises(DomainError, match='preview changed or has conflicts'):
        service.adopt(release['id'], 'ui_standard', [path], 'Not customer owned',
            None, reviewed['preview_hash'], 'fixture')
    assert store.resources() == [] and store.asset_inventory()['total'] == 0
    assert store.manifest(store.release_folder(release['id'])) == before
    assert store.active()['release_id'] is None


@pytest.mark.parametrize('extension', ['.exe', '.dll', '.py', '.ps1', '.bat', '.cmd', '.jar', '.so'])
def test_executable_types_remain_blocked(packages, extension):
    client, store, *_ = packages
    response = client.post('/api/v1/assets', headers=HEADERS,
        json={'filename': 'Synthetic' + extension, 'content_base64': 'c3ludGhldGlj'})
    assert response.status_code == 400 and store.asset_inventory()['total'] == 0
    with pytest.raises(DomainError):
        path_for('ui_standard', 'UGII/menus/profiles/Synthetic' + extension)


def test_profile_extension_case_and_original_basename_still_apply(packages):
    client, store, *_ = packages
    asset = upload(client, 'Original.GRB', b'Synthetic uppercase UI definition')
    path = 'UGII/menus/profiles/All/Original.GRB'
    assert path_for('ui_standard', path) == path
    value = payload(asset, path.replace('Original.GRB', 'Renamed.GRB'))
    response = client.post('/api/v1/resources', headers=HEADERS, json=value)
    assert response.status_code == 400 and store.resources() == []


def test_browser_file_picker_includes_all_profile_data_types():
    from html.parser import HTMLParser

    class Picker(HTMLParser):
        def handle_starttag(self, tag, attrs):
            data = dict(attrs)
            if data.get('id') == 'assetfile':
                self.extensions = set(data['accept'].split(','))

    picker = Picker()
    picker.feed((Path(__file__).parents[1] / 'nxserver/web/index.html').read_text(encoding='utf-8'))
    assert set(EXTENSIONS) <= picker.extensions
    assert not {'.exe', '.dll', '.ps1', '.py'} & picker.extensions

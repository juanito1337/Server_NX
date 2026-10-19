"""STEP text/API and NX contract tests. Doubles do NOT validate real CAD/NX."""
import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.geometry import GeometryJobs
from nxserver import nxworker
from nxserver.safety import DomainError, offline_path
from nxserver.stepfiles import preflight
from nxserver.store import Store

ADMIN = 'geometry-isolated-test-token-' + 'a' * 40
ERP = 'geometry-isolated-erp-token-' + 'b' * 40
HEADERS = {'Authorization': 'Bearer ' + ADMIN}
STEP = b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('isolated text fixture'),'2;1');\nFILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\nENDSEC;\nDATA;\n#1=MANIFOLD_SOLID_BREP('cut',$);\n#2=MANIFOLD_SOLID_BREP('shank',$);\nENDSEC;\nEND-ISO-10303-21;"


@pytest.fixture
def pilot(tmp_path):
    store = Store(tmp_path / 'managed')
    with TestClient(create_app(store, ADMIN, ERP)) as client:
        yield client, store


def draft(client, content=STEP, kind='tool', filename='vendor.step'):
    asset = client.post('/api/v1/assets', headers=HEADERS, json={'filename': filename,
        'content_base64': base64.b64encode(content).decode()}).json()
    payload = {'kind': kind, 'name': 'Isolated test tool', 'cam_status': 'draft',
               'cam': {}, 'asset_ids': [asset['id']]}
    resource = client.post('/api/v1/resources', headers=HEADERS, json=payload).json()
    return resource, asset


def create_job(client, resource, asset, **extra):
    return client.post('/api/v1/geometry-jobs', headers=HEADERS, json={
        'resource_id': resource['id'], 'expected_revision': resource['revision'],
        'asset_id': asset['id'], 'tool_type': 'drilling_solid', **extra})


def report_for(job):
    return {'contract_version': 1, 'job_id': job['id'], 'resource_id': job['resource_id'],
            'resource_revision': job['resource_revision'], 'nx_version': '2512',
            'input_sha256': job['input']['sha256'], 'tool_type': job['tool_type'],
            'status': 'needs_selection', 'automatic_cam_approval': False,
            'resource_hash': job['resource_hash'], 'worker_hashes': job['worker_hashes'],
            'parts': [{'path': path, 'units': 'Millimeters', 'fully_loaded': True,
                'bodies': [{'id': 'BODY '+str(index), 'name': 'Synthetic', 'solid': True,
                            'convergent': False, 'face_count': 6, 'layer': 1, 'color': 42}]}
                for index,path in enumerate(('tool.prt','component.prt'))],
            'components': [{'id': '/0', 'name': 'Synthetic component', 'part': 'component.prt'}],
            'files': [{'path': path, 'size': 10, 'sha256': hashlib.sha256(path.encode()).hexdigest()}
                for path in ('tool.prt','component.prt')], 'errors': [], 'warnings': []}


@pytest.mark.parametrize('schema,protocol', [('CONFIG_CONTROL_DESIGN', '203'), ('AUTOMOTIVE_DESIGN', '214'),
    ('AP203_CONFIGURATION_CONTROLLED_3D_DESIGN_OF_MECHANICAL_PARTS_AND_ASSEMBLIES_MIM_LF', '203'),
    ('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF', '242'),
    ('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING { 1 0 10303 442 1 1 4 }', '242')])
def test_step_protocol_detection_is_not_cad_validation(schema, protocol):
    result = preflight(STEP.replace(b'AUTOMOTIVE_DESIGN', schema.encode()))
    assert result['protocol'] == protocol
    assert result['entity_hints']['MANIFOLD_SOLID_BREP'] == 2
    assert not result['geometry_validated'] and not result['units_verified']


@pytest.mark.parametrize('content', [b'not step', STEP[:-10], STEP + b'\0',
    STEP.replace(b'AUTOMOTIVE_DESIGN', b'UNSUPPORTED_SCHEMA'),
    STEP.replace(b'AUTOMOTIVE_DESIGN', b"AUTOMOTIVE_DESIGN','CONFIG_CONTROL_DESIGN"),
    STEP.replace(b'DATA;', b'REFERENCE;\nENDSEC;\nDATA;'),
    STEP.replace(b'DATA;', b'DATA;\n#3=EXTERNAL_SOURCE($);'),
    STEP.replace(b"'cut'", b"'../../escape'"),
    STEP.replace(b"'cut'", b"'W:\\SiemensShare\\nx2406\\tool'"),
    STEP.replace(b'DATA;', b'DATA;\nENDSEC;\nDATA;')])
def test_step_preflight_rejects_incomplete_ambiguous_external_and_pathlike_data(content):
    with pytest.raises(DomainError):
        preflight(content)


def test_step_metadata_and_comments_are_not_dereferenced():
    content = STEP.replace(b"FILE_DESCRIPTION(('isolated text fixture'),'2;1');",
        b"FILE_NAME('W:\\not-read.step',$,$,$,$,$,$); /* REFERENCE; EXTERNAL_SOURCE($); */")
    result = preflight(content)
    assert result['protocol'] == '214'
    assert preflight(STEP.replace(b"'cut'", b"'REFERENCE; EXTERNAL_SOURCE'"))['protocol'] == '214'
    misleading = STEP.replace(b"'isolated text fixture'", b"'DATA; ENDSEC; HEADER;' ")
    assert preflight(misleading)['protocol'] == '214'
    with pytest.raises(DomainError, match='Path-like'):
        preflight(misleading.replace(b"'cut'", b"'../escape'"))
    with pytest.raises(DomainError, match='Reserved Windows'):
        preflight(STEP.replace(b"'cut'", b"'CON.prt'"))


def test_job_package_is_pinned_and_does_not_change_resource_or_assets(pilot):
    client, store = pilot
    resource, asset = draft(client)
    _, source = store.asset(asset['id'])
    original = source.read_bytes()
    result = create_job(client, resource, asset)
    assert result.status_code == 201, result.text
    job = result.json()
    assert job['status'] == 'awaiting_nx'
    assert client.get('/api/v1/assets/' + asset['id'] + '/step-preview', headers=HEADERS).json()['protocol'] == '214'
    url = '/api/v1/geometry-jobs/' + job['id']
    response = client.get(url + '/package', headers=HEADERS)
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {'input.step', 'job.json', 'LIESMICH.txt', 'nx_step_import.py', 'nx_safety.py', 'stepfiles.py'}
        assert archive.read('input.step') == original
        contract = json.loads(archive.read('job.json'))
        assert contract['resource_revision'] == 1 and contract['input']['sha256'] == hashlib.sha256(original).hexdigest()
        for filename, expected in contract['worker_hashes'].items():
            assert hashlib.sha256(archive.read(filename)).hexdigest() == expected
        assert 'NXCAM3DToolAssistant.dll' in archive.read('LIESMICH.txt').decode('utf-8-sig')
        assert all(ADMIN.encode() not in archive.read(name) for name in archive.namelist())
    assert source.read_bytes() == original
    assert store.resources()[0]['revision'] == 1
    assert store.resources()[0]['cam_status'] == 'draft'
    assert not (store.root / 'r').exists()
    assert not store.active()['release_id']
    assert client.get('/api/v1/geometry-jobs?resource_id=' + resource['id'], headers=HEADERS).json()[0]['id'] == job['id']


def test_geometry_auth_and_type_revision_attachment_gates(pilot):
    client, _ = pilot
    resource, asset = draft(client)
    assert create_job(client, resource, asset, expected_revision=2).status_code == 409
    assert create_job(client, resource, asset, tool_type='guess_from_filename').status_code == 400
    machine, _ = draft(client, kind='machine')
    assert create_job(client, machine, asset).status_code == 400
    other, other_asset = draft(client)
    assert create_job(client, resource, other_asset).status_code == 400
    invalid, invalid_asset = draft(client, content=b'not STEP')
    assert create_job(client, invalid, invalid_asset).status_code == 400
    wrong, wrong_asset = draft(client, filename='vendor.prt')
    assert create_job(client, wrong, wrong_asset).status_code == 400
    assert client.get('/api/v1/geometry-jobs').status_code == 401
    assert client.get('/api/v1/geometry-jobs', headers={'Authorization': 'Bearer ' + ERP}).status_code == 403
    job = create_job(client, resource, asset).json()
    assert client.get('/api/v1/geometry-jobs/' + job['id'] + '/package').status_code == 401
    assert client.get('/api/v1/geometry-jobs/' + job['id'] + '/package', headers={'Authorization': 'Bearer ' + ERP}).status_code == 403


def test_reports_are_diagnostics_not_cam_approval_and_cannot_overwrite(pilot):
    client, store = pilot
    resource, asset = draft(client)
    job = create_job(client, resource, asset).json()
    report = report_for(job)
    url = '/api/v1/geometry-jobs/' + job['id'] + '/report'
    for changed in ({'automatic_cam_approval': True}, {'status': 'validated'}, {'input_sha256': '0' * 64},
                    {'resource_revision': 2}, {'resource_revision': True}, {'parts': 'not a list'}):
        assert client.post(url, headers=HEADERS, json={'report': {**report, **changed}}).status_code == 400
    result = client.post(url, headers=HEADERS, json={'report': report})
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'needs_selection'
    assert client.post(url, headers=HEADERS, json={'report': report}).status_code == 200
    assert client.post(url, headers=HEADERS, json={'report': {**report, 'warnings': ['different run']}}).status_code == 409
    assert store.resources()[0]['revision'] == 1 and store.resources()[0]['cam_status'] == 'draft'
    assert store.audit_entries()[0]['action'] == 'geometry.report.received'
    assert client.post('/api/v1/resources/' + resource['id'], headers=HEADERS).status_code == 405


def test_resource_or_attachment_change_invalidates_report(pilot):
    client, store = pilot
    resource, asset = draft(client)
    job = create_job(client, resource, asset).json()
    payload = {k: v for k, v in resource.items() if k not in {'id', 'revision', 'updated_at'}}
    update = client.put('/api/v1/resources/' + resource['id'], headers=HEADERS,
                        json={**payload, 'expected_revision': 1, 'name': 'Changed'})
    assert update.status_code == 200, update.text
    assert client.post('/api/v1/geometry-jobs/' + job['id'] + '/report', headers=HEADERS,
                       json={'report': report_for(job)}).status_code == 409
    _, path = store.asset(asset['id'])
    path.write_bytes(STEP + b'changed')
    assert client.get('/api/v1/geometry-jobs/' + job['id'] + '/package', headers=HEADERS).status_code == 409


@pytest.mark.parametrize('solid_count,components,status', [(0, [], 'needs_structure'), (1, [{}], 'needs_structure'),
                                                          (2, [], 'needs_structure'), (2, [{}], 'needs_selection')])
def test_worker_structure_rules_do_not_guess_cutting_bodies(solid_count, components, status):
    parts = [{'fully_loaded': True, 'bodies': [{'solid': True} for _ in range(solid_count)]}]
    result = nxworker.summarize(parts, components, 'drilling_solid')
    assert result['status'] == status
    assert not result['automatic_cam_approval']


def test_turning_does_not_require_two_solids_but_still_needs_manual_orientation():
    result = nxworker.summarize([{'fully_loaded': True, 'bodies': [{'solid': True}]}], [{}], 'turning_standard')
    assert result['status'] == 'needs_selection'
    assert result['warnings']


@pytest.mark.parametrize('value', ['W:\\SiemensShare\\nx2406', '\\\\server\\share', 'C:\\tmp;W:\\prod', '/mnt/w/prod'])
def test_worker_rejects_network_environment_before_lookup(value, monkeypatch):
    monkeypatch.setattr(nxworker, 'offline_path', lambda _: pytest.fail('Filesystem touched before network rejection.'))
    session = SimpleNamespace(GetEnvironmentVariableValue=lambda key: value if key == 'UGII_CAM_RESOURCE_DIR' else 'C:\\Siemens\\NX2512' if key == 'UGII_BASE_DIR' else '')
    with pytest.raises(DomainError, match='network/production'):
        nxworker.check_environment(session)


def test_downloaded_worker_contract_with_api_doubles_only(pilot, tmp_path, monkeypatch):
    client, _ = pilot
    resource, asset = draft(client)
    job = create_job(client, resource, asset).json()
    directory = tmp_path / 'job'
    directory.mkdir()
    package = client.get('/api/v1/geometry-jobs/' + job['id'] + '/package', headers=HEADERS).content
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        archive.extractall(directory)
    base = tmp_path / 'installation'
    settings = base / 'STEP214UG/step214ug.def'
    settings.parent.mkdir(parents=True)
    settings.write_text('LOG_FILE = source.log\n', encoding='utf-8')
    monkeypatch.setattr(nxworker, 'job_directory', offline_path)
    monkeypatch.setattr(nxworker, 'check_environment', lambda _: base)
    calls = []
    class Importer:
        ObjectTypes = SimpleNamespace()
        def SetMode(self, mode): calls.append(('mode', mode))
        def Commit(self):
            assert self.ProcessHoldFlag and not self.FlattenAssembly and not self.ImportToTeamcenter
            assert not self.SimplifyGeometry and not self.SewSurfaces and not self.Optimize
            assert self.PartUnit == 'not-set'
            Path(self.OutputFile).write_bytes(b'fake PRT only for contract tests')
            return None
        def Destroy(self): calls.append('destroyed')
    body = SimpleNamespace(JournalIdentifier='BODY 1', Name='body', IsSolidBody=True,
                           IsConvergentBody=False, GetFaces=lambda: [], Layer=1, Color=42)
    part = SimpleNamespace(FullPath=str(directory / 'output/tool.prt'), PartUnits='Millimeters',
                           IsFullyLoaded=True, Bodies=[body], ComponentAssembly=SimpleNamespace(RootComponent=None))
    class Parts:
        Work = Display = None
        def __init__(self): self.opened = False
        def __iter__(self): return iter([part] if self.opened else [])
        def OpenBaseDisplay(self, filename):
            self.opened = True
            return part, SimpleNamespace(NumberUnloadedParts=0, Dispose=lambda: calls.append('disposed'))
    session = SimpleNamespace(Parts=Parts(), IsCamSessionInitialized=lambda: False,
        DexManager=SimpleNamespace(CreateStep214Importer=lambda: Importer()))
    nx = SimpleNamespace(BaseImporter=SimpleNamespace(Mode=SimpleNamespace(NativeFileSystem='native'),
        PartUnitOption=SimpleNamespace(NotSet='not-set')), Step214Importer=SimpleNamespace(ImportToOption=SimpleNamespace(NewPart='new')))
    before = Path.cwd()
    result = nxworker.run_job(directory, session, nx)
    assert result['status'] == 'needs_structure' and not result['automatic_cam_approval']
    assert Path.cwd() == before
    assert 'destroyed' in calls and 'disposed' in calls
    assert (directory / 'output/report.json').is_file()
    assert (directory / 'input.step').read_bytes() == STEP
    with pytest.raises(DomainError, match='empty NX session'):
        nxworker.run_job(directory, session, nx)
    session.Parts = Parts()
    with pytest.raises(FileExistsError):
        nxworker.run_job(directory, session, nx)
    # Successful contract simulation is explicitly NOT a real NX validation.
    result['installation'] = 'isolated test double only'
    response = client.post('/api/v1/geometry-jobs/' + job['id'] + '/report', headers=HEADERS, json={'report': result})
    assert response.status_code == 200, response.text


def test_worker_software_changes_preserve_the_original_job_package(pilot, monkeypatch):
    client, store = pilot
    resource, asset = draft(client)
    job = create_job(client, resource, asset).json()
    original = GeometryJobs(store).package(job['id'])
    monkeypatch.setattr('nxserver.geometry.worker_sources', lambda: {'nx_step_import.py': b'changed'})
    assert GeometryJobs(store).package(job['id']) == original
    assert hashlib.sha256(original).hexdigest() == job['package_sha256']


def test_translator_settings_localize_seeds_and_preserve_source_units(tmp_path):
    template = tmp_path / 'installed'
    template.mkdir()
    seed = template / 'null_mm.prt'
    seed.write_bytes(b'isolated fixture')
    settings = template / 'step.def'
    original = 'BASE_PART_MM = null_mm.prt\nLOG_FILE = stock.log\nCONVERT_STEP_FILES_TO = millimeters\nSMOOTH_BSURFS = On\n'
    settings.write_text(original, encoding='utf-8')
    output = tmp_path / 'output'
    output.mkdir()
    result = nxworker.local_settings(settings, output).read_text(encoding='utf-8')
    assert str(seed) in result and 'LOG_FILE = translator.log' in result
    assert 'SMOOTH_BSURFS = Off' in result and 'CONVERT_STEP_FILES_TO = \n' in result
    assert settings.read_text(encoding='utf-8') == original
    settings.write_text('BASE_PART_MM = W:\\prod\\tool.prt\n', encoding='utf-8')
    with pytest.raises(DomainError, match='no external seed'):
        nxworker.local_settings(settings, output)

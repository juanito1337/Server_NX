import base64
import binascii
import secrets
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .dat import read_database
from .geometry import GeometryJobs
from .stepfiles import TOOL_TYPES
from .libraries import KINDS, SPECS, NativeTable, cam_table, native_cam, spec_for
from .safety import DomainError
from .store import TOOL_DATABASE, Store

Kind = Literal['tool', 'holder', 'shank', 'tool_assembly', 'segmented_tool', 'trackpoint',
               'multitool', 'fixture', 'machine', 'device', 'part_material', 'tool_material',
               'feeds_speeds', 'machining_data', 'tool_machining_data', 'cut_method',
               'process_force', 'gauge', 'inspection_tool', 'inspection_machine', 'inspection_device']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ERPLink(StrictModel):
    company_id: str = Field(min_length=1, max_length=100)
    system_id: str = Field(min_length=1, max_length=100)
    number: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=200)
    unit: str = Field(default='', max_length=50)
    source_revision: str = Field(min_length=1, max_length=100)


class GeometryInput(StrictModel):
    mode: Literal['none', 'parametric_nx', 'nx_cam', 'step', 'manufacturer'] = 'none'
    status: Literal['needs_preparation', 'in_preparation', 'validated'] = 'needs_preparation'
    nx_version: str = Field(default='', max_length=4)
    checks: dict[str, bool] = Field(default_factory=dict)
    evidence: str = Field(default='', max_length=4000)
    asset_hashes: dict[str, str] = Field(default_factory=dict)


class ResourceInput(StrictModel):
    kind: Kind
    name: str = Field(min_length=1, max_length=200)
    erp: ERPLink | None = None
    cam: dict = Field(default_factory=dict)
    cam_status: Literal['draft', 'approved', 'retired'] = 'draft'
    asset_ids: list[str] = Field(default_factory=list, max_length=100)
    geometry: GeometryInput = Field(default_factory=GeometryInput)
    origin: dict | None = None


class ResourceUpdate(ResourceInput):
    expected_revision: int = Field(ge=1)


class ERPInput(ERPLink):
    kind: Kind
    expected_revision: int | None = Field(default=None, ge=1)


class ReleaseInput(StrictModel):
    source: str = Field(min_length=1, max_length=100)
    nx_version: str = Field(pattern=r'^\d{4}$')
    name: str = Field(min_length=1, max_length=200)
    normalize_libraries: bool = False
    legacy_version: str = Field(default='2406', pattern=r'^\d{4}$')
    defaults: dict = Field(default_factory=dict)
    resolutions: dict = Field(default_factory=dict)
    preview_hash: str | None = None
    legacy_omissions: list[str] = Field(default_factory=list, max_length=100)


class DeriveInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    defaults: dict = Field(default_factory=dict)


class CatalogInput(StrictModel):
    libraries: list[str] = Field(min_length=1, max_length=100)
    include_standard: bool = False
    preview_hash: str | None = None


class ReviewInput(StrictModel):
    tests: dict[str, bool]
    evidence: str = Field(min_length=20, max_length=4000)
    syslog_source: str = Field(min_length=1, max_length=100)


class ActivateInput(StrictModel):
    release_id: str
    expected_generation: int = Field(ge=0)


class AssetInput(StrictModel):
    filename: str = Field(min_length=1, max_length=120)
    content_base64: str = Field(min_length=1, max_length=28_000_000)


class GeometryJobInput(StrictModel):
    resource_id: str = Field(min_length=1, max_length=36)
    expected_revision: int = Field(ge=1)
    asset_id: str = Field(min_length=1, max_length=36)
    tool_type: str = Field(min_length=1, max_length=50)


class GeometryReportInput(StrictModel):
    report: dict


def create_app(store: Store, admin_token: str, erp_token: str | None = None):
    if len(admin_token) < 32 or (erp_token and len(erp_token) < 32) or admin_token == erp_token:
        raise ValueError('Distinct API tokens with at least 32 characters are required.')
    app = FastAPI(title='NX Folder Management', version='0.1.0', docs_url=None, redoc_url=None, openapi_url=None)
    bearer = HTTPBearer(auto_error=False)
    geometry_jobs = GeometryJobs(store)

    def authenticated(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials and credentials.scheme.lower() == 'bearer':
            token = credentials.credentials
            if secrets.compare_digest(token, admin_token):
                return 'admin'
            if erp_token and secrets.compare_digest(token, erp_token):
                return 'erp'
        raise HTTPException(401, 'Valid Bearer token required.', headers={'WWW-Authenticate': 'Bearer'})

    def admin(actor=Depends(authenticated)):
        if actor != 'admin':
            raise HTTPException(403, 'CAM administrator permission required.')
        return actor

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse(status_code=error.status, content={'detail': str(error)})

    @app.middleware('http')
    async def limits(request: Request, call_next):
        # Restrict the local pilot to its own browser origin (DNS rebinding/CSRF).
        if request.url.hostname not in ('127.0.0.1', 'localhost', 'testserver'):
            return JSONResponse({'detail': 'This pilot only accepts localhost hosts.'}, status_code=403)
        if request.headers.get('origin') and request.headers['origin'] != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': 'Cross-origin requests are not allowed.'}, status_code=403)
        if request.method in ('POST', 'PUT'):
            try:
                length = int(request.headers.get('content-length', '-1'))
            except ValueError:
                length = -1
            if length < 0 or length > 30 * 1024 * 1024 or request.headers.get('transfer-encoding'):
                return JSONResponse({'detail': 'A bounded Content-Length is required.'}, status_code=413)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
        return response

    @app.get('/health')
    def health():
        return {'status': 'ok', 'mode': 'local-pilot', 'production_connected': False}

    @app.get('/', include_in_schema=False)
    def gui():
        return FileResponse(Path(__file__).parent / 'web' / 'index.html')

    @app.get('/app.js', include_in_schema=False)
    def javascript():
        return FileResponse(Path(__file__).parent / 'web' / 'app.js', media_type='text/javascript')

    @app.get('/native.js', include_in_schema=False)
    def native_javascript():
        return FileResponse(Path(__file__).parent / 'web' / 'native.js', media_type='text/javascript')

    @app.get('/geometry.js', include_in_schema=False)
    def geometry_javascript():
        return FileResponse(Path(__file__).parent / 'web' / 'geometry.js', media_type='text/javascript')

    @app.get('/api/v1/openapi.json', dependencies=[Depends(admin)])
    def schema():
        return app.openapi()

    @app.get('/api/v1/capabilities', dependencies=[Depends(authenticated)])
    def capabilities():
        return {'catalog_kinds': list(KINDS), 'kind_labels': KINDS,
                'nx_export_adapters': ['metric_tool_database_new_records', 'native_schema_grouped_libraries'],
                'native_library_count': len(SPECS), 'native_library_specs': list(SPECS.values()),
                'geometry_preparation': {'mode': 'offline_nx_import_package', 'nx_versions': ['2512'],
                                         'tool_types': TOOL_TYPES, 'automatic_cam_approval': False,
                                         'nx_execution_verified': False, 'assistant_batch_api_documented': False},
                'planned_adapters': ['machine_package_creation', 'automatic_cam_geometry_classification',
                                     'business_central', 'nx_cloud_tool_manager', 'sinumerik_integrate'],
                'erp_connected': False, 'production_connected': False}

    @app.get('/api/v1/sources', dependencies=[Depends(admin)])
    def sources():
        return {'folders': {k: str(v) for k, v in store.sources.items()}, 'logs': {k: str(v) for k, v in store.logs.items()},
                'standards': {k: str(v) for k, v in store.standards.items()}}

    @app.get('/api/v1/resources', dependencies=[Depends(authenticated)])
    def resources():
        return store.resources()

    def validate_cam(resource):
        if resource.cam.get('library_id'):
            try:
                cam_table(resource.cam)
            except (KeyError, TypeError, AttributeError, ValueError) as error:
                raise DomainError('Invalid native CAM data structure.') from error
            if spec_for(resource.cam['library_id'])['kind'] != resource.kind:
                raise DomainError('Catalog kind and native NX library differ.')
        elif resource.kind == 'tool' and resource.cam_status == 'approved':
            if set(resource.cam) != {'class_name', 'values'} or not isinstance(resource.cam['class_name'], str) or not isinstance(resource.cam['values'], dict):
                raise DomainError('Approved tools require cam.class_name and cam.values.')
            if not resource.cam['values'].get('LIBRF'):
                raise DomainError('Approved tools require a non-empty LIBRF.')
        elif resource.cam_status == 'approved' and resource.kind != 'gauge':
            raise DomainError('Approved native resources require a library-specific CAM record.')

    @app.post('/api/v1/resources', status_code=201)
    def create_resource(value: ResourceInput, actor=Depends(admin)):
        validate_cam(value)
        return store.save_resource(value.model_dump(), actor)

    @app.put('/api/v1/resources/{resource_id}')
    def update_resource(resource_id: str, value: ResourceUpdate, actor=Depends(admin)):
        validate_cam(value)
        payload = value.model_dump(exclude={'expected_revision'})
        return store.save_resource(payload, actor, resource_id, value.expected_revision)

    @app.get('/api/v1/resources/{resource_id}/history', dependencies=[Depends(admin)])
    def history(resource_id: str):
        return store.history(resource_id)

    @app.post('/api/v1/erp/resources/upsert')
    def erp_upsert(value: ERPInput, actor=Depends(authenticated)):
        return store.erp_upsert(value.model_dump(), actor)

    @app.post('/api/v1/assets', status_code=201)
    def add_asset(value: AssetInput, actor=Depends(admin)):
        try:
            content = base64.b64decode(value.content_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise DomainError('Invalid base64 asset.') from error
        return store.add_asset(value.filename, content, actor)

    @app.get('/api/v1/assets/{asset_id}', dependencies=[Depends(authenticated)])
    def asset(asset_id: str):
        metadata, path = store.asset(asset_id)
        return FileResponse(path, media_type='application/octet-stream', filename=metadata['filename'])

    @app.get('/api/v1/assets/{asset_id}/metadata', dependencies=[Depends(admin)])
    def asset_metadata(asset_id: str):
        return store.asset(asset_id)[0]

    @app.get('/api/v1/assets/{asset_id}/step-preview', dependencies=[Depends(admin)])
    def step_preview(asset_id: str):
        return geometry_jobs.preview(asset_id)

    @app.post('/api/v1/geometry-jobs', status_code=201)
    def create_geometry_job(value: GeometryJobInput, actor=Depends(admin)):
        return geometry_jobs.create(value.resource_id, value.expected_revision, value.asset_id, value.tool_type, actor)

    @app.get('/api/v1/geometry-jobs', dependencies=[Depends(admin)])
    def list_geometry_jobs(resource_id: str | None = None):
        return geometry_jobs.list(resource_id)

    @app.get('/api/v1/geometry-jobs/{job_id}', dependencies=[Depends(admin)])
    def get_geometry_job(job_id: str):
        return geometry_jobs.get(job_id)

    @app.get('/api/v1/geometry-jobs/{job_id}/package', dependencies=[Depends(admin)])
    def geometry_package(job_id: str):
        job = geometry_jobs.get(job_id)
        return Response(content=geometry_jobs.package(job_id), media_type='application/zip',
                        headers={'Content-Disposition': 'attachment; filename="NX_STEP_' + job['id'] + '.zip"'})

    @app.post('/api/v1/geometry-jobs/{job_id}/report')
    def geometry_report(job_id: str, value: GeometryReportInput, actor=Depends(admin)):
        return geometry_jobs.submit_report(job_id, value.report, actor)

    @app.get('/api/v1/releases', dependencies=[Depends(admin)])
    def releases():
        return store.releases()

    @app.post('/api/v1/releases', status_code=201)
    def import_release(value: ReleaseInput, actor=Depends(admin)):
        normalization = {'legacy_version': value.legacy_version, 'defaults': value.defaults,
                         'resolutions': value.resolutions, 'preview_hash': value.preview_hash,
                         'legacy_omissions': value.legacy_omissions} if value.normalize_libraries else None
        return store.import_release(value.source, value.nx_version, value.name, actor, normalization=normalization)

    @app.post('/api/v1/import-preview')
    def preview_import(value: ReleaseInput, actor=Depends(admin)):
        return store.preview_normalization(value.source, value.nx_version, value.legacy_version,
                                           value.defaults, value.resolutions, value.legacy_omissions)

    @app.get('/api/v1/releases/{release_id}', dependencies=[Depends(admin)])
    def get_release(release_id: str):
        return store.get_release(release_id)

    @app.post('/api/v1/releases/{release_id}/derive', status_code=201)
    def derive(release_id: str, value: DeriveInput, actor=Depends(admin)):
        return store.import_release('', '', value.name, actor, parent=release_id, defaults=value.defaults)

    @app.get('/api/v1/releases/{release_id}/libraries', dependencies=[Depends(admin)])
    def libraries(release_id: str):
        return store.library_inventory(release_id)

    @app.get('/api/v1/releases/{release_id}/libraries/{identifier}', dependencies=[Depends(admin)])
    def native_library(release_id: str, identifier: str, query: str = Query('', max_length=200),
                       offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        release = store.get_release(release_id)
        spec = spec_for(identifier)
        path = store.release_folder(release_id) / spec['path']
        if not path.is_file():
            raise DomainError('Native library not present in this release.', 404)
        table = NativeTable.read(path)
        groups = table.groups()
        filtered = [(key, records) for key, records in groups.items() if query.casefold() in str(records).casefold()]
        return {**spec, 'nx_version': release['nx_version'], 'schemas': table.blocks,
                'total': len(groups), 'filtered': len(filtered), 'offset': offset,
                'items': [{'key': key, 'cam': native_cam(identifier, release['nx_version'], table, records)}
                          for key, records in filtered[offset:offset + limit]]}

    @app.post('/api/v1/releases/{release_id}/catalog-preview')
    def preview_catalog(release_id: str, value: CatalogInput, actor=Depends(admin)):
        return store.catalog_plan(release_id, value.libraries, value.include_standard)[1]

    @app.post('/api/v1/releases/{release_id}/catalog-import')
    def import_catalog(release_id: str, value: CatalogInput, actor=Depends(admin)):
        return store.import_catalog(release_id, value.libraries, value.include_standard, value.preview_hash, actor)

    @app.post('/api/v1/releases/{release_id}/validate')
    def validate(release_id: str, actor=Depends(admin)):
        return store.validate_release(release_id, actor)

    @app.get('/api/v1/releases/{release_id}/tool-schemas', dependencies=[Depends(admin)])
    def tool_schemas(release_id: str):
        store.get_release(release_id)
        _, schemas, records, _ = read_database(store.release_folder(release_id) / TOOL_DATABASE)
        return {'schemas': schemas, 'examples': records[:10]}

    @app.get('/api/v1/releases/{release_id}/tools', dependencies=[Depends(admin)])
    def nx_tools(release_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), query: str = Query('', max_length=200)):
        store.get_release(release_id)
        records = read_database(store.release_folder(release_id) / TOOL_DATABASE)[2]
        filtered = [r for r in records if query.casefold() in str(r).casefold()]
        return {'total': len(records), 'filtered': len(filtered), 'offset': offset, 'items': filtered[offset:offset + limit]}

    @app.post('/api/v1/releases/{release_id}/review')
    def review(release_id: str, value: ReviewInput, actor=Depends(admin)):
        return store.review_release(release_id, value.tests, value.evidence, value.syslog_source, actor)

    @app.post('/api/v1/logs/{alias}/scan')
    def scan_log(alias: str, actor=Depends(admin)):
        return store.scan_log(alias)

    @app.get('/api/v1/active', dependencies=[Depends(admin)])
    def active():
        return store.active()

    @app.post('/api/v1/active')
    def activate(value: ActivateInput, actor=Depends(admin)):
        return store.activate(value.release_id, value.expected_generation, actor)

    @app.get('/api/v1/audit', dependencies=[Depends(admin)])
    def audit():
        return store.audit_entries()

    return app

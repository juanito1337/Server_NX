import base64
import hashlib
import binascii
import secrets
import re
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request,Header
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .dat import read_database
from .geometry import GeometryJobs
from .stepfiles import TOOL_TYPES
from .libraries import KINDS, SPECS, NativeTable, cam_table, legacy_cam_table, native_cam, spec_for,validate_catalog_cam
from .safety import DomainError,offline_path
from .store import TOOL_DATABASE, Store, digest
from .upgrades import Upgrades
from .filepackages import MAX_PATH_CHARS

Kind = Literal['tool', 'holder', 'shank', 'tool_assembly', 'segmented_tool', 'trackpoint',
               'multitool', 'fixture', 'machine', 'device', 'part_material', 'tool_material',
               'feeds_speeds', 'machining_data', 'tool_machining_data', 'cut_method',
               'process_force', 'gauge', 'inspection_tool', 'inspection_machine', 'inspection_device','template','ui_standard']


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


class CollectorInput(StrictModel):
    root_library_id:str=Field(min_length=1,max_length=100)
    root_key:str=Field(min_length=1,max_length=116)


class ResultPreviewInput(StrictModel):
    target_release_id:str=Field(min_length=36,max_length=36)
    column_values:dict[str,dict[str,dict[str,dict[str,str]]]]=Field(default_factory=dict)


class ResultApplyInput(ResultPreviewInput):
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')


class ResultAdoptionJobInput(ResultApplyInput):
    result_id:str=Field(min_length=36,max_length=36)


class ResourceInput(StrictModel):
    kind: Kind
    name: str = Field(min_length=1, max_length=200)
    erp: ERPLink | None = None
    cam: dict = Field(default_factory=dict)
    cam_status: Literal['draft', 'approved', 'retired'] = 'draft'
    asset_ids: list[str] = Field(default_factory=list, max_length=100)
    geometry: GeometryInput = Field(default_factory=GeometryInput)
    origin: dict | None = None
    file_bindings:list[dict[str,str]]=Field(default_factory=list,max_length=100)
    model_package:dict|None=None
    file_package:dict|None=None


class ResourceUpdate(ResourceInput):
    expected_revision: int = Field(ge=1)


class ERPInput(ERPLink):
    kind: Kind
    expected_revision: int | None = Field(default=None, ge=1)


class ERPArticleInput(ERPLink):
    expected_revision:int=Field(ge=0,le=2**53-1)


class ERPModelMigrationInput(StrictModel):
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')


class ERPVariantInput(StrictModel):
    article_id:str|None=Field(default=None,min_length=36,max_length=36)
    expected_revision:int=Field(ge=0,le=2**53-1)
    expected_resource_revision:int=Field(ge=1,le=2**53-1)
    expected_article_revision:int|None=Field(default=None,ge=1,le=2**53-1)


class FileAdoptionInput(StrictModel):
    kind:Literal['template','ui_standard','machine','fixture']
    paths:list[str]=Field(min_length=1,max_length=100)
    resource_id:str|None=None


class FileAdoptionApply(FileAdoptionInput):
    name:str=Field(min_length=1,max_length=200)
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')


class FileAdoptionJobInput(FileAdoptionApply):
    release_id:str=Field(min_length=36,max_length=36)


class LaunchProfileInput(StrictModel):
    resource_id:str|None=Field(default=None,min_length=36,max_length=36)
    path:str|None=Field(default=None,min_length=1,max_length=1024)


class LaunchProfileApply(LaunchProfileInput):
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')


class ReleaseInput(StrictModel):
    source: str = Field(min_length=1, max_length=100)
    source_path: str | None = Field(default=None, min_length=1, max_length=1024)
    target: str | None = Field(default=None, min_length=1, max_length=100)
    target_path: str | None = Field(default=None, min_length=1, max_length=1024)
    nx_version: str = Field(pattern=r'^\d{4}$')
    name: str = Field(min_length=1, max_length=200)
    normalize_libraries: bool = False
    legacy_version: str = Field(default='2406', pattern=r'^\d{4}$')
    defaults: dict = Field(default_factory=dict)
    resolutions: dict = Field(default_factory=dict)
    reference_resolutions: dict[str, dict[str, dict[str, dict[str, str]]]] = Field(default_factory=dict)
    preview_hash: str | None = None
    legacy_omissions: list[str] = Field(default_factory=list, max_length=100)


class DeriveInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    defaults: dict = Field(default_factory=dict)
    preview_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class ExportPreviewInput(StrictModel):
    defaults: dict = Field(default_factory=dict)


class ExportObjectsInput(ExportPreviewInput):
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    library_id:str=Field(default='',max_length=100)
    change:Literal['','created','updated','removed']=''
    query:str=Field(default='',max_length=200)
    offset:int=Field(default=0,ge=0,le=1000000)
    limit:int=Field(default=50,ge=1,le=100)


class ExportFilesInput(ExportPreviewInput):
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    view:Literal['files','private']='files'
    change:Literal['','changed','created','updated','removed','unchanged']=''
    query:str=Field(default='',max_length=200)
    offset:int=Field(default=0,ge=0,le=1000000)
    limit:int=Field(default=50,ge=1,le=100)


class ExportRowsInput(ExportPreviewInput):
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    library_id:str=Field(min_length=1,max_length=100)
    key:str=Field(min_length=1,max_length=1024)
    side:Literal['before','after']
    group_sha256:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    offset:int=Field(default=0,ge=0,le=1000000)
    limit:int=Field(default=20,ge=1,le=50)


class DeriveJobInput(DeriveInput):
    parent_release_id:str=Field(min_length=36,max_length=36)


class MigrationInput(StrictModel):
    source: str | None = Field(default=None,min_length=1,max_length=100)
    source_path: str | None = Field(default=None,min_length=1,max_length=1024)
    source_release_id: str | None = Field(default=None,min_length=36,max_length=36)
    target: str = Field(min_length=1,max_length=100)
    target_path: str = Field(min_length=1,max_length=1024)
    source_version: str = Field(pattern=r'^\d{4}$')
    target_version: str = Field(pattern=r'^\d{4}$')
    name: str = Field(min_length=1,max_length=200)
    defaults: dict = Field(default_factory=dict)
    resolutions: dict = Field(default_factory=dict)
    file_resolutions: dict[str,str] = Field(default_factory=dict)
    reference_resolutions: dict[str, dict[str, dict[str, dict[str, str]]]] = Field(default_factory=dict)
    legacy_omissions: list[str] = Field(default_factory=list,max_length=100)


class MigrationBuildInput(MigrationInput):
    preview_id: str = Field(min_length=36,max_length=36)
    preview_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class ExtractionInput(StrictModel):
    version:str=Field(pattern=r'^\d{4}$')


class ExtractionSelection(StrictModel):
    expected_generation:int=Field(ge=0)


class SchemaRowDecision(StrictModel):
    target_block_id:str|None=Field(default=None,max_length=200)
    field_map:dict[str,str]=Field(default_factory=dict)
    discard_fields:list[str]=Field(default_factory=list,max_length=1000)
    values:dict[str,str]=Field(default_factory=dict)
    accept_class_change:bool=False


class SchemaAdaptationInput(StrictModel):
    target_release_id:str=Field(min_length=36,max_length=36)
    kind:Kind
    cam:dict
    resource_id:str|None=Field(default=None,min_length=36,max_length=36)
    expected_revision:int|None=Field(default=None,ge=1)
    decisions:dict[str,SchemaRowDecision]=Field(default_factory=dict)


class SchemaAdaptationApply(SchemaAdaptationInput):
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')


class SchemaImpactInput(StrictModel):
    library_id:str=Field(default='',max_length=100)
    kind:str=Field(default='',max_length=100)
    cam_status:str=Field(default='',max_length=30)
    state:str=Field(default='',max_length=40)
    query:str=Field(default='',max_length=200)
    offset:int=Field(default=0,ge=0)
    limit:int=Field(default=50,ge=1,le=100)
    context_sha256:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')


class PackagePreflightEntry(StrictModel):
    asset_id:str=Field(pattern=r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')
    path:str=Field(min_length=1,max_length=MAX_PATH_CHARS)


class PackagePreflightInput(StrictModel):
    kind:Literal['template','ui_standard','machine','fixture']
    nx_version:str=Field(pattern=r'^[0-9]{4}$')
    asset_ids:list[str]=Field(max_length=100)
    entries:list[PackagePreflightEntry]=Field(max_length=100)


class GraphicsBinding(StrictModel):
    asset_id:str=Field(min_length=36,max_length=36)
    path:str=Field(min_length=1,max_length=4096)


class GraphicsPreviewInput(StrictModel):
    kind:Kind
    cam:dict
    asset_ids:list[str]=Field(max_length=100)
    file_bindings:list[GraphicsBinding]=Field(max_length=100)
    resource_id:str|None=Field(default=None,min_length=36,max_length=36)
    expected_revision:int|None=Field(default=None,ge=1)


class EditorReferenceInput(StrictModel):
    target_release_id:str=Field(min_length=36,max_length=36)
    kind:Kind
    cam:dict
    resource_id:str|None=Field(default=None,min_length=36,max_length=36)
    expected_revision:int|None=Field(default=None,ge=1)


class EditorReferenceApply(EditorReferenceInput):
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    row_index:int=Field(ge=0,lt=1000)
    field:str=Field(min_length=1,max_length=100)
    value:str=Field(min_length=1,max_length=1024)


class CloneInput(StrictModel):
    library_id:str=Field(min_length=1,max_length=100)
    source_key:str=Field(min_length=1,max_length=1024)
    source_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    new_key:str=Field(min_length=1,max_length=116)
    name:str=Field(min_length=1,max_length=200)


class CloneApply(CloneInput):
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')


class AuditPosition(StrictModel):
    sequence:int=Field(ge=0)
    hash:str=Field(pattern=r'^[a-f0-9]{64}$')


class CheckpointRow(StrictModel):
    key:list[str|int]=Field(min_length=1,max_length=2)
    sha256:str=Field(pattern=r'^[a-f0-9]{64}$')


class CheckpointTable(StrictModel):
    columns:list[str]=Field(min_length=1,max_length=100)
    rows:list[CheckpointRow]


class CheckpointInput(StrictModel):
    schema_version:int=Field(ge=1,le=1)
    audit:AuditPosition
    tables:dict[str,CheckpointTable]
    files_checked:bool=False


class IntegrityInput(StrictModel):
    include_files:bool=True
    anchor:CheckpointInput|None=None


class BackupInput(StrictModel):
    accept_legacy_unbound:bool=False


class RecoveryPreviewInput(StrictModel):
    target:str=Field(default='local',min_length=1,max_length=100)
    name:str=Field(min_length=1,max_length=60)


class RecoveryInput(RecoveryPreviewInput):
    preview_id:str=Field(min_length=36,max_length=36)
    preview_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    accept_unsigned:bool=False


class CatalogInput(StrictModel):
    libraries: list[str] = Field(min_length=1, max_length=100)
    include_standard: bool = False
    preview_hash: str | None = None
    selection: dict[str,list[str]] | None = Field(default=None,max_length=100)


class CatalogJobInput(CatalogInput):
    release_id:str=Field(min_length=36,max_length=36)


class ReviewInput(StrictModel):
    tests: dict[str, bool]
    evidence: str = Field(min_length=20, max_length=4000)
    syslog_source: str = Field(min_length=1, max_length=100)
    expected_log_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    expected_manifest_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


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


class LogInput(StrictModel):
    filename: str = Field(min_length=1, max_length=120)
    content_base64: str = Field(min_length=1, max_length=28_000_000)


class LogScanInput(StrictModel):
    release_id: str | None = Field(default=None, max_length=36)


def create_app(store: Store, admin_token: str, erp_token: str | None = None):
    if len(admin_token) < 32 or (erp_token and len(erp_token) < 32) or admin_token == erp_token:
        raise ValueError('Distinct API tokens with at least 32 characters are required.')
    app = FastAPI(title='NX Folder Management', version='0.1.0', docs_url=None, redoc_url=None, openapi_url=None)
    bearer = HTTPBearer(auto_error=False)
    geometry_jobs = GeometryJobs(store)
    from .results import Results,MAX_ARCHIVE
    results=Results(store)
    upgrades = Upgrades(store)
    from .integrity import IntegrityJobs
    integrity = IntegrityJobs(store)
    from .backups import Backups
    backups=Backups(store)
    app.state.upgrades=upgrades
    app.state.backups=backups

    def authenticated(request:Request,credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials and credentials.scheme.lower() == 'bearer':
            token = credentials.credentials
            if secrets.compare_digest(token, admin_token):
                path=request.url.path
                backup_resolution=(request.method=='POST' and (
                    (path.startswith('/api/v1/backup-requests/') and path.endswith('/cancel')) or
                    (request.headers.get('Idempotency-Key') and (path=='/api/v1/backups' or
                     (path.startswith('/api/v1/backups/') and path.rsplit('/',1)[-1] in ('verify','restore-preview','restore'))))))
                if request.method not in ('GET','HEAD') and backups.busy and not backup_resolution:
                    raise DomainError('Backup/recovery is running. Writes are paused; observe the existing job.',409)
                return 'admin'
            if erp_token and secrets.compare_digest(token, erp_token):
                if request.method not in ('GET','HEAD') and backups.busy:
                    raise DomainError('Backup/recovery is running. ERP writes are paused.',409)
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
            prepared=request.url.path=='/api/v1/release-jobs/result_upload' or bool(re.fullmatch(r'/api/v1/geometry-jobs/[a-f0-9-]{36}/prepared-result',request.url.path))
            maximum=MAX_ARCHIVE if prepared else 30*1024*1024
            if length < 0 or length > maximum or request.headers.get('transfer-encoding'):
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

    @app.get('/bootstrap.js',include_in_schema=False)
    def bootstrap_javascript():return FileResponse(Path(__file__).parent/'web/bootstrap.js',media_type='text/javascript')

    @app.get('/saves.js',include_in_schema=False)
    def saves_javascript():return FileResponse(Path(__file__).parent/'web'/'saves.js',media_type='text/javascript')

    @app.get('/downloads.js',include_in_schema=False)
    def downloads_javascript():return FileResponse(Path(__file__).parent/'web/downloads.js',media_type='text/javascript')

    @app.get('/vendor/jssha-3.3.2-sha256.js',include_in_schema=False)
    def download_hash_javascript():return FileResponse(Path(__file__).parent/'web/vendor/jssha-3.3.2-sha256.js',media_type='text/javascript')

    @app.get('/native.js', include_in_schema=False)
    def native_javascript():
        return FileResponse(Path(__file__).parent / 'web' / 'native.js', media_type='text/javascript')

    @app.get('/folders.js', include_in_schema=False)
    def folders_javascript():
        return FileResponse(Path(__file__).parent / 'web' / 'folders.js', media_type='text/javascript')

    @app.get('/geometry.js', include_in_schema=False)
    def geometry_javascript():
        return FileResponse(Path(__file__).parent / 'web' / 'geometry.js', media_type='text/javascript')

    @app.get('/exports.js', include_in_schema=False)
    def exports_javascript():
        return FileResponse(Path(__file__).parent / 'web' / 'exports.js', media_type='text/javascript')

    @app.get('/assets.js', include_in_schema=False)
    def assets_javascript():
        return FileResponse(Path(__file__).parent / 'web' / 'assets.js', media_type='text/javascript')

    @app.get('/upgrades.js',include_in_schema=False)
    def upgrades_javascript():
        return FileResponse(Path(__file__).parent/'web'/'upgrades.js',media_type='text/javascript')

    @app.get('/schemas.js',include_in_schema=False)
    def schemas_javascript():
        return FileResponse(Path(__file__).parent/'web'/'schemas.js',media_type='text/javascript')

    @app.get('/integrity.js',include_in_schema=False)
    def integrity_javascript():
        return FileResponse(Path(__file__).parent/'web'/'integrity.js',media_type='text/javascript')

    @app.get('/references.js',include_in_schema=False)
    def references_javascript():
        return FileResponse(Path(__file__).parent/'web'/'references.js',media_type='text/javascript')

    @app.get('/backups.js',include_in_schema=False)
    def backups_javascript():
        return FileResponse(Path(__file__).parent/'web'/'backups.js',media_type='text/javascript')

    @app.get('/results.js',include_in_schema=False)
    def results_javascript():return FileResponse(Path(__file__).parent/'web'/'results.js',media_type='text/javascript')

    @app.get('/reviews.js',include_in_schema=False)
    def reviews_javascript():return FileResponse(Path(__file__).parent/'web'/'reviews.js',media_type='text/javascript')

    @app.get('/history.js',include_in_schema=False)
    def history_javascript():return FileResponse(Path(__file__).parent/'web'/'history.js',media_type='text/javascript')

    @app.get('/auditview.js',include_in_schema=False)
    def auditview_javascript():return FileResponse(Path(__file__).parent/'web'/'auditview.js',media_type='text/javascript')

    @app.get('/erp.js',include_in_schema=False)
    def erp_javascript():return FileResponse(Path(__file__).parent/'web'/'erp.js',media_type='text/javascript')

    @app.get('/api/v1/openapi.json', dependencies=[Depends(admin)])
    def schema():
        return app.openapi()

    @app.get('/api/v1/capabilities', dependencies=[Depends(authenticated)])
    def capabilities():
        return {'workspace_snapshot':{'schema_version':1,'sqlite_read_transaction':True,'read_only':True,
                                      'files_verified':False,'audit_verified':False},
                'catalog_kinds': list(KINDS), 'kind_labels': KINDS,
                'native_schema_read':{'contract_version':1,'inventory_metadata_only':True,'schema_only':True,
                    'manifest_bound':True,'read_only':True,'object_values_included':False,
                    'max_library_bytes':32*1024*1024,'max_report_bytes':4*1024*1024},
                'erp_variants':{'contract_version':1,'model_version':2,'cardinality_policy':'one_article_many_variants',
                    'explicit_migration':True,'automatic_migration':False,'shared_commercial_revisions':True,
                    'separate_variant_revisions':True,'technical_resources_written':False,'erp_connected':False},
                'erp_catalog':{'contract_version':1,'read_only':True,'overview_role':'admin','exact_lookup_roles':['admin','erp'],
                    'identity_fields':['company_id','system_id'],'number_is_identity':False,'snapshot_bound_pages':True,
                    'cardinality_policy':'not_configured','automatic_assignment':False,'erp_connected':False,
                    'max_resources':50000,'max_payload_bytes':32*1024*1024,'max_read_bytes':512*1024*1024},
                'write_requests':{'contract_version':1,'idempotency_header':'Idempotency-Key',
                    'atomic_resource_save':True,'atomic_erp_upsert':True,'durable_receipts':True,'cancel_fence':True,
                    'role_scoped':True,'automatic_retry':False,'automatic_approval':False},
                'asset_uploads':{'contract_version':1,'durable_allocation':True,'atomic_asset_receipt':True,
                    'commit_fence':True,'automatic_retry':False,'automatic_assignment':False,'automatic_approval':False},
                'graphics_bindings':{'contract_version':1,'read_only_preview':True,'standard_graphics_rules':True,
                    'original_basenames':True,'explicit_prt_selection':True,'max_assets':100,
                    'automatic_reference_rewrite':False,'automatic_approval':False},
                'log_uploads':{'contract_version':1,'durable_allocation':True,'atomic_log_receipt':True,'commit_fence':True,
                    'automatic_retry':False,'automatic_assignment':False,'automatic_approval':False},
                'release_transitions':{'contract_version':1,'actions':['review','activate'],'durable_intent':True,'atomic_final_commit':True,
                    'historical_receipt':True,'final_commit_fence':True,'final_evidence_byte_gate':True,'filesystem_atomic_snapshot':False,
                    'automatic_retry':False,'automatic_activation':False},
                'migration_requests':{'contract_version':1,'idempotency_header':'Idempotency-Key',
                    'modes':['preview','build','extract','import','derive','catalog','file_adopt','result_upload','result_adopt'],'atomic_job_acceptance':True,'durable_receipts':True,
                    'pre_acceptance_fence':True,'worker_cancellation':False,'automatic_restart':False,'automatic_activation':False},
                'migration_execution':{'contract_version':1,'ongoing_evidence':True,'response_only_live_projection':True,
                    'future_owner_child_checked':True,'automatic_restart':False,'timeout_proves_exit':False},
                'catalog_selection':{'contract_version':1,'whole_objects':True,'max_objects':500,
                    'preview_bound':True,'draft_only':True,'automatic_dependencies':False},
                'catalog_adoption':{'contract_version':1,'scopes':['whole','selected'],
                    'final_source_and_baseline_gate':True,'current_catalog_contents_bound':True,
                    'whole_group_schema_preview':True,'common_cam_contract':True,
                    'draft_only':True,'nx_files_changed':False,'automatic_approval':False},
                'editor_references':{'contract_version':1,'manifest_bound':True,'editor_only':True,
                    'preview_bound':True,'automatic_assignment':False,'automatic_save':False},
                'native_cloning':{'contract_version':1,'whole_group':True,'editor_only':True,
                    'preview_bound':True,'automatic_save':False,'copy_approval':False,'copy_assets':False},
                'release_jobs':{'contract_version':1,'modes':['import','derive','catalog','file_adopt','result_upload','result_adopt'],'background_writer':True,
                    'status_endpoint':'migrations','receipt_endpoint':'migration-requests',
                    'atomic_adoption_result':True,'durable_attachment_intents':True,
                    'automatic_restart':False,'automatic_approval':False,'automatic_activation':False},
                'nx_file_packages':{'kinds':['template','ui_standard','machine','fixture'],'versioned':True,
                    'preflight_contract':1,'preflight_read_only':True,'preflight_total_bytes':128*1024*1024,
                    'pax_reference_contract':1,'pax_commit_byte_gate':True,'pax_path_rewrites':False,
                    'final_adoption_commit_gate':True,'source_and_baseline_bound':True,'current_ownership_bound':True,
                    'standard_path_only':True,'existing_file_adoption':True,'four_manual_checks':True,
                    'automatic_approval':False,'script_execution':False,'overwrite_unowned':False},
                'nx_export_adapters': ['metric_tool_database_new_records', 'native_schema_grouped_libraries'],
                'native_library_count': len(SPECS), 'native_library_specs': list(SPECS.values()),
                'catalog_export': {'read_only_preview': True, 'preview_binding': True,
                                   'explicit_column_defaults': True, 'single_identifier_owner': True,
                                   'native_values_preview':1,'native_rows_paged':True,'automatic_row_alignment':False,
                                   'file_plan_preview':1,'private_attachments_metadata_only':True},
                'catalog_schema_adaptation':{'read_only_preview':True,'explicit_mapping_discard':True,
                                             'editor_apply_only':True,'automatic_approval':False},
                'catalog_schema_impact':{'contract_version':1,'read_only':True,'editor_entry_only':True,
                    'automatic_save':False,'automatic_approval':False,'max_resources':10000,'max_source_cells':1000000,
                    'max_target_bytes':128*1024*1024,'max_metadata_bytes':32*1024*1024,'target_library_bounded_reader':True},
                'management_integrity':{'read_only':True,'audit_chain':True,'metadata_bindings':True,
                                        'artifact_scan':True,'external_checkpoint':True,'signed':False},
                'nx_review':{'contract_version':2,'nx_version_header':'NX_VERSION','pinned_log_manifest':True,
                             'six_manual_checks':True,'automatic_approval':False},
                'managed_backup':{'consistent_sqlite_snapshot':True,'managed_files':True,'partial_outputs':True,
                                  'zip64':True,'deduplicated':True,'signed':False,'automatic_activation':False,
                                  'isolated_restore':True,'overwrite_existing':False,'writes_paused':True},
                'backup_requests':{'contract_version':1,'durable_acceptance':True,'pre_acceptance_fence':True,
                    'modes':['backup','verify','preview','restore'],'automatic_restart':False,'automatic_activation':False},
                'attachments': {'inventory': True, 'reuse': True, 'revisioned_assignment': True,
                                'immutable_bytes': True, 'delete_files': False, 'max_bytes': 20 * 1024 * 1024},
                'whole_share_migration': {'version_pairs':[['2406','2512']],'asynchronous_jobs':True,
                                         'plan_contract_version':1,'self_hashing_plan':True,'max_report_bytes':64*1024*1024,
                                         'installation_version_contract_version':1,'static_pe_version_metadata':True,
                                         'sidt_version_contract_version':1,
                                         'static_extraction':True,'extraction_worker':'windows_pinned_innounp',
                                         'installer_execution':False,'automatic_activation':False},
                'geometry_preparation': {'mode': 'offline_nx_import_package', 'nx_versions': ['2512'],
                                         'tool_types': TOOL_TYPES, 'automatic_cam_approval': False,
                                         'nx_execution_verified': False, 'assistant_batch_api_documented': False},
                'geometry_requests':{'contract_version':1,'durable_package_intents':True,'atomic_job_receipt':True,
                    'commit_fence':True,'automatic_restart':False,'automatic_approval':False},
                'prepared_result_packages':{'collector':True,'native_related_drafts':True,'relative_components':True,
                    'column_decisions_contract':1,'explicit_new_column_values':True,
                    'max_archive_bytes':MAX_ARCHIVE,'background_upload':True,'background_adoption':True,
                    'atomic_operation_result':True,'automatic_cam_approval':False,'nx_execution_verified':False},
                'release_test_launcher': {'automatic': True, 'nx_versions': ['2512'],
                                          'mode': 'self_contained_windows_package', 'automatic_nx_start': False,
                                          'ui_profile_contract':1,'explicit_ui_role':True,'user_profiles_changed':False,
                                          'manual_template_refresh':True,'bound_cam_template_directories':True},
                'nx_test_evidence': {'system_log_upload': True, 'max_bytes': 20 * 1024 * 1024,
                                     'immutable_uploads': True, 'automatic_approval': False},
                'planned_adapters': ['machine_package_creation', 'automatic_cam_geometry_classification',
                                     'business_central', 'nx_cloud_tool_manager', 'sinumerik_integrate'],
                'erp_connected': False, 'production_connected': False}

    @app.get('/api/v1/sources', dependencies=[Depends(admin)])
    def sources():
        return {'folders': {k: str(v) for k, v in store.sources.items()}, 'logs': {k: str(v) for k, v in store.logs.items()},
                'standards': {k: str(v) for k, v in store.standards.items()},
                'source_roots': {k: str(v) for k, v in store.folders.sources.items()},
                'target_roots': {k: str(v) for k, v in store.folders.targets.items()}}

    @app.get('/api/v1/folders', dependencies=[Depends(admin)])
    def folders(purpose: Literal['source', 'target'], root: str = Query(min_length=1, max_length=100),
                path: str | None = Query(None, min_length=1, max_length=1024)):
        return store.folders.browse(purpose, root, path)

    @app.get('/api/v1/resources', dependencies=[Depends(authenticated)])
    def resources():
        return store.resources()

    @app.get('/api/v1/workspace', dependencies=[Depends(admin)])
    def workspace():
        # Seal the actual serialized bytes, not JavaScript-reencoded objects:
        # legitimate free CAM JSON can contain 1.0, decimals and large integers.
        response=JSONResponse(store.workspace())
        response.headers['X-NX-Workspace-Contract']='1'
        response.headers['X-NX-Workspace-Bytes']=str(len(response.body))
        response.headers['X-NX-Workspace-SHA256']=hashlib.sha256(response.body).hexdigest()
        return response

    @app.get('/api/v1/releases/{release_id}/file-packages',dependencies=[Depends(admin)])
    def package_inventory(release_id:str,kind:Literal['template','ui_standard','machine','fixture'],
                          query:str=Query('',max_length=200),offset:int=Query(0,ge=0,le=1000000),limit:int=Query(100,ge=1,le=500)):
        from .filepackages import FilePackages
        return FilePackages(store).inventory(release_id,kind,query,offset,limit)

    @app.post('/api/v1/releases/{release_id}/file-packages/preview')
    def package_adoption_preview(release_id:str,value:FileAdoptionInput,actor=Depends(admin)):
        from .filepackages import FilePackages
        with store.lock:return FilePackages(store).adoption_preview(release_id,value.kind,value.paths,value.resource_id)[0]

    @app.post('/api/v1/releases/{release_id}/file-packages/adopt')
    def package_adoption_apply(release_id:str,value:FileAdoptionApply,actor=Depends(admin)):
        from .filepackages import FilePackages
        return FilePackages(store).adopt(release_id,value.kind,value.paths,value.name,value.resource_id,value.preview_hash,actor)

    @app.get('/filepackages.js',include_in_schema=False)
    def file_packages_javascript():return FileResponse(Path(__file__).parent/'web/filepackages.js',media_type='text/javascript')

    @app.get('/api/v1/migrations/options',dependencies=[Depends(admin)])
    def migration_options():return upgrades.options()

    @app.get('/api/v1/migrations',dependencies=[Depends(admin)])
    def migration_jobs():return upgrades.list()

    @app.post('/api/v1/migrations/preview',status_code=202)
    def migration_preview(value:MigrationInput,actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        return upgrades.create(value.model_dump(),'preview',actor,request_key=idempotency_key)

    @app.post('/api/v1/migrations/extract',status_code=202)
    def extraction_create(value:ExtractionInput,actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        return upgrades.create_extraction(value.version,actor,request_key=idempotency_key)

    @app.post('/api/v1/migrations/{job_id}/select-input')
    def extraction_select(job_id:str,value:ExtractionSelection,actor=Depends(admin)):
        return upgrades.select_extraction(job_id,value.expected_generation,actor)

    @app.post('/api/v1/migrations/build',status_code=202)
    def migration_build(value:MigrationBuildInput,actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        return upgrades.create(value.model_dump(exclude={'preview_id','preview_hash'}),'build',actor,value.preview_id,value.preview_hash,request_key=idempotency_key)

    @app.get('/api/v1/migration-requests/{request_key}')
    def migration_request_receipt(request_key:str,actor=Depends(admin)):
        from .migration_requests import get
        return get(store,actor,request_key)

    @app.post('/api/v1/migration-requests/{request_key}/cancel')
    def migration_request_cancel(request_key:str,actor=Depends(admin)):
        from .migration_requests import cancel
        return cancel(store,actor,request_key)

    @app.post('/api/v1/release-jobs/import',status_code=202)
    def release_import_job(value:ReleaseInput,actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        return upgrades.create_import(value.model_dump(),actor,request_key=idempotency_key)

    @app.post('/api/v1/release-jobs/derive',status_code=202)
    def release_derive_job(value:DeriveJobInput,actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        return upgrades.create_derive(value.model_dump(),actor,request_key=idempotency_key)

    @app.post('/api/v1/release-jobs/catalog',status_code=202)
    def release_catalog_job(value:CatalogJobInput,actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        return upgrades.create_adoption(value.model_dump(),'catalog',actor,request_key=idempotency_key)

    @app.post('/api/v1/release-jobs/file_adopt',status_code=202)
    def release_file_adoption_job(value:FileAdoptionJobInput,actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        return upgrades.create_adoption(value.model_dump(),'file_adopt',actor,request_key=idempotency_key)

    @app.post('/api/v1/release-jobs/result_adopt',status_code=202)
    def release_result_adoption_job(value:ResultAdoptionJobInput,actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        return upgrades.create_result_adoption(value.model_dump(),actor,request_key=idempotency_key)

    @app.post('/api/v1/release-jobs/result_upload',status_code=202)
    async def release_result_upload_job(request:Request,geometry_job_id:str=Query(min_length=36,max_length=36),actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        if request.headers.get('content-type','').split(';')[0]!='application/zip':raise DomainError('Upload the prepared result as application/zip.')
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>MAX_ARCHIVE:raise DomainError('Prepared ZIP exceeds 64 MiB.',413)
            chunks.append(chunk)
        if size!=int(request.headers['content-length']):raise DomainError('Prepared upload length differs from Content-Length.')
        return await run_in_threadpool(upgrades.create_result_upload,geometry_job_id,b''.join(chunks),actor,idempotency_key)

    @app.get('/releasejobs.js',include_in_schema=False)
    def release_jobs_javascript():return FileResponse(Path(__file__).parent/'web/releasejobs.js',media_type='text/javascript')

    @app.get('/api/v1/migrations/{job_id}',dependencies=[Depends(admin)])
    def migration_job(job_id:str):return upgrades.get(job_id)

    @app.get('/api/v1/migrations/{job_id}/report',dependencies=[Depends(admin)])
    def migration_report(job_id:str):return upgrades.report(job_id)

    def validate_cam(resource):
        validate_catalog_cam(resource.model_dump())

    @app.post('/api/v1/resources', status_code=201)
    def create_resource(value: ResourceInput, actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        validate_cam(value)
        return store.save_resource(value.model_dump(), actor,request_key=idempotency_key)

    @app.post('/api/v1/releases/{release_id}/library-clone/preview')
    def clone_preview(release_id:str,value:CloneInput,actor=Depends(admin)):
        from .cloning import plan
        return plan(store,release_id,value.model_dump())

    @app.post('/api/v1/releases/{release_id}/library-clone/apply')
    def clone_apply(release_id:str,value:CloneApply,actor=Depends(admin)):
        from .cloning import apply
        return apply(store,release_id,value.model_dump(exclude={'preview_hash'}),value.preview_hash)

    @app.get('/api/v1/releases/{release_id}/reference-options',dependencies=[Depends(admin)])
    def editor_reference_options(release_id:str,library_id:str=Query(min_length=1,max_length=100),
                                 field:str=Query(min_length=1,max_length=100),query:str=Query('',max_length=200),
                                 offset:int=Query(0,ge=0,le=1_000_000),limit:int=Query(100,ge=1,le=100)):
        from .editorrefs import options
        return options(store,release_id,library_id,field,query,offset,limit)

    @app.post('/api/v1/editor-references/preview')
    def editor_reference_preview(value:EditorReferenceInput,actor=Depends(admin)):
        from .editorrefs import plan
        return plan(store,value.model_dump())

    @app.post('/api/v1/editor-references/apply')
    def editor_reference_apply(value:EditorReferenceApply,actor=Depends(admin)):
        from .editorrefs import apply
        return apply(store,value.model_dump(exclude={'preview_hash','row_index','field','value'}),
            value.preview_hash,value.row_index,value.field,value.value)

    @app.post('/api/v1/schema/preview')
    def schema_preview(value:SchemaAdaptationInput,actor=Depends(admin)):
        from .schemas import plan
        return plan(store,value.model_dump())

    @app.post('/api/v1/file-packages/preflight')
    def file_package_preflight(value:PackagePreflightInput,actor=Depends(admin)):
        from .filepackages import preflight
        return preflight(store,value.model_dump())

    @app.post('/api/v1/releases/{release_id}/catalog-schema-impact')
    def catalog_schema_impact(release_id:str,value:SchemaImpactInput,actor=Depends(admin)):
        from .schemaimpact import SchemaImpact
        return SchemaImpact(store).inspect(release_id,**value.model_dump())

    @app.post('/api/v1/graphics-bindings/preview')
    def graphics_binding_preview(value:GraphicsPreviewInput,actor=Depends(admin)):
        from .graphics import preview
        return preview(store,value.model_dump())

    @app.post('/api/v1/schema/apply')
    def schema_apply(value:SchemaAdaptationApply,actor=Depends(admin)):
        from .schemas import apply
        return apply(store,value.model_dump(exclude={'preview_hash'}),value.preview_hash)

    @app.get('/api/v1/resources/{resource_id}',dependencies=[Depends(admin)])
    def current_resource(resource_id:str):
        return store.get_resource(resource_id)

    @app.put('/api/v1/resources/{resource_id}')
    def update_resource(resource_id: str, value: ResourceUpdate, actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        validate_cam(value)
        payload = value.model_dump(exclude={'expected_revision'})
        return store.save_resource(payload, actor, resource_id, value.expected_revision,request_key=idempotency_key)

    @app.get('/api/v1/write-requests/{request_key}')
    def write_request_receipt(request_key:str,actor=Depends(authenticated)):
        from .write_requests import get
        return get(store,actor,request_key)

    @app.post('/api/v1/write-requests/{request_key}/cancel')
    def write_request_cancel(request_key:str,actor=Depends(authenticated)):
        from .write_requests import cancel
        return cancel(store,actor,request_key)

    @app.get('/api/v1/resources/{resource_id}/history', dependencies=[Depends(admin)])
    def history(resource_id: str):
        return store.history(resource_id)

    @app.get('/api/v1/resources/{resource_id}/history-summary',dependencies=[Depends(admin)])
    def history_summary(resource_id:str,offset:int=Query(0,ge=0,le=2**63-1),limit:int=Query(20,ge=1,le=50),expected_revision:int|None=Query(None,ge=1,le=2**63-1)):
        from .history import summary
        return summary(store,resource_id,offset,limit,expected_revision)

    @app.get('/api/v1/resources/{resource_id}/history-diff',dependencies=[Depends(admin)])
    def history_diff(resource_id:str,before:int=Query(ge=1,le=2**63-1),after:int=Query(ge=1,le=2**63-1),before_sha256:str=Query(pattern=r'^[a-f0-9]{64}$'),after_sha256:str=Query(pattern=r'^[a-f0-9]{64}$'),
            expected_revision:int|None=Query(None,ge=1,le=2**63-1),offset:int=Query(0,ge=0,le=2**63-1),limit:int=Query(50,ge=1,le=100),area:str=Query('',max_length=30),query:str=Query('',max_length=200),comparison_sha256:str|None=Query(None,pattern=r'^[a-f0-9]{64}$')):
        from .history import compare
        return compare(store,resource_id,before,after,before_sha256,after_sha256,expected_revision,offset,limit,area,query,comparison_sha256)

    @app.get('/api/v1/erp/model',dependencies=[Depends(authenticated)])
    def erp_model_status():
        from .erp_variants import ERPVariants
        return ERPVariants(store).status()

    @app.get('/api/v1/erp/model/migration-preview',dependencies=[Depends(admin)])
    def erp_model_preview():
        from .erp_variants import ERPVariants
        return ERPVariants(store).migration_preview()

    @app.post('/api/v1/erp/model/migrate')
    def erp_model_migrate(value:ERPModelMigrationInput,actor=Depends(admin),idempotency_key:str=Header(min_length=36,max_length=36)):
        from .erp_variants import ERPVariants
        return ERPVariants(store).migrate(value.preview_hash,actor,idempotency_key)

    @app.post('/api/v1/erp/articles/upsert')
    def erp_article_upsert(value:ERPArticleInput,actor=Depends(authenticated),idempotency_key:str=Header(min_length=36,max_length=36)):
        from .erp_variants import ERPVariants
        data=value.model_dump();expected=data.pop('expected_revision')
        return ERPVariants(store).upsert(data,expected,actor,idempotency_key)

    @app.put('/api/v1/erp/variants/{resource_id}')
    def erp_variant_assign(resource_id:str,value:ERPVariantInput,actor=Depends(admin),idempotency_key:str=Header(min_length=36,max_length=36)):
        from .erp_variants import ERPVariants
        return ERPVariants(store).assign(resource_id,**value.model_dump(),actor=actor,request_key=idempotency_key)

    @app.get('/api/v1/erp/articles',dependencies=[Depends(authenticated)])
    def erp_articles(query:str=Query('',max_length=200),offset:int=Query(0,ge=0,le=1000000),limit:int=Query(50,ge=1,le=100),
                     snapshot_sha256:str|None=Query(None,pattern=r'^[a-f0-9]{64}$'),company_id:str|None=Query(None,min_length=1,max_length=100),system_id:str|None=Query(None,min_length=1,max_length=100)):
        from .erp_variants import ERPVariants
        return ERPVariants(store).read(query,offset,limit,snapshot_sha256,company_id=company_id,system_id=system_id)

    @app.get('/api/v1/erp/articles/{article_id}/variants',dependencies=[Depends(authenticated)])
    def erp_article_variants(article_id:str,query:str=Query('',max_length=200),offset:int=Query(0,ge=0,le=1000000),limit:int=Query(50,ge=1,le=100),snapshot_sha256:str|None=Query(None,pattern=r'^[a-f0-9]{64}$')):
        from .erp_variants import ERPVariants
        return ERPVariants(store).read(query,offset,limit,snapshot_sha256,article_id)

    @app.get('/api/v1/erp/requests/{request_key}')
    def erp_request_receipt(request_key:str,actor=Depends(authenticated)):
        from .erp_variants import ERPVariants
        return ERPVariants(store).receipt(actor,request_key)

    @app.post('/api/v1/erp/requests/{request_key}/cancel')
    def erp_request_cancel(request_key:str,actor=Depends(authenticated)):
        from .erp_variants import ERPVariants
        return ERPVariants(store).cancel(actor,request_key)

    @app.get('/api/v1/erp/variants/{resource_id}',dependencies=[Depends(authenticated)])
    def erp_variant(resource_id:str):
        from .erp_variants import ERPVariants
        return ERPVariants(store).variant(resource_id)

    @app.get('/api/v1/erp/articles/{article_id}/history',dependencies=[Depends(authenticated)])
    def erp_article_history(article_id:str,offset:int=Query(0,ge=0,le=1000000),limit:int=Query(50,ge=1,le=100),snapshot_sha256:str|None=Query(None,pattern=r'^[a-f0-9]{64}$')):
        from .erp_variants import ERPVariants
        return ERPVariants(store).history('article',article_id,offset,limit,snapshot_sha256)

    @app.get('/api/v1/erp/variants/{resource_id}/history',dependencies=[Depends(authenticated)])
    def erp_variant_history(resource_id:str,offset:int=Query(0,ge=0,le=1000000),limit:int=Query(50,ge=1,le=100),snapshot_sha256:str|None=Query(None,pattern=r'^[a-f0-9]{64}$')):
        from .erp_variants import ERPVariants
        return ERPVariants(store).history('variant',resource_id,offset,limit,snapshot_sha256)

    @app.post('/api/v1/erp/resources/upsert')
    def erp_upsert(value: ERPInput, actor=Depends(authenticated),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        return store.erp_upsert(value.model_dump(), actor,request_key=idempotency_key)

    @app.get('/api/v1/erp/catalog-preview',dependencies=[Depends(admin)])
    def erp_catalog_preview(offset:int=Query(0,ge=0,le=2**63-1),limit:int=Query(50,ge=1,le=100),state:str=Query('',max_length=30),kind:str=Query('',max_length=40),
            company_id:str=Query('',max_length=100),query:str=Query('',max_length=200),catalog_sha256:str|None=Query(None,pattern=r'^[a-f0-9]{64}$')):
        from .erp_catalog import preview
        return preview(store,offset,limit,state,kind,company_id,query,catalog_sha256)

    @app.get('/api/v1/erp/resources/lookup',dependencies=[Depends(authenticated)])
    def erp_resource_lookup(company_id:str=Query(min_length=1,max_length=100),system_id:str=Query(min_length=1,max_length=100),catalog_sha256:str|None=Query(None,pattern=r'^[a-f0-9]{64}$')):
        from .erp_catalog import lookup
        return lookup(store,company_id,system_id,catalog_sha256)

    @app.post('/api/v1/assets', status_code=201)
    def add_asset(value: AssetInput, actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        try:
            content = base64.b64decode(value.content_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise DomainError('Invalid base64 asset.') from error
        return store.add_asset(value.filename,content,actor,request_key=idempotency_key) if idempotency_key is not None else store.add_asset(value.filename,content,actor)

    @app.get('/api/v1/asset-requests/{request_key}')
    def asset_upload_receipt(request_key:str,actor=Depends(admin)):
        from .asset_requests import get
        return get(store,actor,request_key)

    @app.post('/api/v1/asset-requests/{request_key}/cancel')
    def asset_upload_fence(request_key:str,actor=Depends(admin)):
        from .asset_requests import cancel
        return cancel(store,actor,request_key)

    @app.get('/api/v1/assets', dependencies=[Depends(admin)])
    def assets(query: str = Query('', max_length=200), offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        return store.asset_inventory(query, offset, limit)

    @app.get('/api/v1/assets/{asset_id}', dependencies=[Depends(authenticated)])
    def asset(asset_id: str):
        metadata, content = store.asset_content(asset_id)
        return Response(content, media_type='application/octet-stream', headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(metadata['filename'], safe=''),
            'X-Asset-SHA256': metadata['sha256']})

    @app.get('/api/v1/assets/{asset_id}/metadata', dependencies=[Depends(admin)])
    def asset_metadata(asset_id: str):
        return store.asset(asset_id)[0]

    @app.get('/api/v1/assets/{asset_id}/step-preview', dependencies=[Depends(admin)])
    def step_preview(asset_id: str):
        return geometry_jobs.preview(asset_id)

    @app.post('/api/v1/geometry-jobs', status_code=201)
    def create_geometry_job(value: GeometryJobInput, actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        args=(value.resource_id,value.expected_revision,value.asset_id,value.tool_type,actor)
        return geometry_jobs.create(*args,request_key=idempotency_key) if idempotency_key is not None else geometry_jobs.create(*args)

    @app.get('/api/v1/geometry-requests/{request_key}')
    def geometry_request_receipt(request_key:str,actor=Depends(admin)):
        from .geometry_requests import get
        return get(store,actor,request_key)

    @app.post('/api/v1/geometry-requests/{request_key}/cancel')
    def geometry_request_fence(request_key:str,actor=Depends(admin)):
        from .geometry_requests import cancel
        return cancel(store,actor,request_key)

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

    @app.get('/api/v1/geometry-jobs/{job_id}/package-metadata', dependencies=[Depends(admin)])
    def geometry_package_metadata(job_id:str):
        package=geometry_jobs.package(job_id)
        return {'job_id':job_id,'sha256':hashlib.sha256(package).hexdigest(),'size':len(package),
                'files_verified':True,'nx_execution_verified':False,'automatic_cam_approval':False}

    @app.post('/api/v1/geometry-jobs/{job_id}/report')
    def geometry_report(job_id: str, value: GeometryReportInput, actor=Depends(admin)):
        return geometry_jobs.submit_report(job_id, value.report, actor)

    @app.get('/api/v1/releases', dependencies=[Depends(admin)])
    def releases():
        return store.releases()

    @app.post('/api/v1/releases', status_code=201)
    def import_release(value: ReleaseInput, actor=Depends(admin)):
        if value.reference_resolutions and not value.normalize_libraries:
            raise DomainError('Reference corrections require a reviewed normalization preview.')
        normalization = {'legacy_version': value.legacy_version, 'defaults': value.defaults,
                         'resolutions': value.resolutions, 'preview_hash': value.preview_hash,
                         'reference_resolutions': value.reference_resolutions,
                         'legacy_omissions': value.legacy_omissions} if value.normalize_libraries else None
        return store.import_release(value.source, value.nx_version, value.name, actor, normalization=normalization,
                                    source_path=value.source_path, target=value.target, target_path=value.target_path)

    @app.post('/api/v1/import-preview')
    def preview_import(value: ReleaseInput, actor=Depends(admin)):
        if bool(value.target) != bool(value.target_path):
            raise DomainError('Target alias and target path must be provided together.')
        return store.preview_normalization(value.source, value.nx_version, value.legacy_version,
                                           value.defaults, value.resolutions, value.legacy_omissions,
                                           value.source_path, value.target, value.target_path, value.reference_resolutions)

    @app.get('/api/v1/releases/{release_id}', dependencies=[Depends(admin)])
    def get_release(release_id: str):
        return store.get_release(release_id)

    @app.post('/api/v1/releases/{release_id}/test-launcher')
    def prepare_test_launcher(release_id: str, actor=Depends(admin)):
        return store.prepare_test_launcher(release_id, actor)

    @app.get('/api/v1/releases/{release_id}/test-launcher/package', dependencies=[Depends(admin)])
    def test_launcher_package(release_id: str):
        return FileResponse(store.test_launcher_package(release_id), media_type='application/zip',
                            filename='NX2512_Test_' + release_id[:8] + '.zip')

    @app.post('/api/v1/releases/{release_id}/test-launcher/ui-profile-preview')
    def ui_profile_preview(release_id:str,value:LaunchProfileInput,actor=Depends(admin)):
        from .launchprofiles import LaunchProfiles
        return LaunchProfiles(store).preview(release_id,value.resource_id,value.path)

    @app.post('/api/v1/releases/{release_id}/test-launcher/ui-profile')
    def ui_profile_prepare(release_id:str,value:LaunchProfileApply,actor=Depends(admin)):
        from .launchprofiles import LaunchProfiles
        return LaunchProfiles(store).prepare(release_id,value.resource_id,value.path,value.preview_hash,actor)

    @app.post('/api/v1/releases/{release_id}/derive', status_code=201)
    def derive(release_id: str, value: DeriveInput, actor=Depends(admin)):
        return store.import_release('', '', value.name, actor, parent=release_id, defaults=value.defaults,
                                    preview_hash=value.preview_hash)

    @app.post('/api/v1/releases/{release_id}/derive-preview')
    def preview_export(release_id: str, value: ExportPreviewInput, actor=Depends(admin)):
        return store.preview_export(release_id, value.defaults)

    @app.post('/api/v1/releases/{release_id}/derive-preview/objects')
    def export_objects(release_id:str,value:ExportObjectsInput,actor=Depends(admin)):
        from .exportview import ExportView
        return ExportView(store).objects(release_id,value.defaults,value.preview_hash,value.library_id,value.change,value.query,value.offset,value.limit)

    @app.post('/api/v1/releases/{release_id}/derive-preview/files')
    def export_files(release_id:str,value:ExportFilesInput,actor=Depends(admin)):
        from .exportfiles import ExportFiles
        return ExportFiles(store).inspect(release_id,value.defaults,value.preview_hash,value.view,value.change,value.query,value.offset,value.limit)

    @app.post('/api/v1/releases/{release_id}/derive-preview/rows')
    def export_rows(release_id:str,value:ExportRowsInput,actor=Depends(admin)):
        from .exportview import ExportView
        return ExportView(store).rows(release_id,value.defaults,value.preview_hash,value.library_id,value.key,value.side,value.group_sha256,value.offset,value.limit)

    @app.get('/api/v1/releases/{release_id}/library-schemas',dependencies=[Depends(admin)])
    def library_schema_inventory(release_id:str):
        from .library_schemas import LibrarySchemas
        return LibrarySchemas(store).inventory(release_id)

    @app.get('/api/v1/releases/{release_id}/library-schemas/{identifier}',dependencies=[Depends(admin)])
    def library_schema(release_id:str,identifier:str,inventory_sha256:str=Query(...,pattern=r'^[a-f0-9]{64}$')):
        from .library_schemas import LibrarySchemas
        return LibrarySchemas(store).schema(release_id,identifier,inventory_sha256)

    @app.get('/api/v1/releases/{release_id}/libraries', dependencies=[Depends(admin)])
    def libraries(release_id: str):
        return store.library_inventory(release_id)

    @app.get('/api/v1/releases/{release_id}/libraries/{identifier}', dependencies=[Depends(admin)])
    def native_library(release_id: str, identifier: str, query: str = Query('', max_length=200),
                       offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        release = store.get_release(release_id)
        spec = spec_for(identifier)
        path = offline_path(Path(release['folder']) / spec['path'])
        if not path.is_file():
            raise DomainError('Native library not present in this release.', 404)
        table = NativeTable.read(path)
        expected=next((item for item in release['manifest'] or [] if item['path'].casefold()==spec['path'].casefold()),None)
        if not expected or table.source_sha256!=expected['sha256']:
            raise DomainError('Native library differs from the managed manifest; import a new managed stand instead of editing this release.',409)
        groups = table.groups()
        filtered = [(key, records) for key, records in groups.items() if query.casefold() in str(records).casefold()]
        return {**spec, 'nx_version': release['nx_version'], 'schemas': table.blocks,
                'total': len(groups), 'filtered': len(filtered), 'offset': offset,'limit':limit,
                'input_sha256':table.source_sha256,'manifest_sha256':digest(release['manifest']),
                'integrity_checked':True,'scope':'selected_native_library_bytes','automatic_approval':False,
                'items': [{'key': key, 'cam': native_cam(identifier, release['nx_version'], table, records)}
                          for key, records in filtered[offset:offset + limit]]}

    @app.post('/api/v1/releases/{release_id}/catalog-preview')
    def preview_catalog(release_id: str, value: CatalogInput, actor=Depends(admin)):
        return store.catalog_plan(release_id, value.libraries, value.include_standard,value.selection)[1]

    @app.post('/api/v1/releases/{release_id}/catalog-import')
    def import_catalog(release_id: str, value: CatalogInput, actor=Depends(admin)):
        return store.import_catalog(release_id, value.libraries, value.include_standard, value.preview_hash, actor,selection=value.selection)

    @app.post('/api/v1/releases/{release_id}/validate')
    def validate(release_id: str, actor=Depends(admin)):
        return store.validate_release(release_id, actor)

    @app.get('/api/v1/releases/{release_id}/tool-schemas', dependencies=[Depends(admin)])
    def tool_schemas(release_id: str):
        release=store.get_release(release_id)
        expected=next((item['sha256'] for item in release['manifest'] or [] if item['path'].casefold()==TOOL_DATABASE.casefold()),None)
        if expected is None:raise DomainError('Tool database is not in the managed manifest.',409)
        _, schemas, records, _ = read_database(Path(release['folder']) / TOOL_DATABASE,expected)
        return {'schemas': schemas, 'examples': records[:10],'input_sha256':expected,'integrity_checked':True,
            'scope':'selected_legacy_tool_bytes','automatic_approval':False}

    @app.get('/api/v1/releases/{release_id}/tools', dependencies=[Depends(admin)])
    def nx_tools(release_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), query: str = Query('', max_length=200)):
        release=store.get_release(release_id)
        expected=next((item['sha256'] for item in release['manifest'] or [] if item['path'].casefold()==TOOL_DATABASE.casefold()),None)
        if expected is None:raise DomainError('Tool database is not in the managed manifest.',409)
        records = read_database(Path(release['folder']) / TOOL_DATABASE,expected)[2]
        filtered = [r for r in records if query.casefold() in str(r).casefold()]
        return {'total': len(records), 'filtered': len(filtered), 'offset': offset,'limit':limit,
            'input_sha256':expected,'integrity_checked':True,'scope':'selected_legacy_tool_bytes','automatic_approval':False,
            'items': filtered[offset:offset + limit]}

    @app.post('/api/v1/releases/{release_id}/review')
    def review(release_id: str, value: ReviewInput, actor=Depends(admin),idempotency_key:str|None=Header(default=None,alias='Idempotency-Key')):
        return store.review_release(release_id, value.tests, value.evidence, value.syslog_source, actor,
                                    value.expected_log_sha256, value.expected_manifest_sha256,request_key=idempotency_key)

    @app.post('/api/v1/logs/{alias}/scan')
    def scan_log(alias: str, value: LogScanInput | None = None, actor=Depends(admin)):
        folder = None; release = None
        if value and value.release_id:
            release = store.get_release(value.release_id)
            candidate = store.release_folder(value.release_id)
            folder = store.host_folder(candidate) or candidate
        report = store.scan_log(alias, folder, release['nx_version'] if release else None)
        if release:report.update(release_id=release['id'],manifest_sha256=digest(release['manifest']))
        return report

    @app.get('/api/v1/logs', dependencies=[Depends(admin)])
    def logs():
        return store.list_logs()

    @app.post('/api/v1/logs', status_code=201)
    def upload_log(value: LogInput, actor=Depends(admin),idempotency_key:str|None=Header(default=None,alias='Idempotency-Key')):
        try:
            content = base64.b64decode(value.content_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise DomainError('Invalid base64 system log.') from error
        return store.add_log(value.filename,content,actor,request_key=idempotency_key)

    @app.get('/api/v1/log-requests/{request_key}')
    def log_upload_receipt(request_key:str,actor=Depends(admin)):
        from .log_requests import get
        return get(store,actor,request_key)

    @app.post('/api/v1/log-requests/{request_key}/cancel')
    def log_upload_fence(request_key:str,actor=Depends(admin)):
        from .log_requests import cancel
        return cancel(store,actor,request_key)

    @app.get('/api/v1/logs/{identifier}/metadata',dependencies=[Depends(admin)])
    def log_upload_metadata(identifier:str):return store.log_metadata(identifier)

    @app.get('/api/v1/active', dependencies=[Depends(admin)])
    def active():
        return store.active()

    @app.post('/api/v1/active')
    def activate(value: ActivateInput, actor=Depends(admin),idempotency_key:str|None=Header(default=None,alias='Idempotency-Key')):
        return store.activate(value.release_id, value.expected_generation, actor,request_key=idempotency_key)

    @app.get('/api/v1/release-requests/{request_key}')
    def release_transition_receipt(request_key:str,actor=Depends(admin)):
        from .release_requests import get
        return get(store,actor,request_key)

    @app.post('/api/v1/release-requests/{request_key}/cancel')
    def release_transition_fence(request_key:str,actor=Depends(admin)):
        from .release_requests import cancel
        return cancel(store,actor,request_key)

    @app.get('/api/v1/audit', dependencies=[Depends(admin)])
    def audit():
        return store.audit_entries()

    @app.get('/api/v1/audit-summary', dependencies=[Depends(admin)])
    def audit_summary(offset:int=Query(0,ge=0,le=2**53-1),limit:int=Query(20,ge=1,le=50),query:str=Query('',max_length=200),
                      head_sequence:int|None=Query(None,ge=0,le=2**53-1),head_hash:str|None=Query(None,pattern=r'^[a-f0-9]{64}$')):
        from .auditview import summary
        return summary(store,offset,limit,query,head_sequence,head_hash)

    @app.get('/api/v1/audit-entries/{sequence}', dependencies=[Depends(admin)])
    def audit_entry(sequence:int,expected_hash:str=Query(pattern=r'^[a-f0-9]{64}$'),
                    head_sequence:int=Query(ge=0,le=2**53-1),head_hash:str=Query(pattern=r'^[a-f0-9]{64}$')):
        from .auditview import entry
        return entry(store,sequence,expected_hash,head_sequence,head_hash)

    @app.get('/api/v1/integrity',dependencies=[Depends(admin)])
    def integrity_jobs():return integrity.list()

    @app.post('/api/v1/integrity',status_code=202)
    def integrity_start(value:IntegrityInput,actor=Depends(admin)):
        return integrity.create(value.include_files,value.anchor.model_dump() if value.anchor else None)

    @app.get('/api/v1/integrity/{job_id}',dependencies=[Depends(admin)])
    def integrity_job(job_id:str):return integrity.get(job_id)

    @app.get('/api/v1/backups',dependencies=[Depends(admin)])
    def backup_list():
        return {'snapshots':backups.list(),'jobs':backups.jobs(),'busy':backups.busy,
                'restore_roots':{key:str(path) for key,path in backups.restore_roots.items()}}

    @app.post('/api/v1/backups',status_code=202)
    def backup_create(value:BackupInput,actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        return backups.create('backup',actor,value.model_dump(),request_key=idempotency_key)

    @app.get('/api/v1/backup-requests/{request_key}')
    def backup_acceptance(request_key:str,actor=Depends(admin)):
        from .backup_requests import get
        return get(store,actor,request_key)

    @app.post('/api/v1/backup-requests/{request_key}/cancel')
    def backup_fence(request_key:str,actor=Depends(admin)):
        from .backup_requests import cancel
        return cancel(store,actor,request_key,busy=lambda:backups.busy)

    @app.get('/api/v1/backups/{backup_id}/receipt',dependencies=[Depends(admin)])
    def backup_receipt(backup_id:str):return backups.receipt(backup_id)

    @app.get('/api/v1/backups/{backup_id}/download',dependencies=[Depends(admin)])
    def backup_download(backup_id:str):
        return FileResponse(backups.download(backup_id),media_type='application/zip',filename='NX_Backup_'+backup_id+'.zip')

    @app.post('/api/v1/backups/{backup_id}/verify',status_code=202)
    def backup_verify(backup_id:str,actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        return backups.create('verify',actor,backup_id=backup_id,request_key=idempotency_key)

    @app.post('/api/v1/backups/{backup_id}/restore-preview',status_code=202)
    def recovery_preview(backup_id:str,value:RecoveryPreviewInput,actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        return backups.create('preview',actor,value.model_dump(),backup_id,request_key=idempotency_key)

    @app.post('/api/v1/backups/{backup_id}/restore',status_code=202)
    def recovery_create(backup_id:str,value:RecoveryInput,actor=Depends(admin),idempotency_key:str|None=Header(None,min_length=36,max_length=36)):
        return backups.create('restore',actor,value.model_dump(),backup_id,request_key=idempotency_key)

    @app.get('/api/v1/backup-jobs/{job_id}',dependencies=[Depends(admin)])
    def backup_job(job_id:str):return backups.get_job(job_id)

    @app.get('/api/v1/backup-jobs/{job_id}/report',dependencies=[Depends(admin)])
    def backup_report(job_id:str):return backups.report(job_id)

    @app.post('/api/v1/geometry-jobs/{job_id}/collector-package')
    def result_collector(job_id:str,value:CollectorInput,actor=Depends(admin)):
        package,digest=results.collector(job_id,value.root_library_id,value.root_key,actor)
        return Response(package,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="NX_Result_Collector.zip"',
            'X-Content-SHA256':digest})

    @app.post('/api/v1/geometry-jobs/{job_id}/prepared-result',status_code=201)
    async def result_upload(job_id:str,request:Request,actor=Depends(admin)):
        geometry_jobs.get(job_id)
        if request.headers.get('content-type','').split(';')[0]!='application/zip':raise DomainError('Upload the prepared result as application/zip.')
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>MAX_ARCHIVE:raise DomainError('Prepared ZIP exceeds 64 MiB.',413)
            chunks.append(chunk)
        if size!=int(request.headers['content-length']):raise DomainError('Prepared upload length differs from Content-Length.')
        return await run_in_threadpool(results.upload,job_id,b''.join(chunks),actor)

    @app.get('/api/v1/geometry-jobs/{job_id}/prepared-results',dependencies=[Depends(admin)])
    def prepared_results(job_id:str):
        geometry_jobs.get(job_id);return results.list(job_id)

    @app.get('/api/v1/geometry-results/{result_id}',dependencies=[Depends(admin)])
    def prepared_result(result_id:str):return results.get(result_id)

    @app.post('/api/v1/geometry-results/{result_id}/preview')
    def prepared_preview(result_id:str,value:ResultPreviewInput,actor=Depends(admin)):
        return results.preview(result_id,value.target_release_id,column_values=value.column_values)[0]

    @app.post('/api/v1/geometry-results/{result_id}/apply')
    def prepared_apply(result_id:str,value:ResultApplyInput,actor=Depends(admin)):
        return results.apply(result_id,value.target_release_id,value.preview_hash,actor,column_values=value.column_values)

    return app

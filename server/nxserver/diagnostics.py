"""Strict external NX diagnostic schema; no path is dereferenced and no CAM approval."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .safety import DomainError
from .nxworker import summarize


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Body(Strict):
    id: str = Field(min_length=1, max_length=500)
    name: str = Field(max_length=1000)
    solid: bool
    convergent: bool
    face_count: int = Field(ge=0, le=100_000_000)
    layer: int = Field(ge=0, le=65535)
    color: int = Field(ge=0, le=65535)


class Part(Strict):
    path: str = Field(min_length=1, max_length=1024)
    units: str = Field(min_length=1, max_length=100)
    fully_loaded: bool
    bodies: list[Body] = Field(max_length=10000)


class Component(Strict):
    id: str = Field(pattern=r'^/[0-9]+(?:/[0-9]+)*$', max_length=500)
    name: str = Field(max_length=1000)
    part: str = Field(min_length=1, max_length=1024)


class File(Strict):
    path: str = Field(min_length=1, max_length=1024)
    size: int = Field(ge=0, le=1 << 40)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class Report(Strict):
    contract_version: int
    job_id: str
    resource_id: str
    resource_revision: int = Field(ge=1)
    input_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    nx_version: str
    tool_type: str
    status: Literal['needs_selection', 'needs_structure', 'failed']
    automatic_cam_approval: Literal[False]
    parts: list[Part] = Field(max_length=5000)
    components: list[Component] = Field(max_length=10000)
    files: list[File] = Field(max_length=10000)
    errors: list[str] = Field(max_length=10000)
    warnings: list[str] = Field(max_length=10000)
    installation: str | None = Field(default=None, max_length=1024)
    resource_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    worker_hashes: dict[str, str] | None = None
    solid_prototypes: int | None = Field(default=None, ge=0)
    body_prototypes: int | None = Field(default=None, ge=0)
    component_occurrences: int | None = Field(default=None, ge=0)


def relative_output(path):
    """Lexical Windows output path contract, NOT a filesystem operation."""
    if (any(c in path for c in '\\:<>"|?*') or any(ord(c)<32 for c in path) or
            any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in path.split('/'))):
        raise DomainError('Diagnostic paths must be ordinary relative output paths.')
    for part in path.split('/'):
        stem=part.split('.')[0].upper()
        if stem in {'CON','PRN','AUX','NUL'} or re.fullmatch(r'(COM|LPT)[1-9]',stem):
            raise DomainError('Diagnostic paths contain a reserved Windows filename.')
    return path.casefold()


def validate_report(value, job):
    try:
        report=Report.model_validate(value)
    except ValidationError as error:
        details=[{'field':'.'.join(map(str,item['loc'])),'type':item['type']} for item in error.errors()[:10]]
        raise DomainError('Invalid NX diagnostic structure: '+str(details)) from error
    expected={'contract_version':1,'job_id':job['id'],'resource_id':job['resource_id'],
        'resource_revision':job['resource_revision'],'input_sha256':job['input']['sha256'],
        'nx_version':job['nx_version'],'tool_type':job['tool_type'],'automatic_cam_approval':False}
    if any(type(value.get(k)) is not type(v) or value.get(k)!=v for k,v in expected.items()):
        raise DomainError('Report does not match the pinned job or attempts automatic approval.')
    if report.resource_hash is not None and report.resource_hash!=job['resource_hash']:
        raise DomainError('Diagnostic resource hash does not match the pinned revision.')
    if report.worker_hashes is not None and report.worker_hashes!=job['worker_hashes']:
        raise DomainError('Diagnostic worker hashes do not match the pinned job.')
    for messages in (report.errors,report.warnings):
        if any(len(text)>16000 or '\x00' in text for text in messages):
            raise DomainError('Invalid diagnostic message text.')
    def index(items, field):
        result={}
        for item in items:
            key=relative_output(getattr(item,field))
            if key in result:raise DomainError('Repeated case-insensitive diagnostic path.')
            result[key]=item
        return result
    parts,files=index(report.parts,'path'),index(report.files,'path')
    if any(not part.path.lower().endswith('.prt') for part in report.parts):
        raise DomainError('Diagnostic part paths must identify PRT files.')
    for part in report.parts:
        if len({body.id for body in part.bodies})!=len(part.bodies):
            raise DomainError('Repeated body identifier within a diagnostic part.')
    ids=set()
    for component in report.components:
        if component.id in ids:raise DomainError('Repeated component occurrence identifier.')
        ids.add(component.id)
        key=relative_output(component.part)
        if report.status!='failed' and key not in parts:
            raise DomainError('Diagnostic component references an unlisted part.')
    if report.status!='failed':
        if not set(parts)<=set(files):raise DomainError('Diagnostic parts are missing from the file manifest.')
        data=summarize([p.model_dump() for p in report.parts],[c.model_dump() for c in report.components],job['tool_type'])
        if report.status!=data['status']:
            raise DomainError('Diagnostic status contradicts the reported body/assembly inventory.')
        for field in ('solid_prototypes','body_prototypes','component_occurrences'):
            count=getattr(report,field)
            if count is not None and count!=data[field]:raise DomainError('Diagnostic counts contradict the inventory.')
    if report.status in ('failed','needs_structure') and not report.errors:
        raise DomainError('Failed/structurally incomplete diagnostics must describe the errors.')
    if report.status=='needs_selection' and report.errors:
        raise DomainError('A selection-ready diagnostic cannot claim unresolved structural/import errors.')
    return report.model_dump(exclude_unset=True)

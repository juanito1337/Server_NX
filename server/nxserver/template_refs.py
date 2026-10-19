"""PAX dependency checks in planned releases, without NX or reference rewriting."""
import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from .safety import DomainError,offline_path

MAX_BYTES=20*1024*1024
MAX_NODES=50000
MAX_REFS=10000
MAX_PALETTES=1000
MAX_TOTAL_BYTES=256*1024*1024
STAGE_VARIABLES={'UGII_CAM_RESOURCE_DIR':'MACH/resource/','UGII_CAM_CONFIG_DIR':'MACH/resource/configuration/',
    'UGII_CAM_TEMPLATE_PART_DIR':'MACH/resource/template_part/','UGII_CAM_TEMPLATE_PART_METRIC_DIR':'MACH/resource/template_part/metric/',
    'UGII_CAM_TEMPLATE_PART_ENGLISH_DIR':'MACH/resource/template_part/english/','UGII_CAM_TEMPLATE_IMAGE_DIR':'MACH/resource/template_part/images/',
    'UGII_CAM_TEMPLATE_SET_DIR':'MACH/resource/template_set/'}
INSTALL_VARIABLES={'UGII_BASE_DIR':'','UGII_ROOT_DIR':'UGII/','UGII_CAM_BASE_DIR':'MACH/'}

class PaletteBuilder(ET.TreeBuilder):
    def __init__(self,label='PAX'):super().__init__();self.nodes=0;self.depth=0;self.label=label
    def start(self,tag,attrs):
        self.nodes+=1;self.depth+=1
        if self.nodes>MAX_NODES or self.depth>64:raise DomainError(self.label+' exceeds XML node/depth bounds.',409)
        return super().start(tag,attrs)
    def end(self,tag):self.depth-=1;return super().end(tag)
    def doctype(self,*args):raise DomainError(self.label+' DTD/entity declarations are unsupported.',409)

def parse_xml(raw,label='PAX'):
    if len(raw)>MAX_BYTES:raise DomainError(label+' exceeds 20 MiB.',409)
    try:root=ET.fromstring(raw,parser=ET.XMLParser(target=PaletteBuilder(label)))
    except (ET.ParseError,ValueError) as error:raise DomainError(label+' XML is not well formed.',409) from error
    return root

def read_xml(path):
    try:
        with offline_path(path).open('rb') as stream:raw=stream.read(MAX_BYTES+1)
    except OSError as error:raise DomainError('PAX file is unreadable.',409) from error
    return parse_xml(raw)

def parse(raw):
    root=parse_xml(raw)
    if root.tag.rsplit('}',1)[-1]!='Palette':raise DomainError('PAX root must be Palette.',409)
    return root

def relative(value,pax):
    value=value.strip().replace('\\','/')
    if not value or len(value)>1024 or any(ord(char)<32 for char in value):raise DomainError('Empty/invalid PAX reference.',409)
    if value.startswith('@DB/'):raise DomainError('Teamcenter reference is unsupported in this native pilot.',409)
    scope='release';base=str(PurePosixPath(pax).parent)+'/'
    match=re.match(r'^\$\{([A-Za-z0-9_]+)\}',value)
    if match:
        name=match[1];value=value[match.end():]
        if name in STAGE_VARIABLES:base=STAGE_VARIABLES[name]
        elif name in INSTALL_VARIABLES:scope='installation';base=INSTALL_VARIABLES[name]
        else:raise DomainError('Unknown/implicit PAX search variable: '+name,409)
        value=value.lstrip('/')
    elif value.startswith('/') or ':' in value:raise DomainError('Absolute/network PAX reference is not portable to a new release.',409)
    if '${' in value or '%' in value or ':' in value:raise DomainError('PAX reference contains an unresolved variable or URI.',409)
    parts=[]
    for part in (base+value).split('/'):
        if part in ('','.'):continue
        if part=='..':
            if not parts:raise DomainError('PAX reference escapes its declared root.',409)
            parts.pop()
        else:parts.append(part)
    from .migration import portable
    path='/'.join(parts);portable(path)
    return scope,path

def planned(store,release,plan):
    from .store import sha256
    known={item['path'].casefold():dict(item) for item in release['manifest']}
    for item in plan.get('file_deletions',[]):known.pop(item['path'].casefold(),None)
    for item in plan['assets'].values():known[item['path'].casefold()]={name:item[name] for name in ('path','sha256','size')}
    from .libraries import SPECS
    for identifier,table in plan.get('tables',{}).items():
        raw=table.text.encode('utf-8-sig');path=SPECS[identifier]['path']
        known[path.casefold()]={'path':path,'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)}
    palettes=[item for item in plan['assets'].values() if item.get('file_package') and item['path'].lower().endswith('.pax')]
    rows=[];errors=[];inputs={};complete=True;total_bytes=0
    if plan.get('file_package_planning_error'):
        complete=False
        errors.append({'resource_id':None,'pax_path':'','reason':plan['file_package_planning_error'],
                       'status':'conflict','upstream_conflict':True})
    owners={entry['path'].casefold():resource['id'] for resource in plan['catalog'] for entry in (resource.get('file_package') or {}).get('entries',[])}
    for number,palette in enumerate(sorted(palettes,key=lambda item:item['path'])):
        path=palette['path'];owner=owners.get(path.casefold())
        if number>=MAX_PALETTES or total_bytes+palette['size']>MAX_TOTAL_BYTES:
            complete=False;errors.append({'resource_id':owner,'pax_path':path,'reason':'PAX collection exceeds palette/byte bounds.','status':'conflict'});break
        try:
            source=offline_path(palette['source'])
            with source.open('rb') as stream:raw=stream.read(MAX_BYTES+1)
            total_bytes+=len(raw)
            if len(raw)!=palette['size'] or hashlib.sha256(raw).hexdigest()!=palette['sha256']:raise DomainError('PAX attachment changed during planning.',409)
            root=parse(raw);ids=set()
            for entry in root:
                if entry.tag.rsplit('}',1)[-1]!='PaletteEntry':continue
                identifier=entry.get('id','')
                if not identifier or identifier in ids:raise DomainError('Missing/duplicate PAX entry ID.',409)
                ids.add(identifier)
                for node in entry.iter():
                    tag=node.tag.rsplit('}',1)[-1]
                    if tag in ('Filename','Configuration'):
                        if list(node):raise DomainError('Nested PAX reference markup is unsupported.',409)
                        value=node.text or ''
                    elif tag=='PreviewImage':value=node.get('location','')
                    else:continue
                    record={'resource_id':owner,'pax_path':path,'entry_id':identifier,'field':tag,'value':value}
                    try:
                        scope,target=relative(value,path);record.update(scope=scope,target=target)
                        if scope=='release':
                            item=known.get(target.casefold())
                            if not item:raise DomainError('Referenced file is missing from the planned release: '+target,409)
                            record.update(target=item['path'],sha256=item['sha256'],size=item['size'])
                        else:
                            base=store.standards.get(release['nx_version'])
                            if base is None:raise DomainError('Matching Siemens installation is required for this PAX reference.',409)
                            file=offline_path(base/target)
                            if not file.is_file():raise DomainError('Referenced installation file is missing: '+target,409)
                            stamp={'root':str(base),'path':target,'size':file.stat().st_size,'sha256':sha256(file),'nx_version':release['nx_version']}
                            inputs[(str(base),target)]=stamp;record.update(size=stamp['size'],sha256=stamp['sha256'])
                        record['status']='present'
                    except DomainError as error:record.update(status='conflict',reason=str(error));errors.append(record)
                    if len(rows)>=MAX_REFS:raise DomainError('PAX dependencies exceed reference bound.',409)
                    rows.append(record)
        except (DomainError,OSError) as error:
            complete=False;errors.append({'resource_id':owner,'pax_path':path,'reason':str(error),'status':'conflict'})
            if len(rows)>=MAX_REFS:break
    report={'contract_version':1,'palettes':len(palettes),'references':rows,'errors':errors,'installation_inputs':list(inputs.values()),
        'nx_version':release['nx_version'],'technical_only':True,'nx_execution_verified':False,'automatic_approval':False,
        'path_rewrites':False,'binary_dependencies_verified':False,'ui_reachability_verified':False,'complete':complete,
        'scope':'managed_pax_declared_file_references'}
    plan['template_references']=report
    return report

def verify(store,folder,report):
    from .store import sha256
    for item in report.get('installation_inputs',[]):
        root=store.standards.get(item['nx_version'])
        if root is None or str(root)!=item['root']:raise DomainError('PAX installation root changed before export commit.',409)
        path=offline_path(root/item['path'])
        if not path.is_file() or path.stat().st_size!=item['size'] or sha256(path)!=item['sha256']:raise DomainError('PAX installation dependency changed before export commit.',409)
    for item in report.get('references',[]):
        if item['status']!='present':raise DomainError('Unresolved PAX dependency cannot be committed.',409)
        if item['scope']=='release':
            path=offline_path(folder/item['target'])
            if not path.is_file() or path.stat().st_size!=item['size'] or sha256(path)!=item['sha256']:raise DomainError('Copied PAX dependency differs from reviewed bytes.',409)

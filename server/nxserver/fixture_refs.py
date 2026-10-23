"""NX2406 fixture KRX declared-file graph, never binary assembly inspection.

The installed 2406 examples use KRX-parent / Folder / RootPart (graphics) and
KRX-parent / Folder / Icon. A leading slash in RootPart is this declared local
folder convention, not a filesystem-root lookup. All resolution is lexical.
"""
from pathlib import PurePosixPath

from .safety import DomainError,asset_filename,offline_path,read_bounded_file,file_fingerprint
from .migration import portable
from .template_refs import parse_xml

ROOT='MACH/resource/library/fixture/'
MAX_DESCRIPTORS=1000
MAX_TOTAL_BYTES=128*1024*1024
MAX_REFERENCES=10000
MAX_DESCRIPTOR_BYTES=1024*1024


def bounded_descriptor(raw):
    if len(raw)>MAX_DESCRIPTOR_BYTES:raise DomainError('KRX descriptor exceeds 1 MiB.',409)
    return raw


def references(raw,path):
    portable(path)
    if not path.startswith(ROOT) or not path.lower().endswith('.krx'):
        raise DomainError('NX2406 fixture descriptor must use its declared fixture directory.')
    root=parse_xml(bounded_descriptor(raw),label='NX2406 fixture KRX')
    if root.tag!='RootItemCollection' or root.attrib or not list(root) or (root.text or '').strip():
        raise DomainError('KRX must be a nonempty RootItemCollection without extensions.')
    rows=[];names=set()
    for item in root:
        if item.tag!='RootItem' or list(item) or (item.text or '').strip() or (item.tail or '').strip() or set(item.attrib)-{'Name','DisplayName','Icon','RootPart','Folder','Subtype'}:
            raise DomainError('Unsupported KRX RootItem structure; no implicit file references.')
        name=item.get('Name','');folder=item.get('Folder','');icon=item.get('Icon','');part=item.get('RootPart','')
        for value in (name,folder,icon):asset_filename(value)
        if any(char in folder+icon for char in ('%','$')):
            raise DomainError('KRX Folder/Icon may not contain variables or implicit search paths.')
        if name.casefold() in names:raise DomainError('Repeated case-insensitive KRX item name.')
        names.add(name.casefold())
        if len(names)>100:raise DomainError('KRX exceeds 100 declared items.')
        if (not part.startswith('/graphics/') or part.startswith('//') or '\\' in part or
            any(value in part for value in (':','%','$')) or
            not part.lower().endswith('.prt')):
            raise DomainError('KRX RootPart must be an explicit /graphics/*.prt reference in its own Folder.')
        portable(part[1:])
        for segment in part[1:].split('/'):asset_filename(segment)
        if PurePosixPath(icon).suffix.lower() not in ('.bmp','.png','.jpg','.jpeg'):
            raise DomainError('KRX icon must name a supported image in its own Folder.')
        base=PurePosixPath(path).parent/folder
        for field,target in [('RootPart',str(base/part[1:])),('Icon',str(base/icon))]:
            portable(target)
            rows.append({'descriptor':path,'item':name,'field':field,'target':target})
    return rows


def bind(raw,path,known):
    rows=references(raw,path)
    index={item['path'].casefold():item for item in known}
    for row in rows:
        target=index.get(row['target'].casefold())
        if target is None or target['path']!=row['target']:
            raise DomainError('KRX target is missing or differs in path casing: '+row['target'],409)
        if 'size' in target and target['size']<=0:raise DomainError('KRX target must not be empty: '+row['target'],409)
        row.update({key:target[key] for key in ('sha256','size') if key in target})
    return rows


def validate_package(entries,contents):
    if not contents:raise DomainError('NX2406 fixture packages require a KRX descriptor, not unregistered model files.')
    rows=[]
    for path,raw in contents.items():rows.extend(bind(raw,path,entries))
    return {'scope':'nx2406_declared_krx_files','references':rows,'binary_prt_dependencies_verified':False,
            'nx_execution_verified':False,'automatic_approval':False}


def planned(store,release,plan):
    report={'contract_version':1,'scope':'nx2406_declared_krx_files','references':[],'descriptors':[],
            'errors':[],'complete':True,'binary_prt_dependencies_verified':False,
            'nx_execution_verified':False,'automatic_approval':False}
    plan['fixture_references']=report
    if release['nx_version']!='2406':return report
    known={item['path'].casefold():dict(item) for item in release['manifest']}
    for item in plan.get('file_deletions',[]):known.pop(item['path'].casefold(),None)
    for item in plan['assets'].values():known[item['path'].casefold()]={k:item[k] for k in ('path','sha256','size')}
    # Detect canonical-root case variants too. Windows can load them; ignoring
    # them would bypass reference validation. references() rejects bad casing.
    descriptors=[item for item in known.values() if item['path'].casefold().startswith(ROOT.casefold()) and item['path'].lower().endswith('.krx')]
    total=0
    for index,item in enumerate(sorted(descriptors,key=lambda x:x['path'])):
        try:
            if index>=MAX_DESCRIPTORS or total+item['size']>MAX_TOTAL_BYTES:
                raise DomainError('KRX descriptor collection exceeds its count/byte budget.',409)
            asset=plan['assets'].get(item['path'].casefold())
            source=asset['source'] if asset else offline_path(release['folder'])/item['path']
            raw=read_bounded_file(source,max_bytes=MAX_DESCRIPTOR_BYTES,expected_size=item['size'],expected_sha256=item['sha256'],
                                 changed_message='KRX descriptor differs from the reviewed bytes.')
            total+=len(raw)
            rows=bind(raw,item['path'],known.values())
            if len(report['references'])+len(rows)>MAX_REFERENCES:
                report['complete']=False;report['errors'].append({'path':item['path'],'reason':'KRX declared references exceed the report bound.'});break
            report['references'].extend(rows)
            report['descriptors'].append(item)
        except (DomainError,OSError) as error:
            report['complete']=False;report['errors'].append({'path':item['path'],'reason':str(error)})
            if index>=MAX_DESCRIPTORS or total+item['size']>MAX_TOTAL_BYTES:break
    plan['fixture_references']=report
    return report


def verify(folder,report):
    if not report.get('complete') or report.get('errors'):raise DomainError('Unresolved KRX dependencies cannot be committed.',409)
    checked={item['path']:item for item in report.get('descriptors',[])}
    for item in report.get('references',[]):checked[item['target']]={'path':item['target'],'sha256':item['sha256'],'size':item['size']}
    for item in checked.values():
        try:actual=file_fingerprint(offline_path(folder)/item['path'],max_bytes=item['size'])
        except (DomainError,OSError) as error:raise DomainError('Copied KRX dependency differs from reviewed bytes.',409) from error
        if actual!={key:item[key] for key in ('size','sha256')}:
            raise DomainError('Copied KRX dependency differs from reviewed bytes.',409)

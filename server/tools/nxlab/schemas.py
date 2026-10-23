"""Read only explicitly known native schema files from the local NX installation.

No directory traversal, installation writes, NX execution or network discovery.
Full installed record contents are never placed in the evidence report.
"""
import hashlib
from nxserver.libraries import SPECS,NativeTable,decode
from nxserver.safety import read_bounded_file
from .guard import local,executable_version,Blocked


def inspect_fixtures(installation):
    from nxserver.fixture_refs import ROOT,references,bind
    from nxserver.safety import file_fingerprint
    root=local(installation)
    version=executable_version(root/'NXBIN/ugraf.exe')
    if version!='2406.5001.0.0':raise Blocked('Unqualified NX fixture baseline: '+version)
    cases=[]
    # Explicit known local examples only; no recursive installation/share scan.
    for category in ('Chuck','Vise'):
        relative=ROOT+category+'/FixtureConf.krx'
        case={'id':'nx2406-'+category.lower()+'-layout','status':'failed','descriptor':relative,
              'nx_execution_verified':False,'prt_component_dependencies_verified':False,'units_verified':False}
        try:
            raw=read_bounded_file(local(root/relative),max_bytes=64*1024)
            pin=hashlib.sha256(raw).hexdigest()
            rows=references(raw,relative);known=[]
            for row in rows:
                known.append({'path':row['target'],**file_fingerprint(local(root/row['target']))})
            bound=bind(raw,relative,known)
            read_bounded_file(local(root/relative),max_bytes=64*1024,expected_size=len(raw),expected_sha256=pin)
            case.update(status='passed',descriptor_sha256=pin,declared_references=bound,
                        note='Installed KRX and its declared root part/icon exist. No model was opened or qualified.')
        except Exception as error:case['message']=str(error)
        cases.append(case)
    return version,cases


def inspect(installation):
    root=local(installation)
    version=executable_version(root/'NXBIN/ugraf.exe')
    if version!='2406.5001.0.0':raise Blocked('Unqualified NX schema baseline: '+version)
    cases=[]
    for identifier,spec in SPECS.items():
        path=local(root/spec['path'])
        case={'id':identifier,'status':'failed','relative_path':spec['path']}
        try:
            raw=read_bounded_file(path,max_bytes=32*1024*1024)
            digest=hashlib.sha256(raw).hexdigest()
            table=NativeTable(decode(raw),schema_only=True,max_schemas=1000,max_fields=100000)
            if not table.blocks:raise ValueError('No FORMAT definitions in installed library.')
            after=read_bounded_file(path,max_bytes=32*1024*1024,expected_size=len(raw),expected_sha256=digest)
            case.update(status='passed',sha256=digest,size=len(after),kind=spec['kind'],
                        format_count=len(table.blocks),fields=[block['fields'] for block in table.blocks])
        except Exception as error:case['message']=str(error)
        cases.append(case)
    return version,cases

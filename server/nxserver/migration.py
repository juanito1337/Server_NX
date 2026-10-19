"""Whole-share three-way migration. Reads SIDT instructions as data, never runs them."""
import hashlib
import os
import re
from collections import Counter
from pathlib import Path, PurePosixPath

from .libraries import (SPECS, OPTIONAL_PALETTE, NativeTable, legacy_database,
                        normalization_plan, projection_block, records_hash)
from .libraries import reference_report
from .safety import DomainError, disjoint, offline_path, tree_files,export_path_casing,read_bounded_file
from .store import digest, sha256
from .installation_version import installation_version,version_evidence
from .sidt_version import declaration as sidt_declaration,evidence as sidt_evidence

FILES = ('10.ugf','ugcolor.cdf','ugsmd_def.std','thd_english.dat','thd_metric.dat')
TREES = ('UGII/udf','UGII/materials','NXPLOT/config/pm_server','MACH/custom',
         'MACH/resource','CMM_INSPECTION/resource')
OVERLAYS = (('applications/nx/nxplot','NXPLOT/config/pm_server'),('applications/nx/ugii','UGII'))
EXTENSIONS = ('UGII/startup/','UGII/application/','UGII/udo/','UGII/menus/','UGII/templates/',
              'UGII/udf/','UGII/materials/','NXPLOT/config/pm_server/',
              'MACH/custom/','MACH/resource/','CMM_INSPECTION/custom/','CMM_INSPECTION/resource/')
CUSTOM_DIRS = 'MACH/resource/usertools/ugii/menus/custom_dirs.dat'
REVIEW_TYPES = {'.dll','.dpx','.exe','.jar','.prt','.pyc','.so'}
PAIRS = {('2406','2512')}


def current_plan(report,request):
    """New plan digest is reconstructible without exposing private input paths."""
    if not isinstance(report,dict) or report.get('plan_contract_version')!=1 or report.get('scope')!='nx_folder_upgrade_plan':return False
    check=report.get('prerequisites')
    if (not isinstance(check,dict) or check.get('installation_version_contract_version')!=1
            or type(check.get('sidt_version_contract_version')) is not int or check['sidt_version_contract_version']!=1
            or not isinstance(check.get('versions'),list) or len(check['versions'])!=2
            or any(not isinstance(item,dict) or not version_evidence(item.get('version_metadata'),version)
                or not isinstance(item.get('profile'),dict) or not sidt_evidence(item['profile'].get('version_declaration'),version)
                for item,version in zip(check['versions'],(request.get('source_version'),request.get('target_version'))))):return False
    value=dict(report);expected=value.pop('preview_hash',None)
    return (report.get('request_sha256')==digest(request) and report.get('result_manifest_sha256')==digest(report.get('result_manifest'))
            and expected==digest(value) and report.get('read_only') is True and report.get('nx_files_written') is False
            and report.get('automatic_approval') is False and report.get('automatic_activation') is False)


def portable(relative):
    if ('\\' in relative or ':' in relative or relative.startswith('/') or
            any(p in ('','.','..') or p.endswith((' ','.')) or
                any(c in p for c in '*?"<>|') or any(ord(c)<32 for c in p)
                or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',p)
                for p in relative.split('/'))):
        raise DomainError('Non-portable migration relative path: '+relative)
    return relative


def validated_file_choices(value):
    """One explicit decision per logical Windows path; no last-key-wins rule."""
    if (not isinstance(value,dict) or
            any(not isinstance(path,str) or choice not in ('standard','customer') for path,choice in value.items())):
        raise DomainError('File choices must be relative path -> standard/customer.')
    choices={}
    for path,choice in value.items():
        key=portable(path).casefold()
        if key in choices:
            raise DomainError('File choices contain repeated case-insensitive paths; keep exactly one explicit choice per file.')
        choices[key]=choice
    return choices


def after_install_profile(sidt,expected_version=None):
    path=offline_path(sidt/'applications/nx/after_install.bat')
    if not path.is_file():raise DomainError('Extracted SIDT after_install.bat is missing.')
    raw=read_bounded_file(path,max_bytes=1024*1024,limit_message='Unexpectedly large or empty SIDT instruction file.')
    pairs=[]
    for line in raw.decode('cp1252').splitlines():
        if not re.match(r'(?i)^\s*xcopy\s',line):continue
        tokens=line.strip().split()
        if len(tokens)<4:raise DomainError('Unrecognized SIDT XCOPY instruction.')
        pairs.append((tokens[2].strip('"').replace('\\','/').lower(),tokens[3].strip('"').replace('\\','/').lower()))
    prefix='%splm_appl_dir%/%nx_version_dir%/'
    destination='%splm_shr_dir%/%nx_shr_version_dir%/'
    expected=[(prefix+'ugii/'+name,destination+'ugii') for name in FILES]
    expected.extend((prefix+name.lower(),destination+name.lower()) for name in TREES)
    expected.extend(('%sidt_dir%/'+source,destination+target.lower()) for source,target in OVERLAYS)
    if Counter(pairs)!=Counter(expected):
        raise DomainError('SIDT copy layout differs from the approved 2406/2512 adapter. No installer script is executed.')
    return {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'copy_instructions':len(pairs),
        'version_declaration':sidt_declaration(sidt,expected_version)}


def prerequisites(store, source_version, target_version):
    if (source_version,target_version) not in PAIRS:
        raise DomainError('No approved whole-share migration adapter for this version pair.')
    versions=[]
    for version in (source_version,target_version):
        errors=[];installation=store.standards.get(version);sidt=store.sidt_root(version) if hasattr(store,'sidt_root') else store.sidts.get(version)
        profile=None;metadata=None
        if installation is None:errors.append('Siemens standard installation path is not configured.')
        elif not offline_path(installation/'NXBIN/ugraf.exe').is_file():errors.append('NXBIN/ugraf.exe is missing; provide the full target/original installation data.')
        else:
            try:
                metadata=installation_version(installation)
                if metadata['nx_version']!=version:
                    errors.append('Configured NX'+version+' installation contains NX'+metadata['strings']['ProductVersion']+' metadata; select the matching original/target installation.')
            except DomainError as error:errors.append(str(error))
        if sidt is None:errors.append('Extracted SIDT directory is not configured.')
        else:
            try:profile=after_install_profile(sidt,version)
            except DomainError as error:errors.append(str(error))
        versions.append({'nx_version':version,'installation':str(installation) if installation else None,
            'sidt':str(sidt) if sidt else None,'profile':profile,'version_metadata':metadata,'ready':not errors,'errors':errors})
    return {'versions':versions,'ready':all(v['ready'] for v in versions),'nx_execution_verified':False,
            'installation_version_contract_version':1,'sidt_version_contract_version':1,'nx_required_on_test_pc':True,'installer_execution':False}


def request_source(store,request):
    identifier=request.get('source_release_id')
    if identifier:
        if request.get('source') or request.get('source_path'):
            raise DomainError('Choose a managed source release OR an external source folder, not both.')
        release=store.get_release(identifier)
        folder=store.release_folder(identifier)
        if release['nx_version']!=request['source_version'] or release['status'] not in ('draft','validated','approved') or store.manifest(folder)!=release['manifest']:
            raise DomainError('Source release must be complete, intact and match the original NX version.',409)
        return folder
    if not request.get('source') or not request.get('source_path'):raise DomainError('Choose an old source folder or managed release.')
    return store.folders.source(request['source'],request['source_path'])


class Planner:
    def __init__(self,store):self.store=store

    def build(self,request,progress=lambda *args:None,job_id=None):
        choices=validated_file_choices(request.get('file_resolutions',{}))
        source_version,target_version=request['source_version'],request['target_version']
        check=prerequisites(self.store,source_version,target_version)
        if not check['ready']:raise DomainError('Configure complete Siemens/SIDT inputs for both versions: '+str(check['versions']),409)
        source=request_source(self.store,request)
        destination=self.store.folders.destination(request['target'],request['target_path'],source,exclude_job=job_id)
        inputs={value['path']:value['sha256'] for item in check['versions']
            for value in (item['profile'],item['version_metadata'],item['profile']['version_declaration'])};indexes={}
        sidts={version:self.store.sidt_root(version) for version in (source_version,target_version)}
        for root in [source,*[self.store.standards[v] for v in (source_version,target_version)],
                     *[sidts[v] for v in (source_version,target_version)]]:
            disjoint(destination,root)
        def entry(relative,path,reason):
            relative=portable(relative);path=offline_path(path)
            key=str(path)
            if key not in inputs:inputs[key]=sha256(path)
            return {'path':relative,'source':key,'sha256':inputs[key],'size':path.stat().st_size,'reason':reason}
        def optional_stock_entry(version,relative,reason):
            # Do not probe a child below a baseline file. Keep the planned
            # customer entry so the final pure-string tree gate can report the
            # file/directory collision, rather than failing with ENOTDIR.
            folder=offline_path(self.store.standards[version])
            parts=relative.split('/')
            for part in parts[:-1]:
                folder=offline_path(folder/part)
                if not folder.is_dir():return None
            path=offline_path(folder/parts[-1])
            return entry(relative,path,reason) if path.is_file() else None
        def baseline(version):
            root,sidt=self.store.standards[version],sidts[version]
            result={}
            for name in FILES:
                path=offline_path(root/'UGII'/name)
                if not path.is_file():raise DomainError('Required Siemens baseline file is missing: '+str(path))
                value=entry('UGII/'+name,path,'siemens_standard');result[value['path'].casefold()]=value
            for relative in TREES:
                folder=offline_path(root/relative)
                if not folder.is_dir():raise DomainError('Required Siemens baseline directory is missing: '+str(folder))
                for suffix,path in tree_files(folder):
                    value=entry(relative+'/'+suffix,path,'siemens_standard');key=value['path'].casefold()
                    if key in result:raise DomainError('Case-insensitive Siemens path collision: '+value['path'])
                    result[key]=value
            for relative,target in OVERLAYS:
                folder=offline_path(sidt/relative)
                if not folder.is_dir():raise DomainError('SIDT overlay directory is missing: '+str(folder))
                for suffix,path in tree_files(folder):
                    value=entry(target+'/'+suffix,path,'sidt_overlay');result[value['path'].casefold()]=value
            return result
        progress('planning',5,'Inventarisierung der unveraenderten After-Install-Standards')
        old,new=baseline(source_version),baseline(target_version)
        output=dict(new);actions=[];ignored=[];conflicts=[];warnings=[]
        source_files=dict(tree_files(source))
        for relative,path in source_files.items():
            key=portable(relative).casefold()
            if key in indexes:raise DomainError('Case-insensitive customer path collision: '+relative)
            indexes[key]=relative
        library_paths={s['path'].casefold() for s in SPECS.values()}
        progress('planning',20,'Vergleich der Kundenabweichungen und Bibliotheksschemata')
        converted,library_report=normalization_plan(source,self.store.standards,target_version,source_version,
            request.get('defaults'),request.get('resolutions'),request.get('legacy_omissions'),source_version=source_version,
            reference_resolutions=request.get('reference_resolutions'),include_target_libraries=True)
        for key,value in library_report['input_hashes'].items():
            if key in inputs and inputs[key]!=value:raise DomainError('Library changed during migration planning.',409)
            inputs[key]=value
        conflicts.extend({**item,'category':'references' if item.get('type')=='reference' else 'library'} for item in library_report['conflicts'])
        new_columns={}
        for spec in SPECS.values():
            oldpath=offline_path(self.store.standards[source_version]/spec['path'])
            baseline_table=NativeTable.read(oldpath) if oldpath.is_file() else None
            target_path=offline_path(self.store.standards[target_version]/spec['path'])
            if not target_path.is_file():continue
            target_table=NativeTable.read(target_path)
            for relative in (spec['path'],spec['legacy_path']):
                if not relative or relative not in source_files:continue
                customer=NativeTable.read(source_files[relative])
                for key,records in customer.groups().items():
                    if baseline_table and key in baseline_table.groups() and records_hash(records)==records_hash(baseline_table.groups()[key]):continue
                    for record in records:
                        try:block=projection_block(record,customer,target_table)
                        except DomainError:continue  # Reported by the shared normalizer; no guessed projection.
                        for field in block['fields']:
                            if field not in record['values']:
                                values=request.get('defaults',{}).get(spec['id'],{}).get(block['id'],{})
                                item=new_columns.setdefault((spec['id'],block['id'],field),{
                                    'library_id':spec['id'],'block_id':block['id'],'field':field,
                                    'confirmed':field in values,'value':values.get(field),'keys':[]})
                                if key not in item['keys']:item['keys'].append(key)
        for relative,path in source_files.items():
            key=relative.casefold()
            if key==OPTIONAL_PALETTE.casefold():output.pop(key,None);actions.append({'path':relative,'action':'optional_palette_omitted'});continue
            if legacy_database(relative):output.pop(key,None);actions.append({'path':relative,'action':'legacy_library_merged'});continue
            if key in library_paths:continue
            if key not in old and not any(key.startswith(prefix.casefold()) for prefix in EXTENSIONS) and key not in ('ugii/ugii_env.dat','ugii/ugii_env_ug_nx2406.dat'):
                ignored.append(relative);continue
            customer=entry(relative,path,'customer')
            old_entry=old.get(key)
            if old_entry is None:
                # Installation copies may include unchanged startup/program data
                # that the SIDT script never puts on the share.
                old_entry=optional_stock_entry(source_version,relative,'old_installation_extension')
                if old_entry and customer['sha256']==old_entry['sha256']:
                    ignored.append(relative);continue
            if key=='ugii/ugii_env_ug_nx2406.dat' and old_entry is None:
                stock=offline_path(self.store.standards[source_version]/'UGII/ugii_env_ug.dat')
                if stock.is_file() and customer['sha256']==entry(relative,stock,'old_environment_snapshot')['sha256']:
                    actions.append({'path':relative,'action':'old_environment_snapshot_omitted'});continue
            current=new.get(key)
            if current is None:
                current=optional_stock_entry(target_version,relative,'new_installation_extension')
            if key==CUSTOM_DIRS.casefold():
                raw=path.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=customer['sha256']:raise DomainError('Configuration changed during planning.',409)
                try:text=raw.decode('utf-8-sig')
                except UnicodeDecodeError:text=raw.decode('cp1252')
                target=self.store.host_folder(destination) or str(destination)
                converted[relative]=re.sub(r'(?i)W:[\\/]SiemensShare[\\/]nx2406(?![A-Za-z0-9_])',lambda match:target,text)
                actions.append({'path':relative,'action':'target_path_converted'});continue
            if old_entry and customer['sha256']==old_entry['sha256']:
                actions.append({'path':relative,'action':'standard_upgraded' if current else 'obsolete_standard_omitted'});continue
            if current and customer['sha256']==current['sha256']:
                output[key]=current;actions.append({'path':relative,'action':'unchanged'});continue
            safe=(old_entry is None and current is None) or (old_entry and current and old_entry['sha256']==current['sha256'])
            choice=choices.get(key)
            if choice=='standard':
                if current:output[key]=current
                else:output.pop(key,None)
                actions.append({'path':relative,'action':'standard_selected'})
            elif safe or choice=='customer':
                output[key]=customer;actions.append({'path':relative,'action':'customer_copied','explicit_choice':choice})
                if Path(relative).suffix.lower() in REVIEW_TYPES:warnings.append({'path':relative,'reason':'NX/version compatibility needs qualified testing.'})
            else:conflicts.append({'category':'file','path':relative,'reason':'Customer and Siemens changed the same path, or the new standard removed it.',
                'customer_sha256':customer['sha256'],'old_sha256':old_entry['sha256'] if old_entry else None,
                'target_sha256':current['sha256'] if current else None})
        for key in list(output):
            if key==OPTIONAL_PALETTE.casefold() or legacy_database(output[key]['path']):output.pop(key)
        for relative,text in converted.items():
            output[relative.casefold()]={'path':relative,'content':text.encode('utf-8-sig'),'sha256':hashlib.sha256(text.encode('utf-8-sig')).hexdigest(),
                'size':len(text.encode('utf-8-sig')),'reason':'schema_or_path_conversion'}
        references=library_report['references']
        for path,expected in inputs.items():
            if sha256(offline_path(path))!=expected:raise DomainError('Migration inputs changed while planning.',409)
        files=sorted(output.values(),key=lambda item:item['path'].casefold())
        try:export_path_casing(item['path'] for item in files)
        except DomainError as error:
            conflicts.append({'category':'path','path':'','scope':'planned_target_tree','reason':str(error)})
        if os.name=='nt':
            for item in files:
                if len(str(destination/item['path']))>=255:
                    conflicts.append({'category':'path','path':item['path'],'reason':'Target exceeds the supported Windows path limit; choose a shorter destination.'})
        report={'plan_contract_version':1,'scope':'nx_folder_upgrade_plan','read_only':True,'nx_files_written':False,
            'automatic_approval':False,'automatic_activation':False,'request_sha256':digest(request),
            'input_sha256':digest(inputs),'input_count':len(inputs),'source_release_id':request.get('source_release_id'),
            'source_folder':str(source),'destination':str(destination),'host_destination':self.store.host_folder(destination),
            'source_version':source_version,'target_version':target_version,'prerequisites':check,
            'file_count':len(files),'bytes':sum(item['size'] for item in files),'actions':actions,'ignored_outside_scope':ignored,
            'conflicts':conflicts,'warnings':warnings,'new_columns':list(new_columns.values()),'libraries':library_report,
            'references':references,
            'can_build':not conflicts,'functional_validation_required':True,
            'result_manifest':[{'path':item['path'],'size':item['size'],'sha256':item['sha256']} for item in files]}
        report['result_manifest_sha256']=digest(report['result_manifest'])
        report['preview_hash']=digest(report)
        return {'files':files,'inputs':inputs,'destination':destination},report

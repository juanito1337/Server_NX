"""Inert server fixtures only. These bytes are neither PRT models nor NX evidence."""
import hashlib
import io
import json
import zipfile

from nxserver.libraries import NativeTable, SPECS, native_cam
from .guard import local


def table(fields,values):
    return '#CLASS SAMPLE\nFORMAT '+' '.join(fields)+'\nDATA | '+' | '.join(values)+'\n#END_DATA\n'


def source(directory):
    directory=local(directory);directory.mkdir(parents=True,exist_ok=False)
    for identifier,fields,values in [
        ('tool-metric',['LIBRF','DESCR','HLDREF'],['STD','Synthetic standard','']),
        ('holder-metric',['LIBRF','DESCR'],['STDH','Synthetic standard holder'])]:
        path=local(directory/SPECS[identifier]['path']);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(table(fields,values),encoding='utf-8')
    return directory


def prepared(job):
    contents={'NXTEST.prt':b'SYNTHETIC SERVER CONTRACT. NOT A PRT MODEL.',
              'holder.prt':b'SYNTHETIC SERVER COMPONENT. NOT A PRT MODEL.'}
    objects=[]
    for identifier,fields,values in [
        ('tool-metric',['LIBRF','DESCR','HLDREF'],['NXTEST','Synthetic return','NXTEST_HOLDER']),
        ('holder-metric',['LIBRF','DESCR'],['NXTEST_HOLDER','Synthetic return holder'])]:
        native=NativeTable(table(fields,values))
        objects.append({'cam':native_cam(identifier,job['nx_version'],native,native.records)})
    manifest={**{name:job[name] for name in ('resource_id','resource_revision','resource_hash','worker_hashes','nx_version','tool_type')},
        'contract_version':1,'job_id':job['id'],'input_sha256':job['input']['sha256'],
        'automatic_cam_approval':False,'root_library_id':'tool-metric','root_key':'NXTEST','root_part':'NXTEST.prt',
        'objects':objects,'parts':[{'path':name,'units':'Millimeters','fully_loaded':True,
            'bodies':[{'id':'B1','name':'Synthetic','solid':True,'convergent':False,'face_count':6,'layer':1,'color':42}]}
            for name in contents],
        'components':[{'id':'/0','name':'Synthetic component','part':'holder.prt'}],
        'files':[{'path':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in contents.items()],
        'collector':'Synthetic server transport contract; no NX execution or CAM acceptance.'}
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps(manifest))
        for name,data in contents.items():archive.writestr('files/'+name,data)
    return output.getvalue()


def adopt_drafts(server,release,job):
    archive=prepared(job)
    result=server.request('/api/v1/geometry-jobs/'+job['id']+'/prepared-result','POST',raw=archive)
    url='/api/v1/geometry-results/'+result['id'];value={'target_release_id':release['id']}
    preview=server.request(url+'/preview','POST',value)
    if not preview['can_apply']:raise AssertionError('Synthetic result adoption failed.')
    server.request(url+'/apply','POST',{**value,'preview_hash':preview['preview_hash']})
    resources=server.request('/api/v1/resources')
    if len(resources)!=2 or any(resource['cam_status']!='draft' for resource in resources):
        raise AssertionError('Result adoption must retain both related objects as drafts.')
    parent=next(resource for resource in resources if resource['id']==job['resource_id'])
    if parent['revision']!=job['resource_revision']+1 or any(parent['geometry']['checks'].values()):
        raise AssertionError('Imported inventory must reset all functional checks and advance the revision.')
    if parent['model_package']['trust']!='unverified_external_nx_inventory':
        raise AssertionError('Synthetic inventory must remain unverified.')
    return server.request(url),resources

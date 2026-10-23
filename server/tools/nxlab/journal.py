"""Test-only NXOpen journal. Requires an isolated account; never run by pytest.

UF primitive creation and STEP APIs are capability-checked at runtime. Until a
real qualified NX run completes these journals are implementation, not evidence.
"""
import hashlib
import json
import math
import os
import stat
import sys
from pathlib import Path


def guarded(value):
    text=str(value).replace('\\','/')
    if (not text.casefold().startswith('c:/nx_migration/tooljobs/') or
        '..' in text.split('/') or text!=text.strip() or len(text)>200):
        raise RuntimeError('NX lab journal requires a dedicated C:/NX_Migration/ToolJobs directory.')
    path=Path(value)
    for item in reversed([path]+list(path.parents)):
        try:info=item.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise RuntimeError('Links/reparse points forbidden in NX lab.')
    return path


def save(part, nx):
    status=part.Save(nx.BasePart.SaveComponents.TrueValue,nx.BasePart.CloseAfterSave.FalseValue)
    if status:status.Dispose()


def create(case, directory, session, nx, uf):
    from NXOpen import Matrix3x3,Point3d
    rotation=Matrix3x3();rotation.Xx=rotation.Yy=rotation.Zz=1.0
    components=[]
    sign=nx.UF.Modl.FeatureSigns.NULLSIGN
    units=nx.Part.Units.Inches if case['units']=='Inches' else nx.Part.Units.Millimeters
    flat_part=session.Parts.NewDisplay(str(guarded(directory/'reference.prt')),units) if case.get('flat') else None
    for index,item in enumerate(case['bodies']):
        filename=guarded(directory/(item['role']+'.prt'))
        part=flat_part if flat_part is not None else session.Parts.NewDisplay(str(filename),units)
        shape=item['shape'];origin=item['origin'];direction=case.get('geometry_axis',[0.0,0.0,1.0])
        if shape=='cylinder':
            feature=uf.Modl.CreateCyl1(sign,origin,str(item['height']),str(item['diameter']),direction)
        elif shape=='cone':
            feature=uf.Modl.CreateCone1(sign,origin,str(item['height']),
                [str(item['diameter']),str(item['top_diameter'])],direction)
        else:
            feature=uf.Modl.CreateBlock1(sign,origin,list(map(str,item['lengths'])))
        # One newly created primitive per component; no unsupported tag-manager
        # lookup is needed. In the flat counterexample, the latest body is new.
        body=list(part.Bodies)[-1]
        body.SetName(item['role']);body.Color=42 if item['role'] in ('cut','tip','insert') else 87
        save(part,nx)
        if flat_part is None:components.append(filename)
    top=guarded(directory/'reference.prt')
    assembly=flat_part if flat_part is not None else session.Parts.NewDisplay(str(top),units)
    for filename in components:
        component,status=assembly.ComponentAssembly.AddComponent(str(filename),'Entire Part',filename.stem,
                                                                Point3d(0,0,0),rotation,-1)
        if status:
            try:
                if status.NumberUnloadedParts:raise RuntimeError('Reference component not completely loaded.')
            finally:status.Dispose()
    # Independent observed marker coordinates after reopening; never use task
    # values as observed data. Exported STEP may omit these and require review.
    for name,coordinates in [('NXTEST_MOUNT',case['mount_point']),('NXTEST_AXIS',[0,0,1])]:
        point=assembly.Points.CreatePoint(Point3d(*coordinates));point.SetName(name)
    if 'insert_direction' in case:
        point=assembly.Points.CreatePoint(Point3d(*case['insert_direction']));point.SetName('NXTEST_INSERT')
    save(assembly,nx)
    step=session.DexManager.CreateStepCreator()
    try:
        step.InputFile=str(top);step.OutputFile=str(guarded(directory/'input.step'))
        step.SettingsFile=str(guarded(directory/'export.def'))
        step.ObjectTypes.Solids=True
        step.ExportAs=nx.StepCreator.ExportAsOption.Ap214
        step.FileSaveFlag=False;step.ProcessHoldFlag=True
        step.Commit()
    finally:step.Destroy()
    return {'reference':str(top),'step':str(directory/'input.step')}


def measure(filename, session, nx, uf):
    part,status=session.Parts.OpenBaseDisplay(str(guarded(filename)))
    if status:
        try:
            if status.NumberUnloadedParts:raise RuntimeError('Reopen has unloaded components.')
        finally:status.Dispose()
    parts=list(session.Parts)
    if not all(p.IsFullyLoaded for p in parts):raise RuntimeError('All part prototypes must be loaded.')
    bodies=[b for p in parts for b in p.Bodies]
    if not bodies or any(not b.IsSolidBody or b.IsConvergentBody for b in bodies):
        raise RuntimeError('Ordinary solid bodies required.')
    boxes=[list(uf.Modl.AskBoundingBox(body.Tag)) for body in bodies]
    volumes=[]
    for body in bodies:
        properties,_=uf.Modl.AskMassProps3d([body.Tag],1,1,4,1.0,1,[0.999]+[0.0]*10)
        volumes.append(properties[1]*1e9)  # UF unit code 4: m^3 -> mm^3.
    components=[];pending=[part.ComponentAssembly.RootComponent]
    while pending:
        parent=pending.pop()
        if parent is None:continue
        for child in parent.GetChildren():
            prototype=child.Prototype
            guarded(prototype.FullPath)
            origin,rotation=child.GetPosition()
            if any(abs(v)>1e-9 for v in (origin.X,origin.Y,origin.Z)) or any(
                abs(getattr(rotation,key)-value)>1e-9 for key,value in
                [('Xx',1),('Xy',0),('Xz',0),('Yx',0),('Yy',1),('Yz',0),('Zx',0),('Zy',0),('Zz',1)]):
                raise RuntimeError('Reference occurrence moved or rotated; prototype measurements are insufficient.')
            components.append(child);pending.append(child)
    points={point.Name:[point.Coordinates.X,point.Coordinates.Y,point.Coordinates.Z] for point in part.Points}
    observed={'units':str(part.PartUnits),'body_count':len(bodies),'component_count':len(components),
              'bbox':[min(b[i] for b in boxes) for i in range(3)]+[max(b[i] for b in boxes) for i in range(3,6)],
              'volumes':sorted(volumes),
              'body_geometry':[{'bbox':box,'volume':volume} for box,volume in zip(boxes,volumes)]}
    for name,key in [('NXTEST_MOUNT','mount_point'),('NXTEST_AXIS','axis'),('NXTEST_INSERT','insert_direction')]:
        if name in points:observed[key]=points[name]
    return observed


def main():
    # No NX imports or session access until the local task and journal are pinned.
    arguments=[value for value in sys.argv[1:] if value.lower().endswith('.json')]
    if len(arguments)!=1:raise RuntimeError('Exactly one local task JSON required.')
    task_file=guarded(arguments[0]);task=json.loads(task_file.read_text(encoding='utf-8'))
    if hashlib.sha256(guarded(__file__).read_bytes()).hexdigest()!=task['journal_sha256']:
        raise RuntimeError('NX lab journal changed.')
    import NXOpen as nx
    import NXOpen.UF
    session=nx.Session.GetSession();uf=nx.UF.UFSession.GetUFSession()
    result={'schema_version':1,'phase':task['phase'],'case':task['case']['id'],'status':'failed'}
    try:
        if any(True for _ in session.Parts):raise RuntimeError('Fresh empty NX process required.')
        base=session.GetEnvironmentVariableValue('UGII_BASE_DIR') or ''
        if base.replace('\\','/').rstrip('/').casefold()!='c:/siemens/nx'+task['nx_version']:
            raise RuntimeError('NX session version/root differs from pinned target.')
        if task['phase']=='preflight':
            capabilities={name:callable(getattr(owner,name,None)) for owner,name in [
                (uf.Modl,'CreateCyl1'),(uf.Modl,'CreateCone1'),(uf.Modl,'CreateBlock1'),
                (uf.Modl,'AskMassProps3d'),(uf.Modl,'AskBoundingBox'),
                (session.DexManager,'CreateStepCreator'),(session.DexManager,'CreateStep214Importer')]}
            if not all(capabilities.values()):raise RuntimeError('Required NX2406 APIs unavailable: '+str(capabilities))
            result['capabilities']=capabilities
            result['solids_step_assembly_license_checkout_verified']=False
        elif task['phase']=='create':result['generated']=create(task['case'],task_file.parent,session,nx,uf)
        elif task['phase']=='measure':result['observed']=measure(task['part'],session,nx,uf)
        else:raise RuntimeError('Unknown NX lab phase.')
        result['status']='passed'
    except Exception as error:
        result['error']=str(error)
    guarded(task_file.parent/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    if result['status']!='passed':raise RuntimeError(result.get('error','NX phase failed.'))


if __name__=='__main__':main()

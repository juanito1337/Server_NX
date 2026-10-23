"""Physical NX counterexamples derived without changing the independent oracle."""
import copy


def counterexamples(fixtures):
    source=next(case for case in fixtures['cases'] if case['id']=='endmill')
    result=[]
    for defect in ('units','orientation','missing_holder','flat','missing_component','displaced_cut'):
        generated=copy.deepcopy(source);generated['id']='endmill-'+defect
        if defect=='units':generated['units']='Inches'
        elif defect=='orientation':generated['geometry_axis']=[0,0,-1];generated['axis']=[0,0,-1]
        elif defect=='missing_holder':generated['bodies']=[body for body in generated['bodies'] if body['role']!='holder']
        elif defect=='flat':generated['flat']=True
        elif defect=='displaced_cut':generated['bodies'][0]['origin'][0]+=2
        result.append({'id':generated['id'],'defect':defect,'generated':generated,'oracle':copy.deepcopy(source)})
    return result

"""Independent numerical oracle; observed data must come from reopened NX parts."""
import math


def compare(case, observed, tolerances):
    errors=[]
    expected=case['expected']
    if observed.get('units')!=case['units']: errors.append('units')
    for key in ('body_count','component_count'):
        if observed.get(key)!=expected[key]: errors.append(key)
    def numbers(key, reference, absolute=None, relative=None):
        values=observed.get(key)
        if not isinstance(values,list) or len(values)!=len(reference):
            errors.append(key);return
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
            errors.append(key);return
        if any(abs(a-b)>(absolute if absolute is not None else abs(b)*relative) for a,b in zip(values,reference)):
            errors.append(key)
    numbers('bbox',expected['bbox'],absolute=tolerances['length_mm'])
    numbers('volumes',sorted(expected['volumes']),relative=tolerances['relative_volume'])
    # Global extents and total volumes can remain unchanged when an individual
    # cutter/shank/insert is displaced. Bind each volume to its measured spatial
    # extents, independent of body names and NX enumeration/STEP import order.
    remaining=observed.get('body_geometry')
    if not isinstance(remaining,list) or len(remaining)!=len(expected['body_geometry']):
        errors.append('body_geometry')
    else:
        remaining=list(remaining)
        def matches(actual,reference):
            if not isinstance(actual,dict):return False
            box=actual.get('bbox');volume=actual.get('volume')
            return (isinstance(box,list) and len(box)==6 and
                all(type(v) in (int,float) and math.isfinite(v) for v in [*box,volume]) and
                all(abs(a-b)<=tolerances['length_mm'] for a,b in zip(box,reference['bbox'])) and
                abs(volume-reference['volume'])<=reference['volume']*tolerances['relative_volume'])
        for reference in expected['body_geometry']:
            index=next((i for i,item in enumerate(remaining) if matches(item,reference)),None)
            if index is None:
                errors.append('body_geometry');break
            remaining.pop(index)
    numbers('mount_point',case['mount_point'],absolute=tolerances['length_mm'])
    for key in ('axis','insert_direction'):
        if key not in case:continue
        direction=observed.get(key)
        if not isinstance(direction,list) or len(direction)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) for v in direction):
            errors.append(key);continue
        length=math.sqrt(sum(v*v for v in direction))
        cosine=sum(a*b for a,b in zip(direction,case[key]))/length if length else -1
        if math.degrees(math.acos(max(-1,min(1,cosine))))>tolerances['angle_deg']: errors.append(key)
    return errors

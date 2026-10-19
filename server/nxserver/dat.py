"""Conservative NX tool database reader/writer; never changes standard records."""
import hashlib

from .safety import DomainError,offline_path


def read_database(path,expected_sha256=None):
    path=offline_path(path)
    try:
        raw = path.read_bytes()
        if expected_sha256 is not None and hashlib.sha256(raw).hexdigest()!=expected_sha256:
            raise DomainError('Tool database differs from the managed manifest; import a new stand.',409)
        encoding = 'utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig'
        text = raw.decode(encoding, errors='strict')
    except (UnicodeError, OSError) as error:
        raise DomainError('Tool database must be readable UTF-8/UTF-16.') from error
    logical = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('#CLASS ') or line == '#END_DATA' or line.startswith(('FORMAT ', 'DATA ')):
            logical.append(line)
        elif line.startswith('#'):
            continue
        elif logical and logical[-1].startswith(('FORMAT ', 'DATA ')):
            logical[-1] += ' ' + line
        else:
            raise DomainError('Unrecognised NX database continuation.')
    schemas, records, keys = {}, [], set()
    current = None
    for line in logical:
        if line.startswith('#CLASS '):
            current = line[7:].strip()
        elif line.startswith('FORMAT '):
            fields = line[7:].split()
            if not current or 'LIBRF' not in fields or len(fields) != len(set(fields)):
                raise DomainError('Invalid NX class schema.')
            schemas[current] = fields
        elif line.startswith('DATA '):
            if current not in schemas:
                raise DomainError('DATA without class/format.')
            values = [value.strip() for value in line[5:].split('|')]
            if values and not values[0]:
                values.pop(0)
            while len(values) > len(schemas[current]) and not values[-1]:
                values.pop()
            if len(values) != len(schemas[current]):
                raise DomainError('NX data record does not match its schema.')
            record = dict(zip(schemas[current], values))
            if not record['LIBRF']:
                raise DomainError('Empty NX library reference.')
            key = (current, record['LIBRF'])
            # Existing exact duplicates are retained, not silently discarded.
            keys.add(key)
            records.append({'class_name': current, 'values': record})
    if not schemas:
        raise DomainError('No NX tool classes found.')
    return text, schemas, records, keys


def append_tools(path, tools):
    text, schemas, records, keys = read_database(path)
    by_class = {}
    for tool in tools:
        name, values = tool['class_name'], tool['values']
        if name not in schemas or set(values) != set(schemas[name]):
            raise DomainError('Provide all and only fields of the selected NX class.')
        if any(not isinstance(v, str) or any(c in v for c in '|\r\n\x00') for v in values.values()):
            raise DomainError('NX values must be strings without separators or newlines.')
        key = (name, values['LIBRF'])
        if not key[1] or key in keys:
            raise DomainError('NX library reference is empty or already exists; no automatic overwrite.')
        keys.add(key)
        by_class.setdefault(name, []).append(values)
    output, current, inserted = [], None, 0
    for line in text.splitlines():
        if line.strip().startswith('#CLASS '):
            current = line.strip()[7:].strip()
        if line.strip() == '#END_DATA' and current in by_class:
            for values in by_class.pop(current):
                output.append('DATA | ' + ' | '.join(values[field] for field in schemas[current]))
                inserted += 1
        output.append(line)
    if by_class or inserted != len(tools):
        raise DomainError('Class has no unique insertion boundary (#END_DATA).')
    offline_path(path).write_text('\n'.join(output) + '\n', encoding='utf-8-sig')
    if len(read_database(path)[2]) != len(records) + inserted:
        raise DomainError('NX post-export validation failed.')

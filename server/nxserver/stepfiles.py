"""Conservative STEP *text* preflight. This is not a B-rep/CAM validator.

Also shipped unchanged in isolated NX job packages; requires only stdlib.
"""
import hashlib
import re

try:
    from .safety import DomainError
except ImportError:  # Standalone NX journal package.
    from nx_safety import DomainError


TOOL_TYPES = {
    'turning_standard': 'Turning - Standard Turning Tool',
    'turning_grooving': 'Turning - Grooving Tool',
    'milling_shoulder': 'Milling - Indexable Shoulder Mill',
    'milling_face': 'Milling - Indexable Face Mill',
    'milling_radius': 'Milling - Solid Endmill (Radius)',
    'milling_square': 'Milling - Solid Endmill (Square)',
    'milling_chamfer': 'Milling - Solid Endmill (Chamfer)',
    'milling_ball': 'Milling - Solid Endmill (Ball Nose)',
    'drilling_solid': 'Drilling - Solid Drill',
    'drilling_indexable': 'Drilling - Indexable Drill',
}
PROTOCOLS = {
    '203': (('CONFIG_CONTROL_DESIGN', 'AP203_CONFIGURATION_CONTROLLED_3D_DESIGN_OF_MECHANICAL_PARTS_AND_ASSEMBLIES_MIM_LF'),
            'STEP203UG/step203ug.def'),
    '214': (('AUTOMOTIVE_DESIGN',), 'STEP214UG/step214ug.def'),
    '242': (('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING', 'AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF'),
            'TRANSLATORS/step242/step242ug.def'),
}


def preflight(content):
    if not content or len(content) > 20 * 1024 * 1024 or b'\0' in content:
        raise DomainError('STEP must be a text file of 1 byte to 20 MB, without NUL bytes.')
    text = content.decode('latin-1')
    # Ignore comments and quoted strings for structural/security checks. Names
    # inside FILE_NAME are metadata, not a reason to dereference their paths.
    lexical = re.sub(r"'(?:[^']|'')*'|/\*.*?\*/", lambda m: ' ' * len(m.group()), text, flags=re.S)
    if not re.match(r'^\s*ISO-10303-21\s*;', lexical, re.I) or not re.search(
            r'END-ISO-10303-21\s*;\s*$', lexical, re.I):
        raise DomainError('Not a complete ISO-10303-21 STEP text file.')
    header = re.search(r'\bHEADER\s*;(.*?)\bENDSEC\s*;', lexical, re.I | re.S)
    if not header or len(header.group(1)) > 1024 * 1024:
        raise DomainError('Missing or oversized STEP header.')
    # Remove comments without removing strings before reading FILE_SCHEMA.
    declarations = list(re.finditer(r'\bFILE_SCHEMA\s*\(\s*\((.*?)\)\s*\)\s*;', header.group(1), re.I | re.S))
    if len(declarations) != 1:
        raise DomainError('Exactly one FILE_SCHEMA declaration is required.')
    schema_start = header.start(1) + declarations[0].start(1)
    schema_end = header.start(1) + declarations[0].end(1)
    schema_text = text[schema_start:schema_end]
    schema_text = re.sub(r"'(?:[^']|'')*'|/\*.*?\*/",
                         lambda m: ' ' if m.group().startswith('/*') else m.group(), schema_text, flags=re.S)
    schemas = re.findall(r"'([^']+)'", schema_text)
    matches = {key for key, (aliases, _) in PROTOCOLS.items()
               if any(re.fullmatch(re.escape(alias) + r'(?:\s*\{[^{}]*\})?', s.strip(), re.I)
                      for alias in aliases for s in schemas)}
    if len(schemas) != 1 or len(matches) != 1:
        raise DomainError('Only unambiguous installed AP203/AP214/AP242 schemas are supported.')
    if len(re.findall(r'\bDATA\s*;', lexical, re.I)) != 1:
        raise DomainError('Exactly one embedded DATA section is required by this pilot.')
    # Edition-3 references and externally linked geometry are intentionally not
    # supported. Never resolve a STEP URL/file reference on the server or in NX.
    external = re.findall(r'\b(REFERENCE|ANCHOR)\s*;|\b(EXTERNAL_SOURCE|EXTERNALLY_DEFINED_REPRESENTATION|'
                          r'DOCUMENT_FILE|EXTERNAL_FILE)\s*\(', lexical, re.I)
    if external:
        raise DomainError('Externally referenced STEP files are not supported; supply a self-contained model.')
    # A translator can derive component filenames from product strings. Reject
    # path-like DATA strings up front, not merely after a possible file write.
    data_match = re.search(r'\bDATA\s*;(.*?)\bENDSEC\s*;', lexical, re.I | re.S)
    if not data_match:
        raise DomainError('Missing complete STEP DATA section.')
    data_text = text[data_match.start(1):data_match.end(1)]
    for token in re.finditer(r"'(?:[^']|'')*'|/\*.*?\*/", data_text, re.S):
        if token.group().startswith('/*'):
            continue
        value = token.group()[1:-1]
        if '/' in value or '\\' in value or ':' in value or value.strip() in {'.', '..'}:
            raise DomainError('Path-like DATA strings are not supported by the isolated STEP pilot.')
        if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value.strip()):
            raise DomainError('Reserved Windows device names in STEP DATA are not supported by this pilot.')
    entities = {name: len(re.findall(r'\b' + name + r'\s*\(', lexical, re.I)) for name in (
        'MANIFOLD_SOLID_BREP', 'BREP_WITH_VOIDS', 'FACETED_BREP',
        'SHELL_BASED_SURFACE_MODEL', 'NEXT_ASSEMBLY_USAGE_OCCURRENCE')}
    return {'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content),
            'protocol': next(iter(matches)), 'schemas': schemas, 'entity_hints': entities,
            'geometry_validated': False, 'units_verified': False,
            'warnings': ['Entity counts are text hints, not NX body counts or cutting/non-cutting classifications.',
                         'STEP import is not a CAM tool definition; mounting, body roles, dimensions and collision model need review.']}

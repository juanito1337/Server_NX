"""Bounded, read-only NX diagnostic parsing; logs never automatically approve CAM data."""
import hashlib
import re

from .safety import DomainError

MAX_LOG_BYTES = 20 * 1024 * 1024
PARSER_VERSION = 2
MAX_FINDINGS = 200
VERSION_HEADER = re.compile(r'^\s*NX_VERSION\s*[:=]\s*(.*?)\s*$', re.I)
VERSION_VALUE = re.compile(r'\d{4}(?:\.\d{1,10}){0,3}')
LOAD_ERRORS = re.compile(
    r'Uid in component.*do not match|Failed to read in part|Failed to load and update part|'
    r'Adding load status.*(?:641032|641044|640001)', re.I)


def decode_log(raw):
    if not raw or len(raw) > MAX_LOG_BYTES:
        raise DomainError('NX system log must contain 1 byte to 20 MB.')
    encoding = 'utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig'
    try:
        text = raw.decode(encoding)
    except UnicodeError:
        if encoding == 'utf-16':
            raise DomainError('NX system log has invalid UTF-16 text.')
        try:
            text = raw.decode('cp1252')
        except UnicodeError as error:
            raise DomainError('NX system log is not readable text.') from error
    # ASCII GS is present in the actual local Siemens syslog. Preserve its raw
    # bytes; only the diagnostic view treats it as a field separator below.
    if '\x00' in text or any(ord(c) < 32 and c not in '\t\r\n\f\x1d' for c in text):
        raise DomainError('NX system log contains binary/control data.')
    return text


def scan_bytes(raw, alias, expected_folder=None, expected_nx_version=None):
    text = decode_log(raw)
    if expected_nx_version is not None and not re.fullmatch(r'\d{4}', expected_nx_version):
        raise DomainError('Expected NX version must be a four-digit release family.')
    lines = text.replace('\x1d', ' ').splitlines()
    errors, evidence, versions, families, invalid, error_count, header_count = [], [], set(), set(), False, 0, 0
    for number, line in enumerate(lines, 1):
        if LOAD_ERRORS.search(line):
            error_count += 1
            if len(errors) < MAX_FINDINGS: errors.append({'line': number, 'message': line.strip()[:1500]})
        match = VERSION_HEADER.fullmatch(line)
        if not match: continue
        header_count += 1
        value = match[1]
        if not VERSION_VALUE.fullmatch(value):
            invalid = True
            if len(evidence) < MAX_FINDINGS: evidence.append({'line': number, 'valid': False})
            continue
        family = value.split('.')[0]
        families.add(family)
        if len(versions) < MAX_FINDINGS or value in versions: versions.add(value)
        else: invalid = True  # A bounded report must not silently omit conflicting headers.
        if len(evidence) < MAX_FINDINGS:
            evidence.append({'line': number, 'valid': True, 'version': value, 'family': family})
    status = ('invalid' if invalid else 'ambiguous' if len(families) > 1 else 'not_requested' if expected_nx_version is None
              else 'missing' if not families else 'match' if families == {expected_nx_version} else 'mismatch')
    version = {'status': status, 'expected': expected_nx_version, 'families': sorted(families),
               'versions': sorted(versions), 'header_count': header_count, 'evidence': evidence,
               'evidence_truncated': header_count > len(evidence), 'supported_marker': 'NX_VERSION'}
    expected = str(expected_folder).replace('\\', '/').rstrip('/').casefold() if expected_folder is not None else None
    contains_reference = expected is None or bool(re.search(
        r'(?<![\w./:])' + re.escape(expected) + r'''(?=$|[/\s'"<>;),])''', text.replace('\\', '/').casefold()))
    cam = bool(re.search(r'\bUG_APP_MANUFACTURING\b', text, re.I))
    warnings = (['ASCII GS field separators are displayed as spaces; original bytes and SHA remain unchanged.']
                if '\x1d' in text else [])
    return {'parser_version': PARSER_VERSION, 'source': alias, 'sha256': hashlib.sha256(raw).hexdigest(),
            'blocking_errors': errors, 'blocking_error_count': error_count, 'blocking_errors_truncated': error_count > len(errors),
            'contains_cam_activity': cam, 'version_check': version, 'warnings': warnings,
            'contains_release_reference': contains_reference,
            'expected_folder': str(expected_folder) if expected_folder is not None else None,
            'can_review': expected_folder is not None and status == 'match' and not error_count and cam and contains_reference}

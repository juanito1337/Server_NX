"""Startup settings and file-backed tokens (no secrets in images or logs)."""
import json
import os
import secrets

from .safety import DomainError, offline_path


def token_from_environment(name, environment):
    inline, filename = environment.get(name), environment.get(name + '_FILE')
    if inline and filename:
        raise DomainError(name + ' and ' + name + '_FILE may not both be configured.')
    if filename:
        path = offline_path(filename)
        if path.stat().st_size > 4096:
            raise DomainError(name + '_FILE exceeds the allowed size.')
        inline = path.read_text(encoding='utf-8-sig').strip()
    if inline is not None and (len(inline) < 32 or len(inline) > 4096 or any(c.isspace() for c in inline)):
        raise DomainError(name + ' must contain 32-4096 characters without whitespace.')
    return inline


def startup(settings_file, host, environment=None):
    environment = os.environ if environment is None else environment
    settings = json.loads(offline_path(settings_file).read_text(encoding='utf-8-sig'))
    admin = token_from_environment('NX_ADMIN_TOKEN', environment)
    erp = token_from_environment('NX_ERP_TOKEN', environment)
    if not admin and host != '127.0.0.1':
        raise DomainError('A configured admin token or token file is required for a non-loopback/container listener.')
    generated = not admin
    admin = admin or secrets.token_urlsafe(32)
    if erp == admin:
        raise DomainError('Admin and ERP tokens must be distinct.')
    return settings, admin, erp, generated

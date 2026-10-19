"""Administrator-scoped server folder selection, never a client file upload."""
import os
import re
from pathlib import Path

from .safety import DomainError, disjoint, offline_path


def scoped_path(root, value=None):
    """Check syntax and containment BEFORE looking up a user-supplied path."""
    if not value:
        return offline_path(root)
    text = str(value).replace('\\', '/')
    if (any(ord(c) < 32 for c in text) or text.startswith('//') or
            re.match(r'(?i)^w:', text) or text.lower().startswith('/mnt/w')):
        raise DomainError('Protected/network paths are not allowed.')
    path = Path(value)
    if not path.is_absolute():
        raise DomainError('Please specify an absolute server folder path.')
    # Portable NX paths: no traversal, ADS, Windows aliases or reserved names.
    components = text.split('/')[1:]
    if any(part in ('.', '..') or part.endswith(('.', ' ')) or
           any(c in part for c in ':*?"<>|') or
           re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part)
           for part in components if part):
        raise DomainError('Invalid or non-portable folder path.')
    try:
        path.relative_to(root)
    except ValueError:
        raise DomainError('Folder is outside the administrator-approved root.') from None
    return offline_path(path)


class FolderAccess:
    def __init__(self, store, source_roots=None, target_roots=None):
        self.store = store
        self.sources = dict(store.sources)
        for key, value in (source_roots or {}).items():
            if key in self.sources:
                raise DomainError('Source root aliases must be unique.')
            self.sources[key] = offline_path(value)
        self.targets = {'managed': store.root / 'r'}
        for key, value in (target_roots or {}).items():
            if key == 'managed':
                raise DomainError('The managed target root may not be overridden.')
            self.targets[key] = offline_path(value)
        for roots in (self.sources, self.targets):
            for key in roots:
                if not re.fullmatch(r'[\w-]{1,100}', key):
                    raise DomainError('Invalid folder root alias.')

    def _releases(self,exclude_job=None):
        with self.store.db() as db:
            paths=[offline_path(row['folder']) for row in db.execute('SELECT folder FROM releases')]
            for row in db.execute('SELECT id,payload FROM migration_jobs'):
                import json
                job=json.loads(row['payload'])
                # Copied recovery history is not a live reservation pointing
                # back at the original host. Partial files have private copies.
                if row['id'] in getattr(self.store,'recovered_job_ids',set()):continue
                if job['mode']=='build' and row['id']!=exclude_job:
                    paths.append(offline_path(job['request']['target_path']))
            return paths

    def _tool_roots(self):
        tool=self.store.extractor
        return [offline_path(tool['path']).parent] if tool else []

    def source(self, key, value=None):
        if key not in self.sources:
            raise DomainError('Unknown administrator-configured source alias.')
        path = scoped_path(self.sources[key], value)
        disjoint(path, self.store.root)
        for tool in self._tool_roots():disjoint(path,tool)
        for release in self._releases():
            disjoint(path, release)
        if not path.is_dir():
            raise DomainError('Source folder does not exist.')
        return path

    def _target_safe(self, path, browsing=False, releases=None):
        managed = self.store.root / 'r'
        if path != managed and managed not in path.parents:
            if browsing:
                if path == self.store.root or self.store.root in path.parents:
                    raise DomainError('Management metadata is not a target folder.')
            else:
                disjoint(path, self.store.root)
        for source in (list(self.store.sources.values()) + list(self.store.standards.values()) + list(self.store.sidts.values()) +
                       list(self.store.logs.values()) + [offline_path(item['path']) for item in self.store.installers.values()] +
                       self._tool_roots() + (self._releases() if releases is None else releases)):
            if browsing:
                if path == source or source in path.parents:
                    raise DomainError('This folder is an input or an existing managed release.')
            else:
                disjoint(path, source)

    def destination(self, key, value, source,exclude_job=None):
        if key not in self.targets:
            raise DomainError('Unknown administrator-configured target alias.')
        if not value:
            raise DomainError('Please specify a new target folder.')
        path = scoped_path(self.targets[key], value)
        if path == self.targets[key]:
            raise DomainError('Choose a new subfolder, not the target root itself.')
        if path.exists():
            raise DomainError('Target folder already exists; nothing will be overwritten.', 409)
        self._target_safe(path,releases=self._releases(exclude_job))
        disjoint(path, source)
        return path

    def browse(self, purpose, key, value=None):
        roots = self.sources if purpose == 'source' else self.targets
        if key not in roots:
            raise DomainError('Unknown administrator-configured folder root.')
        root, path = roots[key], scoped_path(roots[key], value)
        releases = self._releases()
        # Never browse management metadata, inputs as targets, or old releases.
        if purpose == 'source':
            if path == self.store.root or self.store.root in path.parents:
                raise DomainError('Management storage cannot be browsed as a source.')
            for release in releases:
                if path == release or release in path.parents:
                    raise DomainError('Existing managed releases cannot be browsed as a source.')
            for tool in self._tool_roots():
                if path==tool or tool in path.parents:raise DomainError('Archive-reader tools are not NX source folders.')
        else:
            self._target_safe(path, browsing=True, releases=releases)
        if not path.is_dir():
            # The default managed root need not exist before the first import.
            if purpose == 'target' and path == root and not path.exists():
                return {'root': str(root), 'path': str(path), 'parent': None,
                        'children': [], 'can_select': True, 'reason': '', 'truncated': False}
            raise DomainError('Folder does not exist.')
        children, truncated = [], False
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    try:
                        child = scoped_path(root, entry.path)
                        if not child.is_dir():
                            continue
                        if purpose == 'source':
                            if child == self.store.root or self.store.root in child.parents:
                                continue
                            if any(child == r or r in child.parents for r in releases):
                                continue
                            if any(child==tool or tool in child.parents for tool in self._tool_roots()):continue
                        else:
                            self._target_safe(child, browsing=True, releases=releases)
                        children.append({'name': entry.name, 'path': str(child)})
                        if len(children) == 500:
                            truncated = True
                            break
                    except (DomainError, OSError):
                        continue
        except OSError as error:
            raise DomainError('Folder cannot be enumerated.') from error
        reason = ''
        if purpose == 'source':
            try:
                self.source(key, str(path))
            except DomainError as error:
                reason = str(error)
        return {'root': str(root), 'path': str(path), 'parent': str(path.parent) if path != root else None,
                'children': sorted(children, key=lambda entry: entry['name'].casefold()),
                'can_select': not reason, 'reason': reason, 'truncated': truncated}

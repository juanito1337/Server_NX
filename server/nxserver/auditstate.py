"""Hash-bind committed management rows. No retroactive rewriting of old audits."""
import hashlib
import json

# These identifiers are code-owned, never SQL/file identifiers from a request.
KEYS={'resources':('id',),'resource_versions':('id','revision'),'releases':('id',),
      'active':('singleton',),'assets':('id',),'geometry_jobs':('id',),'migration_jobs':('id',),
      'sidt_inputs':('version',),'test_launchers':('release_id',),'syslogs':('id',),'geometry_results':('id',),
      'write_requests':('actor','request_key'),'migration_requests':('actor','request_key'),
      'asset_requests':('actor','request_key'),'geometry_requests':('actor','request_key'),
      'backup_requests':('actor','request_key'),'log_requests':('actor','request_key'),'release_requests':('actor','request_key')}
ERP_KEYS={'erp_model':('singleton',),'erp_articles':('id',),'erp_article_versions':('id','revision'),
          'erp_variant_links':('resource_id',),'erp_variant_versions':('resource_id','revision'),'erp_requests':('actor','request_key')}
KEYS.update(ERP_KEYS)


def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def fingerprint(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def key_for(table,row):return tuple(row[column] for column in KEYS[table])


def row_binding(table,row):
    value=dict(row)
    return {'table':table,'key':list(key_for(table,value)),'columns':sorted(value),'sha256':fingerprint(value)}


def committed_bindings(db,action,payload):
    if action=='management.restored':
        # New accepted baseline after an explicit isolated recovery. Historical
        # audit payloads remain byte-identical; this is not retroactive proof.
        present={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return [row_binding(table,row) for table in KEYS if table in present for row in db.execute('SELECT * FROM '+table)]
    wanted=[]
    if action=='erp.model.migrated':
        wanted.extend((table,key_for(table,row)) for table in ERP_KEYS for row in db.execute('SELECT * FROM '+table))
    elif action=='erp.article.upserted':
        wanted=[('erp_articles',(payload['entity_id'],)),('erp_article_versions',(payload['entity_id'],payload['revision']))]
    elif action=='erp.variant.assigned':
        wanted=[('erp_variant_links',(payload['entity_id'],)),('erp_variant_versions',(payload['entity_id'],payload['revision']))]
    if action in ('resource.saved','erp.upserted'):
        wanted=[('resources',(payload['id'],)),('resource_versions',(payload['id'],payload['revision']))]
    elif action=='catalog.imported':
        for item in payload['objects']:
            wanted.extend([('resources',(item['id'],)),('resource_versions',(item['id'],1))])
    elif action=='asset.created':wanted=[('assets',(payload['id'],))]
    elif action=='syslog.uploaded':wanted=[('syslogs',(payload['id'],))]
    elif action.startswith('geometry.result.') and 'id' in payload:wanted=[('geometry_results',(payload['id'],))]
    elif action.startswith('geometry.') and 'id' in payload:wanted=[('geometry_jobs',(payload['id'],))]
    elif action in ('migration.job.created','migration.job.failed','migration.job.completed','migration.job.interrupted',
                     'installer.extraction.created','installer.extraction.completed'):
        wanted=[('migration_jobs',(payload['id'],))]
    elif action=='installer.extraction.selected':wanted=[('sidt_inputs',(payload['version'],))]
    elif action=='migration.release.created':
        wanted=[('releases',(payload['release_id'],)),('test_launchers',(payload['release_id'],))]
    elif action=='release.activated':wanted=[('active',(1,))]
    elif action=='release.test_launcher.created':wanted=[('test_launchers',(payload['release_id'],))]
    elif action in ('release.test_launcher.refreshed','release.test_launcher.ui_profile'):wanted=[('test_launchers',(payload['current']['release_id'],))]
    elif action in ('release.started','release.imported','release.failed','release.validated','release.approved'):
        wanted=[('releases',(payload['id'],))]
    receipt=payload.get('_write_request')
    if receipt:wanted.append(('write_requests',(receipt['actor'],receipt['key'])))
    migration_receipt=payload.get('_migration_request')
    if migration_receipt:wanted.append(('migration_requests',(migration_receipt['actor'],migration_receipt['key'])))
    if payload.get('_operation_job'):wanted.append(('migration_jobs',(payload['_operation_job'],)))
    asset_receipt=payload.get('_asset_request')
    if asset_receipt:wanted.append(('asset_requests',(asset_receipt['actor'],asset_receipt['key'])))
    geometry_receipt=payload.get('_geometry_request')
    if geometry_receipt:wanted.append(('geometry_requests',(geometry_receipt['actor'],geometry_receipt['key'])))
    backup_receipt=payload.get('_backup_request')
    if backup_receipt:wanted.append(('backup_requests',(backup_receipt['actor'],backup_receipt['key'])))
    log_receipt=payload.get('_log_request')
    if log_receipt:wanted.append(('log_requests',(log_receipt['actor'],log_receipt['key'])))
    release_receipt=payload.get('_release_request')
    if release_receipt:wanted.append(('release_requests',(release_receipt['actor'],release_receipt['key'])))
    erp_receipt=payload.get('_erp_request')
    if erp_receipt and action!='erp.model.migrated':wanted.append(('erp_requests',(erp_receipt['actor'],erp_receipt['key'])))
    result=[]
    for table,key in wanted:
        row=db.execute('SELECT * FROM '+table+' WHERE '+' AND '.join(column+'=?' for column in KEYS[table]),key).fetchone()
        if row:result.append(row_binding(table,row))
    return result

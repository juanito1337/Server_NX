'use strict';
/* Read-only export planning, explicit new-column values and revision-bound creation. */
globalThis.NXExports = (() => {
  const el = id => document.getElementById(id);
  const labels = {created:'Neu',updated:'Aktualisiert',unchanged:'Unverändert',retired:'Stillgelegt',deferred:'Entwurf zurückgestellt',catalog_only:'Nur Stammdaten'};
  let parent = null, preview = null, generation = 0, selectionVersion=0, busy = false,jobsBlocked=true,catalogSignature=null;
  let paxReport=null,paxPage=0,view=null;
  const signature=list=>list.map(item=>item.id+':'+item.revision).sort().join('|');
  function controls(){el('export-preview').disabled=busy;el('export-create').disabled=busy||jobsBlocked||!preview?.can_export;}
  function defaults() {
    const value = JSON.parse(el('export-defaults').value);
    if(!value || typeof value !== 'object' || Array.isArray(value))throw new Error('Spaltenwerte müssen ein JSON-Objekt sein.');
    return value;
  }
  function clearView(){view=null;['export-actions','export-conflicts','export-column-fields','export-reference-reviews'].forEach(id=>el(id).replaceChildren());}
  function invalidate(retire=false) {
    if(retire===true)clearView();
    selectionVersion++;
    NXRequests.invalidate('exports.preview');
    preview = null;el('export-create').disabled = true;
    resetPax();
    globalThis.NXExportData?.reset();
    globalThis.NXExportFiles?.reset();
    el('export-state').textContent = 'Werte oder Daten geändert. Exportvorschau neu prüfen.';
  }
  function reset() {
    generation++;selectionVersion++;NXRequests.invalidate('exports.');busy=false;parent=null;preview=null;catalogSignature=signature(resources);el('export-box').hidden=true;
    clearView();
    el('export-defaults').value='{}';el('export-name').value='';el('export-parent').textContent='';
    el('export-preview').disabled=false;el('export-create').disabled=true;el('export-state').textContent='';
    resetPax();
    globalThis.NXExportData?.reset();
    globalThis.NXExportFiles?.reset();
  }
  function resetPax(){paxReport=null;paxPage=0;el('export-pax-box').open=false;el('export-pax-items').replaceChildren();el('export-pax-state').textContent='';el('export-pax-page').textContent='';el('export-pax-prev').disabled=el('export-pax-next').disabled=true;}
  function validPax(value){const hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);return NXData.record(value)&&value.contract_version===1&&value.nx_version===parent.nx_version&&value.technical_only===true&&value.nx_execution_verified===false&&value.automatic_approval===false&&value.path_rewrites===false&&value.binary_dependencies_verified===false&&value.ui_reachability_verified===false&&typeof value.complete==='boolean'&&value.scope==='managed_pax_declared_file_references'&&Number.isSafeInteger(value.palettes)&&value.palettes>=0&&Array.isArray(value.references)&&Array.isArray(value.errors)&&value.references.every(item=>NXData.record(item)&&typeof item.pax_path==='string'&&typeof item.entry_id==='string'&&typeof item.field==='string'&&typeof item.value==='string'&&(item.status==='conflict'?typeof item.reason==='string':item.status==='present'&&['release','installation'].includes(item.scope)&&typeof item.target==='string'&&Number.isSafeInteger(item.size)&&item.size>=0&&hex(item.sha256)));}
  function renderPax(){if(!paxReport)return;const offset=paxPage*50;el('export-pax-items').replaceChildren(...paxReport.references.slice(offset,offset+50).map(item=>{const row=document.createElement('tr');cell(row,item.pax_path+' / '+item.entry_id);cell(row,item.field+' · '+item.value);cell(row,item.target?(item.scope==='installation'?'Lokale NX-Installation: ':'Neuer Export: ')+item.target:'Nicht auflösbar');cell(row,item.status==='present'?'Datei geplant/gefunden · '+item.size+' Bytes · SHA '+item.sha256:item.reason);return row;}));el('export-pax-state').textContent=paxReport.palettes+' ausdrücklich exportierte PAX · '+paxReport.references.length+' deklarierte Dateiverweise · '+paxReport.errors.length+' Konflikte. '+(paxReport.complete?'Deklarierte Verweise erfasst.':'XML-/Leserbefund nicht vollständig; Export gesperrt.')+' Kein NX-/Geometrie-/UI-Nachweis.';el('export-pax-page').textContent=(paxReport.references.length?offset+1:0)+'–'+Math.min(offset+50,paxReport.references.length)+' von '+paxReport.references.length;el('export-pax-prev').disabled=paxPage===0;el('export-pax-next').disabled=offset+50>=paxReport.references.length;}
  function open(release) {
    reset();parent=release;el('export-box').hidden=false;
    el('export-parent').textContent=`Elternstand: ${release.name} · NX${release.nx_version} · ${release.host_folder||release.folder}`;
    el('export-name').value=release.name+' + Stammdaten';invalidate();showWorkspace('manage','export-title');
  }
  function render(report) {
    const owner={generation,parent,catalog:signature(report.catalog_revisions)};view=owner;
    // Decisions may invalidate readiness without retiring sibling input fields.
    const current=()=>view===owner&&owner.generation===generation&&parent===owner.parent&&signature(resources)===owner.catalog&&!busy&&workspace==='manage'&&Boolean(token)&&!el('export-box').hidden;
    const editButton=(label,id,node)=>button(label,()=>{if(!current()||!node.isConnected)return;const revision=report.catalog_revisions.find(item=>item.id===id)?.revision,resource=resources.find(item=>item.id===id&&item.revision===revision);if(resource)openEditor(resource);});
    paxReport=report.template_references;paxPage=0;renderPax();
    el('export-actions').replaceChildren();el('export-conflicts').replaceChildren();el('export-column-fields').replaceChildren();
    el('export-reference-reviews').replaceChildren();Object.entries(report.reference_reviews||{}).forEach(([id,review])=>{const item=document.createElement('li'),resource=resources.find(r=>r.id===id);item.append(document.createTextNode(`Upgrade-Nachweis für ${resource?.name||id}: ${review.decisions.map(d=>`${d.field}: ${d.previous} → ${d.value}`).join(', ')}. Der Katalog wurde dadurch nicht geändert; Stammdaten nur ausdrücklich speichern. `));if(resource)item.append(editButton('Stammdaten prüfen / bearbeiten',id,item));el('export-reference-reviews').append(item);});
    report.actions.forEach(item=>{const row=document.createElement('tr');cell(row,item.name);cell(row,(item.library_id||'—')+' / '+(item.key||'—'));cell(row,labels[item.action]||item.action);const reason=item.migration_exclusion?'Beim Upgrade durch Zielstand ersetzt/ausgelassen. Automatischer Rückexport gesperrt.':item.action==='deferred'?'Entwurf nicht exportiert. Enthaltene Elterndaten bleiben unverändert.':item.action==='catalog_only'?(item.library_id?'Siemens-Standardobjekt bleibt in der NX-Datenbank.':'Reine Stammdaten; kein NX-Exportadapter zugeordnet.'):(item.reason||'');cell(row,reason);el('export-actions').append(row);});
    (report.file_packages||[]).forEach(item=>{const row=document.createElement('tr');cell(row,resources.find(r=>r.id===item.resource_id)?.name||item.resource_id);cell(row,'NX-Dateipaket');cell(row,labels[item.action]||(item.action==='exported'?'Dateien exportieren':item.action));cell(row,item.files+' Paketdateien im neuen Datenstand; Elternordner bleibt unverändert.');el('export-actions').append(row);});
    (report.file_deletions||[]).forEach(item=>{const row=document.createElement('tr');cell(row,item.path);cell(row,'NX-Dateipaket');cell(row,'Aus neuer Kopie entfernen');cell(row,'Alter SHA '+item.sha256+'. Elternordner und Dateianhang bleiben erhalten.');el('export-actions').append(row);});
    report.conflicts.forEach(item=>{const row=document.createElement('tr');cell(row,(item.library_id||item.library||item.pax_path||'—')+' / '+(item.key||item.entry_id||item.field||'—'));cell(row,item.reason);const target=cell(row,'');const resource=resources.find(r=>r.id===item.resource_id);if(resource)target.append(editButton('Bearbeiten',resource.id,row));el('export-conflicts').append(row);});
    report.new_columns.forEach(column=>{
      const box=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent=`${column.library_id} · ${column.block_id} · ${column.field}`;box.append(legend);
      const label=document.createElement('label'),input=document.createElement('input');label.textContent=`Expliziter Wert für ${column.resource_ids.length} Datensatz/Datensätze`;input.value=column.value??'';label.append(input);box.append(label);
      const confirmLabel=document.createElement('label'),check=document.createElement('input');confirmLabel.className='checklist';check.type='checkbox';check.checked=column.confirmed;confirmLabel.append(check,document.createTextNode('Diesen Wert bestätigen (auch ein Leerwert ist eine explizite Entscheidung)'));box.append(confirmLabel);
      const update=()=>{if(!current()||!box.isConnected)return;invalidate();const data=defaults(),values=NXData.branch(NXData.branch(data,column.library_id),column.block_id);if(check.checked)NXData.set(values,column.field,input.value);else NXData.remove(values,column.field);el('export-defaults').value=JSON.stringify(data,null,2);};input.oninput=action(update);check.onchange=action(update);
      el('export-column-fields').append(box);
    });
    el('export-state').textContent=report.can_export?`Vorschau exportierbar: ${report.actions.length} Stammdatenaktionen, ${report.assets.length} Anhänge. Noch keine NX-Freigabe.`:`${report.conflicts.length} Konflikt(e). Spaltenwerte bestätigen oder Stammdaten korrigieren und erneut prüfen.`;
  }
  async function run(operation) {
    if(busy)return;
    const current=generation;busy=true;el('export-preview').disabled=true;el('export-create').disabled=true;
    try { await operation(current); } catch(error) { if(current===generation&&!NXRequests.isStale(error)){invalidate();message(error.message,true);} }
    finally { if(current===generation){busy=false;controls();} }
  }
  el('export-preview').onclick=()=>run(async current=>{
    clearView();
    if(!parent)throw new Error('Zuerst einen Elternstand wählen.');
    const release=parent.id, body={defaults:defaults()}, snapshot=JSON.stringify(body);
    const request=NXRequests.begin('exports.preview',()=>current===generation&&parent?.id===release&&snapshot===JSON.stringify({defaults:defaults()}));
    preview=null;NXExportData.reset();NXExportFiles.reset();el('export-state').textContent='Prüfe Elternordner, Stammdaten, Schemata und Referenzen …';
    const report=await request.wait(api('releases/'+release+'/derive-preview','POST',body));
    if(!validPax(report.template_references)||report.can_export&&(report.template_references.errors.length||!report.template_references.complete))throw new Error('Kein vollständiger gebundener PAX-Verweisvertrag.');
    if(!NXData.record(report)||report.native_preview_contract!==1||report.release_id!==release||report.nx_version!==parent.nx_version||report.automatic_approval!==false||typeof report.can_export!=='boolean'||!Array.isArray(report.conflicts)||report.can_export!==(report.conflicts.length===0)||!Array.isArray(report.actions)||!Array.isArray(report.assets)||!Array.isArray(report.outputs)||!Array.isArray(report.new_columns)||!Array.isArray(report.catalog_revisions)||typeof report.preview_hash!=='string'||!/^[a-f0-9]{64}$/.test(report.preview_hash)||typeof report.parent_manifest_sha256!=='string'||!/^[a-f0-9]{64}$/.test(report.parent_manifest_sha256))throw new Error('Kein vollständiger gebundener Exportvorschauvertrag.');
    preview=report;render(report);details(report);
    NXExportData.open(report);
    NXExportFiles.open(report);
  });
  el('export-create').onclick=()=>run(async current=>{
    if(!parent||!preview?.can_export)throw new Error('Zuerst eine exportierbare Vorschau prüfen.');
    if(jobsBlocked)throw new Error('Bestehenden Ordnerauftrag abwarten oder unbestätigte Annahme klären.');
    const name=el('export-name').value.trim();if(!name)throw new Error('Bezeichnung für den neuen Datenstand eingeben.');
    const body={name,defaults:defaults(),preview_hash:preview.preview_hash},release=parent.id;
    if(!confirm('Den geprüften Stammdatenexport als neuen Ordner erstellen? Der Elternordner bleibt unverändert.'))return;
    el('export-state').textContent='Export und Testpaket-Erstellung laufen …';
    const signature=JSON.stringify([release,body]),editor=editorGeneration,selection=selectionVersion;
    await NXReleaseJobs.start('derive',{...body,parent_release_id:release},{completed:()=>{
      if(current!==generation||selection!==selectionVersion)return false;
      try{if(signature!==JSON.stringify([parent?.id,{name:el('export-name').value.trim(),defaults:defaults(),preview_hash:body.preview_hash}]))return false;}catch{return false;}
      reset();return workspace==='manage'&&editor===editorGeneration;
    }});
    if(current===generation)invalidate();
  });
  el('export-defaults').oninput=invalidate;
  el('export-name').addEventListener('input',()=>{selectionVersion++;});
  el('export-close').onclick=reset;
  el('export-pax-prev').onclick=()=>{paxPage=Math.max(0,paxPage-1);renderPax();};el('export-pax-next').onclick=()=>{paxPage++;renderPax();};
  function syncCatalog(items) {
    const current=signature(items),changed=catalogSignature!==null&&current!==catalogSignature;catalogSignature=current;
    if(changed||preview&&current!==signature(preview.catalog_revisions))invalidate(true);
  }
  return {open,reset,invalidate,syncCatalog,context:()=>parent&&preview?.can_export?{release_id:parent.id,preview_hash:preview.preview_hash,defaults:defaults(),parent_manifest_sha256:preview.parent_manifest_sha256}:null,releaseJobControls:value=>{jobsBlocked=value;controls();}};
})();

/* Whole-file byte plans and private attachment metadata, never write operations. */
globalThis.NXExportFiles=(()=>{
  const el=id=>document.getElementById('export-files-'+id),hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value),nat=value=>Number.isSafeInteger(value)&&value>=0;
  const uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
  const labels={created:'Neu',updated:'Geändert',removed:'Aus neuer Kopie entfernt',unchanged:'Unverändert',draft:'Entwurf',retired:'Stillgelegt',migration_excluded:'Beim Upgrade ausgeschlossen',not_selected:'Nicht zur Kopie ausgewählt',catalog_only:'Nur Stammdaten'};
  const kinds={native_database:'Native DAT-Datei',graphics:'Bibliotheksgrafik',file_package:'Dateipaket',inherited:'Geerbte Datei'};
  let supported=false,root=null,index=null,owner=null,generation=0,pins=null,blocked=false;
  const context=()=>NXExports.context(),filters=()=>({view:el('view').value,change:el('view').value==='private'?'':el('change').value,query:el('query').value});
  const signature=()=>JSON.stringify([context(),filters()]);
  function controls(){const valid=supported&&!blocked&&root?.can_export&&context()?.preview_hash===root.preview_hash,busy=Boolean(owner);el('load').disabled=!valid||busy;el('prev').disabled=!valid||busy||!index||index.offset===0;el('next').disabled=!valid||busy||!index||index.offset+index.items.length>=index.filtered;el('change').disabled=el('view').value==='private';}
  function clear(){index=null;el('items').replaceChildren();el('page').textContent='';el('totals').textContent='';}
  function changed(){generation++;NXRequests.invalidate('exports.files.');owner=null;clear();el('state').textContent='Ansicht geändert; Dateiplan ausdrücklich neu laden.';controls();}
  function reset(){changed();root=null;pins=null;blocked=false;el('box').open=false;el('view').value='files';el('change').value='';el('query').value='';el('state').textContent='';controls();}
  function configure(cap){supported=cap.catalog_export?.file_plan_preview===1&&cap.catalog_export.private_attachments_metadata_only===true;reset();}
  function open(report){reset();root=report;el('state').textContent=report.can_export?'Exportvorschau gebunden. Dateiplan ausdrücklich laden; es wird nichts geschrieben.':'Zuerst Exportkonflikte lösen und Vorschau erneut prüfen.';controls();}
  const meta=value=>NXData.record(value)&&nat(value.size)&&hex(value.sha256);
  const path=value=>typeof value==='string'&&value.length>0&&value.length<=4096&&!/[\\:\x00-\x1f]/.test(value)&&value.split('/').every(part=>part&&part!=='.'&&part!=='..');
  function validOwner(value){const resource=resources.find(item=>item.id===value?.id);return NXData.record(value)&&uuid(value.id)&&resource&&value.name===resource.name&&value.kind===resource.kind&&value.revision===resource.revision&&root.catalog_revisions.some(item=>item.id===value.id&&item.revision===value.revision);}
  function validFile(item,body){
    if(!NXData.record(item)||!path(item.path)||!Object.hasOwn(kinds,item.kind)||!['created','updated','removed','unchanged'].includes(item.change)||(item.before!==null&&!meta(item.before))||(item.after!==null&&!meta(item.after))||!nat(item.resource_count)||typeof item.owners_complete!=='boolean'||!Array.isArray(item.resources)||item.resources.length!==Math.min(10,item.resource_count)||item.owners_complete!==(item.resource_count<=10)||item.resources.some(value=>!validOwner(value))||new Set(item.resources.map(value=>value.id)).size!==item.resources.length)return false;
    const change=item.before===null?'created':item.after===null?'removed':item.before.size===item.after.size&&item.before.sha256===item.after.sha256?'unchanged':'updated';
    if(item.before===null&&item.after===null||item.change!==change||body.change&&(body.change==='changed'?change==='unchanged':change!==body.change))return false;
    if(item.kind==='graphics'||item.kind==='file_package'){const planned=root.assets.find(value=>value.path===item.path&&value.id===item.asset_id);if(item.after!==null&&(!uuid(item.asset_id)||!planned||planned.size!==item.after.size||planned.sha256!==item.after.sha256))return false;if(item.after===null&&(item.asset_id!==null||!root.file_deletions.some(value=>value.path===item.path&&value.sha256===item.before.sha256)))return false;}
    else if(item.asset_id!==null)return false;
    if(item.kind==='native_database'&&(!item.after||!root.outputs.some(value=>value.path===item.path&&value.sha256===item.after.sha256)))return false;
    return true;
  }
  function validPrivate(item){const resource=resources.find(value=>value.id===item?.resource_id);return NXData.record(item)&&resource&&uuid(item.resource_id)&&uuid(item.asset_id)&&resource.revision===item.resource_revision&&resource.name===item.resource_name&&resource.asset_ids.includes(item.asset_id)&&root.catalog_revisions.some(value=>value.id===resource.id&&value.revision===resource.revision)&&typeof item.filename==='string'&&item.filename.length>0&&!/[\\/\x00-\x1f]/.test(item.filename)&&meta(item)&&['draft','retired','migration_excluded','not_selected','catalog_only'].includes(item.reason);}
  function validate(value,body){
    if(!NXData.record(value)||value.contract_version!==1||value.scope!=='planned_release_files'||value.complete!==true||value.read_only!==true||value.nx_files_written!==false||value.resources_written!==false||value.automatic_approval!==false||value.filesystem_atomic_snapshot!==false||value.private_integrity_checked!==false||value.resource_links!=='current_planned_operations_only'||value.owner_limit!==10||value.release_id!==root.release_id||value.nx_version!==root.nx_version||value.preview_hash!==body.preview_hash||value.parent_manifest_sha256!==root.parent_manifest_sha256||!hex(value.after_manifest_sha256)||!hex(value.private_snapshot_sha256)||value.view!==body.view||value.change!==body.change||value.query!==body.query||value.offset!==body.offset||value.limit!==50||!nat(value.total)||!nat(value.filtered)||value.filtered>value.total||!nat(value.private_attachments)||!NXData.record(value.summary)||!NXData.record(value.counts))return false;
    if(Object.keys(value.counts).sort().join(',')!=='created,removed,unchanged,updated'||Object.values(value.counts).some(count=>!nat(count))||Object.keys(value.summary).sort().join(',')!=='after_bytes,after_files,before_bytes,before_files'||Object.values(value.summary).some(count=>!nat(count)))return false;
    const c=value.counts,s=value.summary,total=c.created+c.updated+c.removed+c.unchanged;
    if(!nat(total)||s.before_files!==c.updated+c.removed+c.unchanged||s.after_files!==c.created+c.updated+c.unchanged||value.total!==(body.view==='files'?total:value.private_attachments)||!Array.isArray(value.items)||value.items.length!==Math.min(50,Math.max(0,value.filtered-body.offset)))return false;
    if(value.items.some(item=>body.view==='files'?!validFile(item,body):!validPrivate(item)))return false;
    const ids=value.items.map(item=>body.view==='files'?item.path.toLowerCase():item.resource_id+'/'+item.asset_id);return new Set(ids).size===ids.length;
  }
  function fingerprint(value){return JSON.stringify([value.after_manifest_sha256,value.private_snapshot_sha256,value.counts,value.summary,value.private_attachments]);}
  function metadataCell(row,value){const target=cell(row,value?value.size+' Bytes · SHA-256 '+value.sha256:'Nicht vorhanden');target.style.overflowWrap='anywhere';}
  function render(value){
    const report=root,epoch=generation,source=signature(),current=()=>root===report&&index===value&&epoch===generation&&source===signature()&&context()?.preview_hash===report.preview_hash&&!owner&&workspace==='manage'&&Boolean(token)&&el('box').open;
    const nodes=value.items.map(item=>{const row=document.createElement('tr');cell(row,value.view==='files'?item.path:item.filename).style.overflowWrap='anywhere';cell(row,labels[value.view==='files'?item.change:item.reason]+(value.view==='files'?' · '+kinds[item.kind]:''));metadataCell(row,value.view==='files'?item.before:null);metadataCell(row,value.view==='files'?item.after:item);const target=cell(row,'');
      const links=value.view==='files'?item.resources:[{id:item.resource_id,name:item.resource_name,revision:item.resource_revision}];for(const link of links){const line=document.createElement('div');line.textContent=link.name+' · Revision '+link.revision+' ';line.append(button('Stammdaten öffnen',()=>{if(!current()||!row.isConnected)return;const resource=resources.find(value=>value.id===link.id&&value.revision===link.revision);if(resource)openEditor(resource);}));target.append(line);}if(value.view==='files'&&!item.owners_complete)target.append(document.createTextNode('Nur 10 von '+item.resource_count+' aktuellen Bezügen angezeigt; Suche berücksichtigt alle.'));if(!links.length)target.textContent='Kein aktueller geplanter Bezug';return row;});
    el('items').replaceChildren(...nodes);el('page').textContent=(value.items.length?value.offset+1:0)+'–'+(value.offset+value.items.length)+' von '+value.filtered+' Treffern.';
    const c=value.counts,s=value.summary;el('totals').textContent=c.created+' neu · '+c.updated+' geändert · '+c.removed+' entfernt · '+c.unchanged+' unverändert. Ordner: '+s.before_files+' Dateien / '+s.before_bytes+' Bytes → '+s.after_files+' Dateien / '+s.after_bytes+' Bytes. '+value.private_attachments+' private Anhangszuordnungen. Geplantes Manifest SHA-256 '+value.after_manifest_sha256;
    el('totals').style.overflowWrap='anywhere';el('state').textContent=value.view==='files'?'Vollständiger Dateiplan seitenweise gelesen. Nur Dateibytes und aktuelle geplante Bezüge; kein atomarer Snapshot und kein NX-Freigabenachweis.':'Private Anhangsmetadaten gelesen; Dateibytes nicht erneut geprüft. Kein Entfernen bereits geerbter Dateien zugesagt.';
  }
  async function load(offset=0){if(owner||!supported||blocked||!root?.can_export||!context())return;const body={...context(),...filters(),offset,limit:50};delete body.release_id;delete body.parent_manifest_sha256;const snapshot=signature(),current=generation,request=NXRequests.begin('exports.files.read',()=>snapshot===signature());owner=request;clear();controls();el('state').textContent='Lese den gebundenen Dateiplan und private Anhangsmetadaten …';
    try{const value=await request.wait(api('releases/'+root.release_id+'/derive-preview/files','POST',body));if(!validate(value,body))throw new Error('Kein vollständiger gebundener Dateiplan.');const pin=fingerprint(value);if(pins!==null&&pins!==pin){blocked=true;throw new Error('Dateimetadaten geändert. Gesamte Exportvorschau erneut prüfen.');}pins=pin;index=value;render(value);
    }catch(error){if(request.current()&&!NXRequests.isStale(error)){clear();el('state').textContent='Dateiplan nicht bestätigt: '+error.message;}}
    finally{if(current===generation&&owner===request){owner=null;controls();}}}
  el('load').onclick=()=>load(0);el('prev').onclick=()=>load(Math.max(0,index.offset-50));el('next').onclick=()=>load(index.offset+50);['view','change'].forEach(id=>el(id).onchange=changed);el('query').oninput=changed;el('query').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();load(0);}};
  return {configure,open,reset,clearConnection:()=>{supported=false;reset();}};
})();

/* Read-only native object/row pages, each bound to the current export plan. */
globalThis.NXExportData=(()=>{
  const el=id=>document.getElementById('export-native-'+id),hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value),nat=value=>Number.isSafeInteger(value)&&value>=0;
  const labels={created:'Neu',updated:'Geänderte Werte',removed:'Aus neuer Kopie entfernt'};
  let supported=false,root=null,index=null,selected=null,rows=null,owner=null,generation=0,specs=[];
  const filters=()=>({library_id:el('library').value,change:el('change').value,query:el('query').value});
  const context=()=>NXExports.context(),signature=()=>JSON.stringify([context(),filters(),selected,el('side').value]);
  function controls(){const busy=Boolean(owner),valid=supported&&root&&context()?.preview_hash===root.preview_hash;el('load').disabled=!valid||busy;el('prev').disabled=!valid||busy||!index||index.offset===0;el('next').disabled=!valid||busy||!index||index.offset+index.items.length>=index.filtered;el('read').disabled=!valid||busy||!selected;el('row-prev').disabled=!valid||busy||!rows||rows.offset===0;el('row-next').disabled=!valid||busy||!rows||rows.offset+rows.items.length>=rows.total;el('objects').querySelectorAll('button').forEach(button=>button.disabled=!valid||busy);}
  function clearRows(){NXRequests.invalidate('exports.native.rows');rows=null;el('rows').replaceChildren();el('row-page').textContent='';controls();}
  function changed(){generation++;NXRequests.invalidate('exports.native.');owner=null;index=selected=rows=null;el('objects').replaceChildren();el('files').replaceChildren();el('rows').replaceChildren();el('detail').hidden=true;el('page').textContent='Filter geändert; DAT-Objekte erneut laden.';controls();}
  function reset(){changed();root=null;el('box').open=false;el('library').value='';el('change').value='';el('query').value='';['state','page','row-state','row-page','title'].forEach(id=>el(id).textContent='');controls();}
  function configure(cap){supported=cap.catalog_export?.native_values_preview===1&&cap.catalog_export.native_rows_paged===true&&cap.catalog_export.automatic_row_alignment===false;specs=cap.native_library_specs||[];reset();}
  function open(report){reset();root=report;const values={'':'Alle geplanten Bibliotheken'};for(const spec of specs){if(report.outputs.some(item=>item.path===spec.path))NXData.set(values,spec.id,spec.label+' · '+spec.id);}options(el('library'),values);el('state').textContent=report.can_export?'Vorschau ist gebunden. DAT-Änderungen ausdrücklich laden; noch keine Datei geschrieben.':'Zuerst Exportkonflikte lösen und eine neue vollständige Exportvorschau prüfen.';controls();}
  function common(value,body){return NXData.record(value)&&value.contract_version===1&&value.release_id===root?.release_id&&value.nx_version===root.nx_version&&value.preview_hash===body.preview_hash&&value.parent_manifest_sha256===root.parent_manifest_sha256&&value.complete===true&&value.read_only===true&&value.nx_files_written===false&&value.resources_written===false&&value.automatic_approval===false&&value.scope==='native_object_values'&&value.row_alignment==='none';}
  function validItem(item){return NXData.record(item)&&specs.some(spec=>spec.id===item.library_id&&spec.path===item.path)&&typeof item.key==='string'&&Object.hasOwn(labels,item.change)&&nat(item.before_rows)&&nat(item.after_rows)&&(item.before_rows?hex(item.before_sha256):item.before_sha256===null)&&(item.after_rows?hex(item.after_sha256):item.after_sha256===null)&&(item.change==='created'?item.before_rows===0&&item.after_rows>0:item.change==='removed'?item.before_rows>0&&item.after_rows===0:item.before_rows>0&&item.after_rows>0&&item.before_sha256!==item.after_sha256);}
  function validLibraries(value){if(!Array.isArray(value.libraries)||value.libraries.length!==root.outputs.length)return false;const seen=new Set(),sum={created:0,updated:0,removed:0,unchanged:0};for(const item of value.libraries){const output=root.outputs.find(row=>row.path===item.path);if(!NXData.record(item)||!specs.some(spec=>spec.id===item.library_id&&spec.path===item.path)||seen.has(item.library_id)||!output||item.after_sha256!==output.sha256||!hex(item.before_sha256)||!hex(item.after_sha256)||!NXData.record(item.counts)||Object.keys(item.counts).sort().join(',')!=='created,removed,unchanged,updated')return false;seen.add(item.library_id);for(const name of Object.keys(sum)){if(!nat(item.counts[name]))return false;sum[name]+=item.counts[name];}}return Object.keys(sum).every(name=>sum[name]===value.counts[name]);}
  async function load(offset=0){if(owner||!context()||!supported)return;const body={...context(),...filters(),offset,limit:50};delete body.release_id;delete body.parent_manifest_sha256;const snapshot=signature(),request=NXRequests.begin('exports.native.objects',()=>snapshot===signature()),current=generation;owner=request;controls();el('state').textContent='Vergleiche tatsächliche DAT-Werte der gebundenen Exportplanung …';
    try{const value=await request.wait(api('releases/'+root.release_id+'/derive-preview/objects','POST',body));
      if(!common(value,body)||value.library_id!==body.library_id||value.change!==body.change||value.query!==body.query||value.offset!==offset||value.limit!==50||!nat(value.total)||!nat(value.filtered)||value.filtered>value.total||!NXData.record(value.counts)||Object.keys(value.counts).sort().join(',')!=='created,removed,unchanged,updated'||Object.values(value.counts).some(count=>!nat(count))||value.counts.created+value.counts.updated+value.counts.removed!==value.total||!validLibraries(value)||!Array.isArray(value.items)||value.items.length!==Math.min(50,Math.max(0,value.filtered-offset))||value.items.some(item=>!validItem(item)))throw new Error('Kein vollständiger gebundener DAT-Objektvergleich.');
      el('files').replaceChildren(...value.libraries.map(item=>{const node=document.createElement('li');node.textContent=item.path+' · '+(item.before_sha256===item.after_sha256?'Bytes unverändert':'Dateibytes werden neu geschrieben; Objektwerte unten')+' · vorher SHA '+item.before_sha256+' · nachher SHA '+item.after_sha256;node.style.overflowWrap='anywhere';return node;}));
      index=value;selected=null;clearRows();el('detail').hidden=true;const report=root,source=JSON.stringify([context(),filters()]),current=()=>root===report&&index===value&&source===JSON.stringify([context(),filters()])&&!owner&&workspace==='manage'&&Boolean(token)&&el('box').open;
      const nodes=value.items.map(item=>{const row=document.createElement('tr');cell(row,item.library_id+' / '+item.key);cell(row,labels[item.change]);cell(row,item.before_rows+' / '+item.after_rows);const actions=cell(row,'');actions.append(button('Vorher-/Nachher-Werte',()=>{if(!current()||!row.isConnected)return;generation++;NXRequests.invalidate('exports.native.rows');selected=item;clearRows();el('detail').hidden=false;el('title').textContent=item.library_id+' / '+item.key+' · '+labels[item.change];el('side').value=item.change==='removed'?'before':'after';el('row-state').textContent='Seite auswählen und Werte ausdrücklich laden; keine automatische Zeilenpaarung.';controls();}));return row;});el('objects').replaceChildren(...nodes);
      el('page').textContent=(value.items.length?offset+1:0)+'–'+(offset+value.items.length)+' von '+value.filtered+' Treffern.';el('state').textContent=value.counts.created+' neue, '+value.counts.updated+' geänderte, '+value.counts.removed+' entfernte Objektgruppen; '+value.counts.unchanged+' unveränderte Gruppen in den geplanten DAT-Dateien. Datei-/Paket-/Referenznachweise bleiben in der Hauptvorschau.';
    }catch(error){if(request.current()&&!NXRequests.isStale(error)){index=null;el('state').textContent='DAT-Vergleich nicht bestätigt: '+error.message;}}
    finally{if(current===generation&&owner===request){owner=null;controls();}}}
  async function read(offset=0){if(owner||!selected||!context())return;const chosen=selected,side=el('side').value,body={...context(),library_id:chosen.library_id,key:chosen.key,side,group_sha256:chosen[side+'_sha256'],offset,limit:20};delete body.release_id;delete body.parent_manifest_sha256;const snapshot=signature(),request=NXRequests.begin('exports.native.rows',()=>snapshot===signature()),current=generation;owner=request;controls();el('row-state').textContent='Lade vollständige FORMAT-Felder dieser Objektseite …';
    try{const value=await request.wait(api('releases/'+root.release_id+'/derive-preview/rows','POST',body));
      if(!common(value,body)||value.library_id!==chosen.library_id||value.key!==chosen.key||value.side!==side||value.group_sha256!==body.group_sha256||value.present!==(chosen[side+'_rows']>0)||value.total!==chosen[side+'_rows']||value.offset!==offset||value.limit!==20||!Array.isArray(value.formats)||!Array.isArray(value.items)||value.items.length!==Math.min(20,Math.max(0,value.total-offset)))throw new Error('Kein vollständiger gebundener DAT-Zeilenvergleich.');
      const formats=new Map();for(const format of value.formats){if(!NXData.record(format)||typeof format.id!=='string'||typeof format.class_name!=='string'||!hex(format.schema_hash)||!Array.isArray(format.fields)||format.fields.some(field=>typeof field!=='string')||new Set(format.fields).size!==format.fields.length||formats.has(format.id))throw new Error('Ungültiger FORMAT-Vertrag im DAT-Vergleich.');formats.set(format.id,format);}
      for(const [position,item] of value.items.entries()){const format=formats.get(item.block_id);if(!format||item.row_index!==offset+position||item.class_name!==format.class_name||!NXData.record(item.values)||Object.keys(item.values).length!==format.fields.length||format.fields.some(field=>typeof NXData.get(item.values,field)!=='string'))throw new Error('Unvollständige native Objektzeile; keine Teilanzeige.');}
      const nodes=value.items.map(item=>{const box=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Zeile '+(item.row_index+1)+' · '+item.block_id+' · RTYPE '+(NXData.get(item.values,'RTYPE')??'—')+' · SEQ '+(NXData.get(item.values,'SEQ')??'—');box.append(summary);const table=document.createElement('table');for(const field of formats.get(item.block_id).fields){const row=document.createElement('tr');cell(row,field);cell(row,NXData.get(item.values,field));table.append(row);}const wrap=document.createElement('div');wrap.className='scroll';wrap.append(table);box.append(wrap);return box;});el('rows').replaceChildren(...nodes);rows=value;el('row-state').textContent=value.present?'Vollständige Feldwerte dieser Seite. Vorher/Nachher sind getrennte Reihenfolgen, keine RTYPE-/SEQ-Zuordnung oder fachliche Freigabe.':'Dieses Objekt ist auf dieser Seite nicht vorhanden.';el('row-page').textContent=(value.items.length?offset+1:0)+'–'+(offset+value.items.length)+' von '+value.total+' Zeilen.';
    }catch(error){if(request.current()&&!NXRequests.isStale(error)){rows=null;el('row-state').textContent='Objektwerte nicht bestätigt: '+error.message;}}
    finally{if(current===generation&&owner===request){owner=null;controls();}}}
  el('load').onclick=()=>load(0);el('prev').onclick=()=>load(Math.max(0,index.offset-50));el('next').onclick=()=>load(index.offset+50);el('read').onclick=()=>read(0);el('row-prev').onclick=()=>read(Math.max(0,rows.offset-20));el('row-next').onclick=()=>read(rows.offset+20);
  ['library','change'].forEach(id=>el(id).onchange=changed);el('query').oninput=changed;el('query').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();load(0);}};el('side').onchange=()=>{generation++;NXRequests.invalidate('exports.native.rows');owner=null;clearRows();el('row-state').textContent='Seite geändert; Werte erneut laden.';};
  return {configure,open,reset,clearConnection:()=>{supported=false;reset();}};
})();

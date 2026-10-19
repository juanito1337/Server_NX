'use strict';
/* Exact Python-compatible JSON hashes and full upgrade-plan qualification. */
globalThis.NXUpgradePlan=(()=>{
  const record=value=>NXData.record(value),hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const nat=value=>Number.isSafeInteger(value)&&value>=0,text=(value,max=4096)=>typeof value==='string'&&value.length>0&&value.length<=max;
  const uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
  function order(a,b){const left=Array.from(a),right=Array.from(b);for(let index=0;index<Math.min(left.length,right.length);index++){const delta=left[index].codePointAt(0)-right[index].codePointAt(0);if(delta)return delta;}return left.length-right.length;}
  function canonical(value,compact=false){let nodes=0;const comma=compact?',':', ',colon=compact?':':': ';function encode(item,depth,parent,key){if(++nodes>2000000||depth>64)throw new Error('Vorschau überschreitet die JSON-Strukturgrenze.');if(Array.isArray(item))return '['+item.map((value,index)=>encode(value,depth+1,item,index)).join(comma)+']';if(record(item))return '{'+Object.keys(item).sort(order).map(key=>JSON.stringify(key)+colon+encode(item[key],depth+1,item,key)).join(comma)+'}';if(typeof item==='number'){const literal=NXData.numberLiteral(parent,key,item);if(literal!==null)return literal;}if(item!==null&&!['string','boolean','number'].includes(typeof item)||typeof item==='number'&&!Number.isSafeInteger(item))throw new Error('Vorschau enthält keinen exakt unterstützten JSON-Wert.');return JSON.stringify(item);}return encode(value,0,null,null);}
  function hash(value,compact=false){const bytes=new TextEncoder().encode(canonical(value,compact));if(bytes.length>64*1024*1024)throw new Error('Vorschau überschreitet die Byte-Lesergrenze.');const digest=new jsSHA('SHA-256','UINT8ARRAY');digest.update(bytes);return digest.getHash('HEX');}
  const same=(a,b)=>canonical(a)===canonical(b),own=(value,name)=>record(value)&&Object.hasOwn(value,name);
  function relative(value){return text(value)&&!/[\\:\x00-\x1f*?"<>|]/.test(value)&&!value.startsWith('/')&&value.split('/').every(part=>part&&!['.','..'].includes(part)&&!/[. ]$/.test(part)&&!/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(part));}
  const issue=value=>record(value)&&text(value.reason);
  function versionMetadata(value,expected){
    if(!record(value)||value.contract_version!==1||value.scope!=='nx_installation_version_metadata'||value.nx_version!==expected||value.metadata_only!==true||value.digital_signature_verified!==false||value.nx_execution_verified!==false||!text(value.path)||!hex(value.sha256)||!nat(value.size)||value.size<1||value.size>16*1024*1024)return false;
    if(['file_version','product_version'].some(name=>!Array.isArray(value[name])||value[name].length!==4||value[name].some(part=>!nat(part)||part>65535)||String(value[name][0])!==expected))return false;
    const strings=value.strings,names=['CompanyName','FileVersion','OriginalFilename','ProductName','ProductVersion'];
    if(!record(strings)||Object.keys(strings).sort().join(',')!==names.join(',')||names.some(name=>!text(strings[name],512)||/[\x00-\x1f]/.test(strings[name]))||!strings.CompanyName.toLowerCase().includes('siemens')||!['NX','Designcenter NX'].includes(strings.ProductName)||!['ugraf','ugraf.exe'].includes(strings.OriginalFilename.toLowerCase()))return false;
    return ['FileVersion','ProductVersion'].every((name,index)=>/^\d+(?:\.\d+){1,3}$/.test(strings[name])&&same([...strings[name].split('.').map(Number),...Array(4-strings[name].split('.').length).fill(0)],value[index?'product_version':'file_version']));
  }
  function sidtVersion(value,expected){return record(value)&&value.contract_version===1&&value.scope==='sidt_version_declaration'&&value.nx_version===expected&&/^[0-9]{4}$/.test(expected)&&text(value.path)&&hex(value.sha256)&&nat(value.size)&&value.size>=1&&value.size<=64*1024&&value.metadata_only===true&&value.script_executed===false&&value.provenance_verified===false;}
  function verify(report,job,input,specs){
    const fail=()=>{throw new Error('Kein vollständiger gebundener Upgrade-Plan. Neue Vorschau erforderlich.');};
    if(!record(report)||!record(job)||!record(input)||!Array.isArray(specs)||!specs.length)fail();
    const known=new Map(specs.map(value=>[value.id,value]));
    if(known.size!==specs.length||specs.some(value=>!text(value.id,100)||!relative(value.path)))fail();
    if(!uuid(job.id)||job.mode!=='preview'||job.status!=='completed'||job.progress!==100||job.execution?.contract_version!==1||job.execution.writer_may_be_active!==false||!hex(job.report_sha256)||!same(job.request,input)||!record(job.summary))fail();
    if(report.plan_contract_version!==1||report.scope!=='nx_folder_upgrade_plan'||report.read_only!==true||report.nx_files_written!==false||report.automatic_approval!==false||report.automatic_activation!==false||report.functional_validation_required!==true||report.source_version!==input.source_version||report.target_version!==input.target_version||report.source_release_id!==(input.source_release_id??null)||report.source_release_id!==null&&!uuid(report.source_release_id)||report.request_sha256!==hash(input)||!hex(report.input_sha256)||!nat(report.input_count)||!hex(report.preview_hash)||!hex(report.result_manifest_sha256)||!text(report.source_folder)||!text(report.destination)||report.host_destination!==null&&!text(report.host_destination))fail();
    const unsigned={...report};delete unsigned.preview_hash;
    if(hash(unsigned)!==report.preview_hash||hash(report,true)!==job.report_sha256||job.summary.preview_hash!==report.preview_hash||job.summary.can_build!==report.can_build||job.summary.file_count!==report.file_count||job.summary.bytes!==report.bytes||job.conflicts!==report.conflicts?.length||job.warnings!==report.warnings?.length)fail();
    const check=report.prerequisites;
    if(!record(check)||check.installation_version_contract_version!==1||check.sidt_version_contract_version!==1||check.ready!==true||check.nx_execution_verified!==false||check.nx_required_on_test_pc!==true||check.installer_execution!==false||!Array.isArray(check.versions)||check.versions.length!==2||check.versions.some((value,index)=>!record(value)||value.nx_version!==[input.source_version,input.target_version][index]||value.ready!==true||!Array.isArray(value.errors)||value.errors.length||!text(value.installation)||!text(value.sidt)||!record(value.profile)||!text(value.profile.path)||!hex(value.profile.sha256)||!nat(value.profile.copy_instructions)||!value.profile.copy_instructions||!versionMetadata(value.version_metadata,value.nx_version)||!sidtVersion(value.profile.version_declaration,value.nx_version)))fail();
    if(!nat(report.file_count)||report.file_count>100000||!nat(report.bytes)||!Array.isArray(report.result_manifest)||report.result_manifest.length!==report.file_count)fail();
    const files=new Map(),prefixes=new Map();let bytes=0;
    for(const value of report.result_manifest){if(!record(value)||Object.keys(value).sort().join(',')!=='path,sha256,size'||!relative(value.path)||!nat(value.size)||!hex(value.sha256)||files.has(value.path.toLowerCase()))fail();files.set(value.path.toLowerCase(),value);bytes+=value.size;if(!nat(bytes))fail();const parts=value.path.split('/');for(let index=1;index<=parts.length;index++){const path=parts.slice(0,index).join('/'),key=path.toLowerCase(),kind=index===parts.length?'file':'directory',previous=prefixes.get(key);if(previous&&(previous.path!==path||previous.kind!==kind)&&report.can_build)fail();prefixes.set(key,{path,kind});}}
    if(bytes!==report.bytes||hash(report.result_manifest)!==report.result_manifest_sha256)fail();
    if(!Array.isArray(report.actions)||report.actions.some(value=>!record(value)||!relative(value.path)||!['optional_palette_omitted','legacy_library_merged','old_environment_snapshot_omitted','target_path_converted','standard_upgraded','obsolete_standard_omitted','unchanged','customer_copied','standard_selected'].includes(value.action)||value.explicit_choice!==undefined&&value.explicit_choice!==null&&value.explicit_choice!=='customer')||!Array.isArray(report.ignored_outside_scope)||report.ignored_outside_scope.some(value=>!relative(value))||!Array.isArray(report.warnings)||report.warnings.some(value=>!issue(value)||!relative(value.path))||!Array.isArray(report.conflicts)||report.conflicts.some(value=>!issue(value)||!['library','references','file','path'].includes(value.category))||typeof report.can_build!=='boolean'||report.can_build!==(report.conflicts.length===0))fail();
    const conflictPaths=new Set();
    for(const value of report.conflicts.filter(value=>value.category==='file')){
      if(!relative(value.path)||conflictPaths.has(value.path.toLowerCase())||!hex(value.customer_sha256)||value.old_sha256!==null&&!hex(value.old_sha256)||value.target_sha256!==null&&!hex(value.target_sha256)||value.old_sha256===value.target_sha256||value.customer_sha256===value.old_sha256||value.customer_sha256===value.target_sha256)fail();
      conflictPaths.add(value.path.toLowerCase());
    }
    const libraries=report.libraries,refs=report.references;
    if(!record(libraries)||libraries.target_version!==input.target_version||libraries.legacy_version!==input.source_version||libraries.source_version!==input.source_version||libraries.optional_palette_omitted!=='MACH/resource/dmg_templates.pax'||!same(libraries.explicit_legacy_omissions,input.legacy_omissions)||!record(libraries.input_hashes)||Object.values(libraries.input_hashes).some(value=>!hex(value))||!record(libraries.result_hashes)||!Array.isArray(libraries.libraries)||!Array.isArray(libraries.conflicts)||libraries.can_import!==(libraries.conflicts.length===0)||!Array.isArray(libraries.reference_resolutions)||!record(refs)||!same(libraries.references,refs)||!Array.isArray(refs.errors)||!Array.isArray(refs.warnings)||!nat(refs.warning_rows)||refs.automatic_geometry_validation!==false||refs.errors.some(value=>!issue(value)||!known.has(value.library))||refs.warnings.some(value=>!issue(value)||!known.has(value.library)))fail();
    if(!same(report.conflicts.filter(value=>['library','references'].includes(value.category)),libraries.conflicts.map(value=>({...value,category:value.type==='reference'?'references':'library'}))))fail();
    for(const [path,fingerprint] of Object.entries(libraries.result_hashes)){if(!relative(path)||!hex(fingerprint)||files.get(path.toLowerCase())?.sha256!==fingerprint||!specs.some(value=>value.path===path))fail();}
    if(libraries.libraries.some(value=>!record(value)||!known.has(value.library)||value.target!==known.get(value.library).path||!relative(value.source)||Object.entries(value).some(([key,data])=>!['library','source','target'].includes(key)&&!nat(data))))fail();
    for(const value of libraries.reference_resolutions){if(!record(value)||!known.has(value.library)||!known.has(value.target)||!text(value.key,1024)||!text(value.field,100)||!text(value.previous,1024)||!text(value.value,1024)||!nat(value.affected_rows)||!value.affected_rows||NXData.get(NXData.get(NXData.get(NXData.get(input.reference_resolutions,value.library),value.key),value.field),value.previous)!==value.value)fail();}
    if(!Array.isArray(report.new_columns))fail();const columns=new Set();
    for(const value of report.new_columns){const identifier=value?.library_id,key=identifier+'/'+value?.block_id+'/'+value?.field,values=NXData.get(NXData.get(input.defaults,identifier),value?.block_id),confirmed=own(values,value?.field);if(!record(value)||!known.has(identifier)||!text(value.block_id,200)||!text(value.field,100)||columns.has(key)||typeof value.confirmed!=='boolean'||value.confirmed!==confirmed||(confirmed?typeof value.value!=='string'||value.value!==values[value.field]:value.value!==null)||!Array.isArray(value.keys)||!value.keys.length||value.keys.some(key=>!text(key,1024))||new Set(value.keys).size!==value.keys.length||report.can_build&&!value.confirmed)fail();columns.add(key);}
    return true;
  }
  return {verify,hash,versionMetadata,sidtVersion};
})();

globalThis.NXUpgrade=(()=>{
  const el=id=>document.getElementById(id);
  let preview=null,generation=0,selectionVersion=0,timer=null,busy=false,inputs=null,activeJob=false,supported=false,pending=null,memory=null,storageError='',folderJobsBusy=false,planSupported=false,specs=[],shownReport=null,shownScope=null;
  const storageKey='nx.pending-migration-request.v1',paths=new Set(['migrations/preview','migrations/build','migrations/extract']);
  const uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
  const running=new Set(['queued','planning','building','verifying','registering','scanning','extracting']);
  const reviewKinds={customer_copied:'Kundenanpassung übernehmen',standard_upgraded:'Zielstandard verwenden',unchanged:'Entspricht dem Zielstandard',standard_selected:'Zielstandard ausdrücklich gewählt',obsolete_standard_omitted:'Entfallenen Altstandard weglassen',target_path_converted:'Lokalen Testpfad anpassen',old_environment_snapshot_omitted:'Alte Umgebungssicherung weglassen',optional_palette_omitted:'Optionale DMG-Palette weglassen',legacy_library_merged:'Alt-Datenbanken prüfen',library:'Bibliotheken abgleichen',warning:'NX-Kompatibilität prüfen',ignored:'Nicht aus der Kundenquelle übernehmen'};
  const reviewNotes={customer_copied:'Datei aus der Kundenquelle in den neuen Ordner übernehmen.',standard_upgraded:'Den Siemens-Standard der Zielversion verwenden, nicht den unveränderten Altstandard zurückkopieren.',unchanged:'Die Quelldatei entspricht bereits dem Siemens-Zielstandard.',standard_selected:'Den Zielstandard verwenden; falls die Datei dort entfällt, die Altdatei nicht übernehmen.',obsolete_standard_omitted:'Der unveränderte Altstandard entfällt in der Zielversion. Die Originalquelle bleibt erhalten.',target_path_converted:'Die bekannte custom_dirs-Pfadreferenz auf den lokalen Zielordner anpassen; kein Zugriff auf das Produktivlaufwerk.',old_environment_snapshot_omitted:'Die unveränderte alte Umgebungssicherung nicht in den neuen Stand kopieren.',optional_palette_omitted:'DMG_Templates.pax wird wie vereinbart nicht übernommen. Die Originalquelle bleibt erhalten.'};
  const groupCounts={added_groups:'Neue Objektgruppen',identical_groups:'Bereits identische Objektgruppen',unchanged_old_standard:'Unveränderter Altstandard',customer_only_changes:'Reine Kundenänderungen',resolved_customer:'Ausdrücklich Kundenstand gewählt',resolved_standard:'Ausdrücklich Standard gewählt',explicitly_omitted_customer_groups:'Ausdrücklich ausgelassene Kundengruppen',conflicts:'Konflikte'};
  let reviewRows=null,reviewPage=0;
  let conflictOwner=null,conflictRows=[],conflictPage=0;
  function fileChoices(){
    const value=JSON.parse(el('upgrade-file-resolutions').value);
    if(!NXData.record(value)||Object.values(value).some(choice=>!['standard','customer'].includes(choice)))throw new Error('Dateientscheidungen müssen ein JSON-Objekt mit standard/customer sein. Rohdaten ausdrücklich korrigieren; vorhandene Daten bleiben erhalten.');
    const paths=new Set();
    for(const path of Object.keys(value)){const key=path.toLowerCase();if(paths.has(key))throw new Error('Mehrdeutige Dateientscheidungen: denselben Pfad nicht mit unterschiedlicher Groß-/Kleinschreibung angeben. Rohdaten ausdrücklich korrigieren.');paths.add(key);}
    return value;
  }
  function fileChoiceKey(choices,path){return Object.keys(choices).find(key=>key.toLowerCase()===path.toLowerCase());}
  function conflictControls(){let choices=null;try{choices=fileChoices();}catch{/* Invalid raw input must not be replaced by a dropdown. */}
    const writable=supported&&planSupported&&!busy&&!activeJob&&!folderJobsBusy&&!pending&&!storageError&&Boolean(choices);
    el('upgrade-file-conflicts').querySelectorAll('[data-upgrade-file-choice]').forEach(control=>control.disabled=!writable);
    if(!conflictOwner)return;
    const files=conflictRows.filter(value=>value.category==='file'),decided=choices?files.filter(value=>fileChoiceKey(choices,value.path)!==undefined).length:0;
    el('upgrade-conflict-state').textContent=files.length+' Dateikonflikte · '+(conflictRows.length-files.length)+' Verweis-/Pfadkonflikte · '+decided+' ausdrücklich entschieden.'+(choices?' Entscheidungen nur vorbereitet; Upgrade-Vorschau erneut starten.':' Dateientscheidungen sind kein gültiges Objekt mit eindeutigen Pfaden und standard/customer. Rohdaten korrigieren; keine automatische Ersetzung.');
  }
  function clearConflicts(){conflictOwner=null;conflictRows=[];conflictPage=0;el('upgrade-conflict-review').hidden=true;el('upgrade-conflict-state').textContent='';el('upgrade-conflict-page').textContent='';el('upgrade-conflict-query').value='';el('upgrade-conflict-query').disabled=true;el('upgrade-conflict-kind').value='all';el('upgrade-conflict-kind').disabled=true;el('upgrade-conflict-prev').disabled=true;el('upgrade-conflict-next').disabled=true;el('upgrade-file-conflicts').replaceChildren();}
  function renderConflicts(){
    if(!conflictOwner)return;const owner=conflictOwner,kind=el('upgrade-conflict-kind').value,query=el('upgrade-conflict-query').value.trim().toLowerCase();
    const rows=conflictRows.filter(item=>(kind==='all'||kind===item.category)&&(!query||[item.path||'',item.library||'',item.key||'',item.field||'',item.reason].some(value=>String(value).toLowerCase().includes(query))));
    conflictPage=Math.max(0,Math.min(conflictPage,Math.ceil(rows.length/50)-1));const offset=conflictPage*50,fragment=document.createDocumentFragment();
    let choices=null;try{choices=fileChoices();}catch{/* Keep malformed input visible and untouched. */}
    for(const item of rows.slice(offset,offset+50)){
      const row=document.createElement('tr'),current=()=>conflictOwner===owner&&owner.generation===generation&&owner.scope===scope()&&row.isConnected&&workspace==='update'&&Boolean(token);
      row.addEventListener('change',event=>{if(!current()){event.stopImmediatePropagation();event.preventDefault();}},true);
      cell(row,item.path||(item.category==='path'?'Geplanter Zielbaum':item.library+' / '+item.key+' / '+item.field));
      const reason=cell(row,'');
      if(item.category==='file'){
        const title=document.createElement('strong');title.textContent=item.old_sha256===null?'Neu im Zielstandard':item.target_sha256===null?'Zielstandard entfernt':'Beide Seiten geändert';reason.append(title);
        const note=document.createElement('p');note.textContent=item.old_sha256===null?'Kein entsprechender Altstandard; Kunden- und neuer Siemensstand unterscheiden sich.':item.target_sha256===null?'Der Altstandard entfällt in der Zielversion; die Kundenanpassung ist davon abgewichen.':'Kundenanpassung und neuer Siemensstand weichen beide vom Altstandard ab.';reason.append(note);
        const comparison=document.createElement('dl');
        for(const [label,hash] of [['Altstandard NX'+owner.report.source_version,item.old_sha256],['Kundenanpassung',item.customer_sha256],['Zielstandard NX'+owner.report.target_version,item.target_sha256]]){const term=document.createElement('dt'),value=document.createElement('dd'),code=document.createElement('code');term.textContent=label;code.textContent=hash??'Nicht vorhanden';code.style.overflowWrap='anywhere';value.append(code);comparison.append(term,value);}reason.append(comparison);
      }else reason.textContent=item.reason;
      const target=cell(row,'');
      if(item.category==='references'&&item.field)target.append(NXReferences.control(item,current));
      else if(item.category==='file'){
        const select=document.createElement('select');select.dataset.upgradeFileChoice='true';select.setAttribute('aria-label','Dateientscheidung für '+item.path);
        const chosen=choices?fileChoiceKey(choices,item.path):undefined;
        options(select,{'':'Noch entscheiden',standard:item.target_sha256===null?'Alte Anpassung nicht übernehmen (Zieldatei entfällt)':'Neuen Siemensstand verwenden',customer:'Kundenanpassung unverändert übernehmen'});select.value=chosen===undefined?'':choices[chosen];
        select.onchange=action(()=>{if(!current()||select.disabled)return;const data=fileChoices(),key=fileChoiceKey(data,item.path)??item.path;if(select.value)NXData.set(data,key,select.value);else NXData.remove(data,key);el('upgrade-file-resolutions').value=JSON.stringify(data,null,2);invalidate();conflictControls();});target.append(select);
      }else target.textContent='Pfad in den Eingaben klären; keine automatische Umdeutung.';
      fragment.append(row);
    }
    el('upgrade-file-conflicts').replaceChildren(fragment);el('upgrade-conflict-page').textContent=(rows.length?offset+1:0)+'–'+Math.min(offset+50,rows.length)+' von '+rows.length+' gefilterten Konflikten · '+conflictRows.length+' insgesamt';el('upgrade-conflict-prev').disabled=conflictPage===0;el('upgrade-conflict-next').disabled=offset+50>=rows.length;conflictControls();
  }
  function showConflicts(report){clearConflicts();conflictOwner={report,generation,scope:scope()};conflictRows=report.conflicts.filter(item=>item.category!=='library');el('upgrade-conflict-review').hidden=!conflictRows.length;el('upgrade-conflict-query').disabled=false;el('upgrade-conflict-kind').disabled=false;renderConflicts();}
  function clearReview(){reviewRows=null;reviewPage=0;el('upgrade-review').hidden=true;el('upgrade-review-summary').textContent='';el('upgrade-review-context').textContent='';el('upgrade-review-items').replaceChildren();el('upgrade-review-page').textContent='';el('upgrade-review-query').value='';el('upgrade-review-query').disabled=true;options(el('upgrade-review-kind'),{all:'Alle Aktionen und Hinweise'});el('upgrade-review-kind').disabled=true;el('upgrade-review-prev').disabled=true;el('upgrade-review-next').disabled=true;}
  function renderReview(){if(reviewRows===null)return;const kind=el('upgrade-review-kind').value,query=el('upgrade-review-query').value.trim().toLowerCase(),rows=reviewRows.filter(row=>(kind==='all'||row.kind===kind)&&(!query||[row.path,row.label,row.note].some(value=>value.toLowerCase().includes(query))));reviewPage=Math.max(0,Math.min(reviewPage,Math.ceil(rows.length/50)-1));const offset=reviewPage*50;
    el('upgrade-review-items').replaceChildren(...rows.slice(offset,offset+50).map(item=>{const row=document.createElement('tr');cell(row,item.path);cell(row,item.label);cell(row,item.note);return row;}));el('upgrade-review-page').textContent=(rows.length?offset+1:0)+'–'+Math.min(offset+50,rows.length)+' von '+rows.length+' gefilterten Aktionen/Hinweisen · '+reviewRows.length+' insgesamt';el('upgrade-review-prev').disabled=reviewPage===0;el('upgrade-review-next').disabled=offset+50>=rows.length;}
  function showReview(report){const omitted=new Set(report.libraries.explicit_legacy_omissions),merged=new Map(report.libraries.libraries.map(item=>[item.source,item]));
    const rows=report.actions.map(item=>{let label=reviewKinds[item.action],note=reviewNotes[item.action]||'';if(item.action==='legacy_library_merged'){const library=merged.get(item.path);if(omitted.has(item.path)){label='Ausdrücklich ausgelassene Alt-Datenbank';note='Diese zusätzliche Alt-Datenbank wird auf ausdrückliche Entscheidung nicht übernommen; keine Behauptung einer Zusammenführung. Die Quelle bleibt erhalten.';}else if(library){label='Alt-Datenbank in Standard abgleichen';note='Geplantes Ziel: '+library.target+'. Konflikte: '+(library.conflicts||0)+'. Keine separate Alt-Datenbank im neuen Ordner; noch kein ausgeführter Build.';}else{label='Alt-Datei nicht separat kopieren';note='Keine neue eigenständige Alt-Datenbank. Sicherungen und ungeklärte Zuordnungen sind nicht automatisch zusammengeführt; Bibliothekskonflikte und Bericht prüfen.';}}if(item.explicit_choice==='customer')note+=' Kundenstand ausdrücklich gewählt.';return {path:item.path,kind:item.action,label,note};});
    report.libraries.libraries.forEach(item=>rows.push({path:item.source,kind:'library',label:reviewKinds.library,note:item.library+' → '+item.target+' · '+Object.entries(item).filter(([key])=>!['library','source','target'].includes(key)).map(([key,value])=>(Object.hasOwn(groupCounts,key)?groupCounts[key]:key)+': '+value).join(' · ')}));
    report.warnings.forEach(item=>rows.push({path:item.path,kind:'warning',label:reviewKinds.warning,note:item.reason+' Datei vor Freigabe fachlich in der Zielversion testen.'}));
    report.ignored_outside_scope.forEach(path=>rows.push({path,kind:'ignored',label:reviewKinds.ignored,note:'Nicht als Kundenanpassung im freigegebenen Netzwerkumfang übernommen. Die Quelldatei bleibt erhalten; kein pauschales Kopieren von Installationsdateien.'}));
    clearReview();reviewRows=rows;const counts=new Map();for(const row of rows)counts.set(row.kind,(counts.get(row.kind)||0)+1);options(el('upgrade-review-kind'),{all:'Alle Aktionen und Hinweise ('+rows.length+')',...Object.fromEntries(Object.entries(reviewKinds).filter(([key])=>counts.has(key)).map(([key,label])=>[key,label+' ('+counts.get(key)+')']))});el('upgrade-review-query').disabled=false;el('upgrade-review-kind').disabled=false;
    el('upgrade-review-summary').textContent='NX'+report.source_version+' → NX'+report.target_version+' · '+report.file_count.toLocaleString('de-DE')+' geplante Zieldateien · '+report.bytes.toLocaleString('de-DE')+' Bytes · '+report.conflicts.length+' Konflikte · '+report.new_columns.filter(column=>!column.confirmed).length+' neue Spaltenwerte nicht bestätigt.';el('upgrade-review-context').textContent='Quelle: '+report.source_folder+' → Neuer Zielordner: '+(report.host_destination||report.destination);renderReview();el('upgrade-review').hidden=false;
  }
  function extractControls(){const blocked=!supported||Boolean(pending)||Boolean(storageError)||folderJobsBusy,configured=inputs?.installers.find(item=>item.version===el('extract-version').value);
    el('extract-start').disabled=blocked||busy||activeJob||!inputs?.reader.ready||!configured?.ready;
    el('upgrade-preview').disabled=blocked||busy||activeJob||!planSupported;el('upgrade-build').disabled=blocked||busy||activeJob||!planSupported||!preview?.ready||!matches(preview.snapshot);
    el('upgrade-recovery-box').hidden=!pending&&!storageError;el('upgrade-request-key').textContent=pending?.key||'—';
    el('upgrade-recovery-state').textContent=storageError||'Auftragsannahme noch nicht bestätigt. Fehlender Beleg beweist keinen Abbruch. Annahme prüfen, denselben Versuch wiederholen oder vor Annahme sperren. Ein bereits angenommener Auftrag wird nicht gestoppt.';
    el('upgrade-receipt').disabled=busy||!supported||!pending;el('upgrade-repeat').disabled=busy||!supported||!pending||!memory;
    el('upgrade-cancel').disabled=busy||!supported||(!pending&&!storageError);
    globalThis.NXReleaseJobs?.upgradeWriterControls(busy||activeJob||Boolean(pending)||Boolean(storageError));
    conflictControls();
  }
  function loadPending(){try{const raw=sessionStorage.getItem(storageKey);if(!raw)return;const value=JSON.parse(raw);
    if(!value||Object.keys(value).sort().join(',')!=='contract_version,created_at,key,method,path'||value.contract_version!==1||value.method!=='POST'||!paths.has(value.path)||!uuid(value.key)||!Number.isFinite(Date.parse(value.created_at)))throw new Error('Ungültiger lokaler Auftragshinweis.');
    pending=value;
  }catch(error){storageError='Lokaler Auftragshinweis nicht lesbar. Neue Starts bleiben gesperrt: '+error.message;}}
  function clearPending(){sessionStorage.removeItem(storageKey);pending=null;memory=null;storageError='';extractControls();}
  function validateAcceptance(ticket,job,action){if(!job||!uuid(job.id)||job.mode!==ticket.path.slice(11)||action&&action!=='migration.'+job.mode)throw new Error('Annahmebeleg passt nicht zu diesem Upgrade-/Extraktionsversuch.');}
  async function accepted(ticket,job,current){
    if(current!==generation||pending?.key!==ticket.key)return;
    validateAcceptance(ticket,job);const context=memory;clearPending();
    message('Auftragsannahme bestätigt: '+job.id+'. Aktuellen Status abfragen; kein zweiter Auftrag und keine automatische Freigabe.');
    await watch(job.id,current,context?.generation===current?context.snapshot:null,null,ticket.path.slice(11));
  }
  async function send(ticket,payload,current){try{return await api(ticket.path,'POST',payload,{'Idempotency-Key':ticket.key});}
    catch(error){if(current===generation&&pending?.key===ticket.key&&[400,422].includes(error.httpStatus)){
      const value=await api('migration-requests/'+ticket.key+'/cancel','POST');if(current!==generation)throw error;
      if(value.contract_version!==1||value.request_key!==ticket.key)throw new Error('Ungültiger Annahmesperrvertrag.');
      if(value.state==='cancelled')clearPending();else if(value.state==='accepted'){validateAcceptance(ticket,value.result,value.action);return value.result;}
    }throw error;}}
  async function submit(path,payload,snapshot,current){
    if(!supported)throw new Error('Server unterstützt keine dauerhaften Update-Annahmebelege. Server aktualisieren.');
    if(pending||storageError)throw new Error('Unbestätigte Auftragsannahme zuerst klären.');
    const ticket={contract_version:1,key:crypto.randomUUID(),method:'POST',path,created_at:new Date().toISOString()};
    sessionStorage.setItem(storageKey,JSON.stringify(ticket));pending=ticket;memory={payload:structuredClone(payload),snapshot,generation:current};extractControls();
    const job=await send(ticket,memory.payload,current);await accepted(ticket,job,current);
  }
  async function recover(mode){if(busy||!supported)return;const current=generation;let handedOff=false;busy=true;extractControls();
    try{
      if(storageError&&!pending){if(!confirm('Nur den unlesbaren Browserhinweis verwerfen? Ein früherer Auftrag könnte angenommen sein. Auftragsliste/Audit zuerst prüfen; keine Serverdaten werden gelöscht.'))return;clearPending();message('Nur lokaler Hinweis verworfen. Vor neuem Start Auftragsliste/Audit prüfen.');return;}
      const ticket=pending;if(!ticket)throw new Error('Kein unbestätigter Update-/Extraktionsversuch.');
      if(mode==='repeat'){
        if(!memory)throw new Error('Nach Neuladen nur Beleg prüfen oder vor Annahme sperren; Eingaben wurden nicht im Browser gespeichert.');
        const job=await send(ticket,memory.payload,current);if(current!==generation)return;handedOff=true;await accepted(ticket,job,current);return;
      }
      if(mode==='cancel'&&!confirm('Noch nicht angenommenen Versuch sperren? Falls bereits angenommen, wird sein Beleg zurückgegeben und der Auftrag weiter beobachtet. Kein Worker-Abbruch, kein Löschen oder Neustart.'))return;
      const value=await api('migration-requests/'+ticket.key+(mode==='cancel'?'/cancel':''),mode==='cancel'?'POST':'GET');
      if(current!==generation||pending?.key!==ticket.key)return;
      if(value.contract_version!==1||value.request_key!==ticket.key)throw new Error('Ungültiger Annahmebelegvertrag.');
      if(value.state==='accepted'){validateAcceptance(ticket,value.result,value.action);handedOff=true;await accepted(ticket,value.result,current);}
      else if(value.state==='cancelled'){clearPending();message('Versuch vor Annahme gesperrt. Eingaben bleiben erhalten; kein bestehender Auftrag oder Ordner gelöscht.');}
      else throw new Error('Kein terminaler Annahmebeleg; Kennung beibehalten.');
    }catch(error){handedOff=false;if(current===generation&&!NXRequests.isStale(error))message((pending?'Auftragsannahme ungeklärt: ':'')+error.message,true);}
    finally{if(current===generation&&!handedOff){busy=false;extractControls();}}
  }
  function body(){const release=el('upgrade-source-release').value;return {source:release?null:el('source').value,source_path:release?null:el('source-path').value,source_release_id:release||null,
    target:el('target').value,target_path:el('target-path').value,source_version:el('legacy-version').value,target_version:el('nxversion').value,name:el('release-name').value,
    defaults:JSON.parse(el('import-defaults').value),resolutions:JSON.parse(el('import-resolutions').value),reference_resolutions:NXReferences.fields(),legacy_omissions:JSON.parse(el('legacy-omissions').value),file_resolutions:fileChoices()};}
  function scope(){return JSON.stringify(['source','source-path','upgrade-source-release','target','target-path','legacy-version','nxversion','upgrade-mode'].map(id=>el(id).value));}
  function clearVerified(){clearReview();if(shownReport&&el('details').textContent===NXData.stringifyWire(shownReport,2))el('details').textContent=JSON.stringify({module:'whole_share_migration',status:'not_verified',reason:'Upgrade-Plan nicht mehr aktuell bestätigt.'},null,2);}
  function clearReport(){clearVerified();clearConflicts();shownReport=null;shownScope=null;el('upgrade-columns').replaceChildren();el('conflicts').replaceChildren();el('reference-applied').replaceChildren();}
  function invalidate(){selectionVersion++;preview=null;if(shownScope&&shownScope!==scope())clearReport();else clearVerified();el('upgrade-build').disabled=true;el('upgrade-state').textContent='Auswahl oder Entscheidungen geändert. Upgrade-Vorschau erneut starten.';}
  function matches(snapshot){try{return Boolean(snapshot)&&snapshot.version===selectionVersion&&snapshot.value===JSON.stringify(body())&&el('upgrade-mode').value==='upgrade';}catch{return false;}}
  const pairKey=()=>JSON.stringify([el('legacy-version').value,el('nxversion').value]);
  function invalidatePrerequisites(){NXRequests.invalidate('upgrade.prerequisites');el('upgrade-prerequisites').textContent='Versionspaar oder SIDT-Eingabe geändert. Voraussetzungen erneut prüfen.';}
  function sourceControls(){const managed=el('upgrade-mode').value==='upgrade'&&Boolean(el('upgrade-source-release').value);['source','source-path','source-browse'].forEach(id=>el(id).disabled=managed);}
  function mode(){const upgrade=el('upgrade-mode').value==='upgrade';el('upgrade-controls').hidden=!upgrade;el('prepared-import-controls').hidden=upgrade;el('normalize').disabled=upgrade;if(upgrade)el('normalize').checked=true;sourceControls();}
  function reset(){generation++;selectionVersion++;NXRequests.invalidate('upgrade.');clearTimeout(timer);timer=null;busy=false;preview=null;inputs=null;activeJob=false;supported=false;planSupported=false;specs=[];clearReport();el('extract-version').replaceChildren();el('extract-state').textContent='Entpacker und freigegebene Pakete werden nach dem Verbinden geprüft.';el('upgrade-mode').value='upgrade';mode();el('upgrade-file-resolutions').value='{}';el('upgrade-columns').replaceChildren();el('upgrade-file-conflicts').replaceChildren();el('upgrade-jobs').replaceChildren();el('upgrade-state').textContent='';el('upgrade-prerequisites').textContent='Installations-/SIDT-Daten beider Versionen prüfen.';extractControls();}
  function reportUI(report){
    // Do not bind to decision edits: users must be able to confirm several fields.
    const owner={generation,scope:scope()},current=()=>shownReport===report&&owner.generation===generation&&owner.scope===scope()&&workspace==='update'&&Boolean(token)&&!busy&&!activeJob&&!folderJobsBusy&&!pending&&!storageError;
    NXReferences.show(report,current);
    showReview(report);
    el('upgrade-file-conflicts').replaceChildren();el('upgrade-columns').replaceChildren();
    showConflicts(report);
    report.new_columns.forEach(column=>{const fieldset=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent=`${column.library_id} · ${column.block_id}.${column.field}`;fieldset.append(legend);const input=document.createElement('input'),label=document.createElement('label');label.textContent=`Wert für ${column.keys.length} Kundengruppe(n)`;input.value=column.value??'';label.append(input);fieldset.append(label);const check=document.createElement('input'),confirm=document.createElement('label');check.type='checkbox';check.checked=column.confirmed;confirm.className='checklist';confirm.append(check,document.createTextNode('Expliziten Wert bestätigen (auch Leerwert)'));fieldset.append(confirm);const update=()=>{if(!current()||!fieldset.isConnected)return;invalidate();const data=JSON.parse(el('import-defaults').value),values=NXData.branch(NXData.branch(data,column.library_id),column.block_id);if(check.checked)NXData.set(values,column.field,input.value);else NXData.remove(values,column.field);el('import-defaults').value=JSON.stringify(data,null,2);};input.oninput=action(update);check.onchange=action(update);el('upgrade-columns').append(fieldset);});
    NXNative.renderConflicts({...report,conflicts:report.conflicts.filter(item=>item.category==='library')},current);
    el('upgrade-state').textContent=report.can_build?`Vorschau baubar: ${report.file_count} Dateien, ${report.warnings.length} Kompatibilitätshinweise. NX-Test/Abnahme weiterhin erforderlich.`:`${report.conflicts.length} Konflikte. Entscheidungen bestätigen und Vorschau erneut starten.`;
  }
  async function refresh(alive=()=>true){
    const current=generation,request=NXRequests.begin('upgrade.list',()=>current===generation&&alive());
    options(el('upgrade-source-release'),{'':'Externe Arbeitskopie aus der Quellordner-Auswahl',...Object.fromEntries(releases.filter(release=>['draft','validated','approved'].includes(release.status)).map(release=>[release.id,release.name+' · NX'+release.nx_version]))});
    const [jobs,settings]=await request.wait(Promise.all([api('migrations'),api('migrations/options')]));inputs=settings;activeJob=jobs.some(job=>running.has(job.status));
    options(el('extract-version'),Object.fromEntries(settings.installers.map(item=>[item.version,(item.filename||'EXE')+(item.ready?'':' · nicht verfügbar')])));
    el('extract-state').textContent=(settings.reader.ready?'Statischer Windows-Entpacker geprüft.':settings.reader.error)+' '+(settings.installers.length?'Pakete: '+settings.installers.map(item=>'NX'+item.version+': '+(item.ready?'SHA/Format geprüft':item.error)).join(' · '):'Keine EXE-Pakete konfiguriert; vorhandenes SIDT-Staging bleibt nutzbar.')+' '+Object.entries(settings.input_selections).map(([version,selection])=>'NX'+version+' verwendet Auftrag '+selection.job_id.slice(0,8)+'.').join(' ');
    extractControls();el('upgrade-jobs').replaceChildren();
    jobs.filter(job=>['preview','build','extract'].includes(job.mode)).forEach(job=>{const row=document.createElement('tr');cell(row,job.id.slice(0,8)+' / '+job.mode);cell(row,NXJobState.label(job));cell(row,job.message);const actions=cell(row,'');actions.append(button('Details',()=>details(job)));if(job.report_sha256)actions.append(button('Bericht',async()=>details(await api('migrations/'+job.id+'/report'))));if(running.has(job.status))actions.append(button('Weiter beobachten',()=>watch(job.id,generation,null,null,job.mode)));if(job.mode==='extract'&&job.status==='completed'){const selected=settings.input_selections[job.request.version]?.job_id===job.id;if(selected)actions.append(document.createTextNode('Als SIDT-Eingabe ausgewählt'));else{const choose=button('Als SIDT-Eingabe wählen',()=>selectInput(job));choose.disabled=busy||activeJob||folderJobsBusy||Boolean(pending)||Boolean(storageError);actions.append(choose);}}if(job.release_id)actions.append(button('Zum NX-Datenstand',()=>showWorkspace('manage','release-title')));el('upgrade-jobs').append(row);});
  }
  async function selectInput(job){if(busy||pending||storageError||folderJobsBusy)return;if(!confirm('Extraktion für NX'+job.request.version+' als Update-Eingabe verwenden? Bestehende NX-Datenstände und Aktivzeiger bleiben unverändert.'))return;const current=generation;busy=true;extractControls();
    try{await api('migrations/'+job.id+'/select-input','POST',{expected_generation:inputs.input_selections[job.request.version]?.generation??0});if(current!==generation)return;invalidatePrerequisites();invalidate();message('NX'+job.request.version+': SIDT-Eingabe ausgewählt. Voraussetzungen und Upgrade-Vorschau erneut prüfen.');}
    finally{if(current===generation){busy=false;await refresh();}}
  }
  async function startExtraction(){if(busy||el('extract-start').disabled)return;if(!confirm('Freigegebenes EXE-Paket nur statisch in einen neuen isolierten Ordner entpacken? Setup und Skripte werden nicht ausgeführt.'))return;const current=generation;invalidate();busy=true;extractControls();
    try{await submit('migrations/extract',{version:el('extract-version').value},null,current);}
    catch(error){if(current===generation){busy=false;extractControls();throw error;}}
  }
  async function watch(id,current,snapshot,request=null,expectedMode=null){
    request??=NXRequests.begin('upgrade.watch',()=>current===generation);
    if(!request.current())return;
    clearTimeout(timer);
    try{const job=await request.wait(api('migrations/'+id));
      if(!NXData.record(job)||job.id!==id||!uuid(job.id)||!['preview','build','extract'].includes(expectedMode)||job.mode!==expectedMode||!NXData.record(job.request)||snapshot&&NXUpgradePlan.hash(job.request)!==NXUpgradePlan.hash(JSON.parse(snapshot.value))||![...running,'completed','failed','interrupted'].includes(job.status)||!Number.isSafeInteger(job.progress)||job.progress<0||job.progress>100||typeof job.message!=='string'||job.execution?.contract_version!==1||typeof job.execution.writer_may_be_active!=='boolean'||job.execution.writer_may_be_active&&!running.has(job.status)){
        busy=true;throw new Error('Auftragsbeleg passt nicht zur beobachteten Kennung, Aktion oder Eingabe.');}
      el('upgrade-state').textContent=NXJobState.label(job)+' · '+job.message;
      if(running.has(job.status)){timer=setTimeout(()=>watch(id,current,snapshot,request,expectedMode),1000);return;}
      busy=false;extractControls();await request.wait(NXRequests.settle(refresh()));
      if(job.status==='completed'&&job.mode==='preview'){
        const report=await request.wait(api('migrations/'+id+'/report'));
        if(matches(snapshot)){
          try{NXUpgradePlan.verify(report,job,JSON.parse(snapshot.value),specs);}catch(error){preview=null;clearReport();el('upgrade-state').textContent='Upgrade-Plan nicht bestätigt: '+error.message;extractControls();return;}
          shownReport=report;shownScope=scope();if(workspace==='update')details(report);reportUI(report);preview={id,hash:report.preview_hash,snapshot,ready:report.can_build};extractControls();}
        else el('upgrade-state').textContent+=' Auswahl passt nicht mehr zum Auftrag. Für diese Auswahl eine neue Vorschau starten; alte Zuordnungen werden nicht angezeigt.';
      }else if(job.status==='completed'&&job.mode==='build'){
        if(!uuid(job.release_id)||!uuid(job.preview_id)||!/^[a-f0-9]{64}$/.test(job.preview_hash||''))throw new Error('Kein gebundener Ergebnisbeleg.');
        const release=await request.wait(api('releases/'+job.release_id)),migration=release?.catalog?.migration;
        if(release?.id!==job.release_id||release.nx_version!==job.request.target_version||migration?.job_id!==id||migration.preview_id!==job.preview_id||migration.preview_hash!==job.preview_hash||migration.source_version!==job.request.source_version||migration.source_release_id!==(job.request.source_release_id??null))throw new Error('Ergebnisdatenstand ist nicht an diesen Versionswechsel gebunden.');
        await request.wait(window.refresh());const focus=matches(snapshot)&&snapshot.editor===editorGeneration&&snapshot.workspace===workspace&&workspace==='update';
        if(focus)showWorkspace('manage','release-title');message('Versionswechsel als neuer NX-Datenstand erstellt. Test-Starter verwenden; keine automatische Freigabe.'+(focus?'':' Aktuelle Eingaben und Arbeitsbereich bleiben erhalten; Ergebnis im Auftrag bzw. Datenstand öffnen.'));}
      else if(job.status==='completed'&&job.mode==='extract')message('NX'+job.request.version+' statisch entpackt. Im Auftrag „Als SIDT-Eingabe wählen“ anklicken; keine automatische Aktivierung.');
    }catch(error){if(!request.current()||NXRequests.isStale(error))return;preview=null;clearReport();extractControls();el('upgrade-state').textContent='Auftragsabfrage fehlgeschlagen: '+error.message+'. Kein Neustart des Auftrags; weiter beobachten oder neu verbinden.';timer=setTimeout(()=>watch(id,current,snapshot,request,expectedMode),3000);}
  }
  async function start(mode){if(busy||pending||storageError)return;if(!planSupported)throw new Error('Server benötigt einen vollständig gebundenen Upgrade-Planvertrag. Server aktualisieren.');const current=generation,request=body(),snapshot={value:JSON.stringify(request),version:selectionVersion,editor:editorGeneration,workspace};if(mode==='build'&&(!preview?.ready||!matches(preview.snapshot)))throw new Error('Zuerst eine passende baubare Vorschau prüfen.');if(mode==='build'&&!confirm('Geprüften Versionswechsel als NEUEN Ordner erstellen? Alte Daten und Freigaben bleiben unverändert.'))return;
    const payload=mode==='build'?{...request,preview_id:preview.id,preview_hash:preview.hash}:request;NXRequests.invalidate('upgrade.watch');clearTimeout(timer);timer=null;preview=null;clearReport();busy=true;extractControls();
    try{await submit('migrations/'+mode,payload,snapshot,current);}catch(error){if(current===generation){busy=false;extractControls();message((pending?'Auftragsannahme ungeklärt: ':'')+error.message,true);}}
  }
  el('upgrade-review-query').oninput=()=>{reviewPage=0;renderReview();};el('upgrade-review-kind').onchange=()=>{reviewPage=0;renderReview();};el('upgrade-review-prev').onclick=()=>{reviewPage--;renderReview();};el('upgrade-review-next').onclick=()=>{reviewPage++;renderReview();};
  el('upgrade-conflict-query').oninput=()=>{conflictPage=0;renderConflicts();};el('upgrade-conflict-kind').onchange=()=>{conflictPage=0;renderConflicts();};el('upgrade-conflict-prev').onclick=()=>{conflictPage--;renderConflicts();};el('upgrade-conflict-next').onclick=()=>{conflictPage++;renderConflicts();};
  el('upgrade-file-resolutions').addEventListener('input',()=>renderConflicts());
  el('extract-version').onchange=extractControls;el('extract-start').onclick=()=>action(startExtraction)();
  el('upgrade-mode').onchange=()=>{NXNative.invalidateImport();mode();};
  el('upgrade-source-release').onchange=()=>{sourceControls();invalidate();};
  ['source','source-path','target','target-path','legacy-version','nxversion','release-name','upgrade-file-resolutions','import-defaults','import-resolutions','reference-resolutions','legacy-omissions'].forEach(id=>el(id).addEventListener('input',invalidate));
  ['legacy-version','nxversion'].forEach(id=>el(id).addEventListener('input',invalidatePrerequisites));
  el('upgrade-preview').onclick=()=>action(()=>start('preview'))();el('upgrade-build').onclick=()=>action(()=>start('build'))();
  el('upgrade-jobs-refresh').onclick=()=>action(refresh)();
  el('upgrade-receipt').onclick=()=>recover('receipt');el('upgrade-repeat').onclick=()=>recover('repeat');el('upgrade-cancel').onclick=()=>recover('cancel');
  el('upgrade-check').onclick=()=>action(async()=>{invalidate();const current=generation,selected=pairKey(),request=NXRequests.begin('upgrade.prerequisites',()=>current===generation&&selected===pairKey());const settings=await request.wait(api('migrations/options'));const pair=settings.adapters.find(adapter=>adapter.source_version===el('legacy-version').value&&adapter.target_version===el('nxversion').value);const verified=pair?.installation_version_contract_version===1&&pair.sidt_version_contract_version===1&&pair.versions?.length===2&&pair.versions.every(version=>version.ready===true&&NXUpgradePlan.versionMetadata(version.version_metadata,version.nx_version)&&NXUpgradePlan.sidtVersion(version.profile?.version_declaration,version.nx_version));el('upgrade-prerequisites').textContent=pair?(pair.ready?(verified?'Installationsmetadaten: '+pair.versions.map(version=>'NX'+version.nx_version+' → '+version.version_metadata.strings.ProductVersion).join(' · ')+'. SIDT-Deklarationen: '+pair.versions.map(version=>'NX'+version.profile.version_declaration.nx_version).join(' · ')+'. SIDT-Kopierprofile vorhanden. Nur statische Metadaten; keine Signatur-, NX-Ausführungs- oder Lizenzprüfung.':'Versionsmetadaten nicht vollständig bestätigt. Neue Serverversion beziehungsweise erneute Prüfung erforderlich.'):pair.versions.flatMap(version=>version.errors.map(error=>'NX'+version.nx_version+': '+error)).join(' · ')):'Kein freigegebener Adapter für dieses Versionspaar.';details(settings);})();
  loadPending();extractControls();
  return {refresh,reset,invalidate,configure:value=>{supported=value.migration_requests?.contract_version===1;planSupported=value.whole_share_migration?.plan_contract_version===1&&value.whole_share_migration.self_hashing_plan===true&&value.whole_share_migration.installation_version_contract_version===1&&value.whole_share_migration.static_pe_version_metadata===true&&value.whole_share_migration.sidt_version_contract_version===1;specs=value.native_library_specs||[];extractControls();},
    sharedWriterControls:value=>{if(folderJobsBusy!==value){folderJobsBusy=value;extractControls();}}};
})();

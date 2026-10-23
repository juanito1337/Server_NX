'use strict';
globalThis.NXResults=(()=>{
  const el=id=>document.getElementById(id);let job=null,resource=null,preview=null,generation=0,busy=false,writerBlocked=true,choices=0,collectorOwner=null,packetsById=new Map(),observedContext=null;
  const hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value),uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
  const revision=value=>Number.isSafeInteger(value)&&value>=1;
  let columnRows=[],columnPage=0,columnConfirmed=new Map(),columnDrafts=new Map();const columnPageSize=10;
  const coordinate=item=>JSON.stringify([item.library,item.key,item.block_id,item.field]);
  function columnInput(){const result=Object.create(null);for(const [key,value] of columnConfirmed){const [library,object,block,field]=JSON.parse(key);NXData.set(NXData.branch(NXData.branch(NXData.branch(result,library),object),block),field,value);}return result;}
  function clearColumns(){columnRows=[];columnPage=0;columnConfirmed.clear();columnDrafts.clear();renderColumns();}
  function columnChanged(){choices++;cancelCollector();NXRequests.invalidate('results.preview');invalidate('Spaltenentscheidungen geändert; Ergebnisvorschau erneut prüfen.');controls();}
  function renderColumns(){
    el('result-columns-box').hidden=columnRows.length===0;el('result-columns').replaceChildren();columnPage=Math.max(0,Math.min(columnPage,Math.max(0,Math.ceil(columnRows.length/columnPageSize)-1)));
    for(const item of columnRows.slice(columnPage*columnPageSize,(columnPage+1)*columnPageSize)){
      const key=coordinate(item),box=document.createElement('fieldset'),legend=document.createElement('legend'),label=document.createElement('label'),input=document.createElement('input'),confirmLabel=document.createElement('label'),check=document.createElement('input');
      box.dataset.field=item.field;box.dataset.library=item.library;box.dataset.object=item.key;legend.textContent=item.library+' / '+item.key+' / '+item.block_id+' · '+item.field+' · '+item.affected_rows+' Zeile(n)';box.append(legend);
      label.textContent='Wert für neue Zielspalte';input.value=columnDrafts.get(key)??columnConfirmed.get(key)??'';input.dataset.columnValue='';label.append(input);box.append(label);
      check.type='checkbox';check.dataset.columnConfirm='';check.checked=columnConfirmed.has(key);confirmLabel.append(check,document.createTextNode('Wert ausdrücklich bestätigen (auch Leerwert); gilt für diese Objekt-/FORMAT-Zeilen'));box.append(confirmLabel);
      const update=()=>{columnDrafts.set(key,input.value);if(check.checked)columnConfirmed.set(key,input.value);else columnConfirmed.delete(key);columnChanged();};input.oninput=update;check.onchange=update;el('result-columns').append(box);
    }
    el('result-columns-page').textContent=columnRows.length?(columnPage*columnPageSize+1)+'–'+Math.min((columnPage+1)*columnPageSize,columnRows.length)+' von '+columnRows.length+' neuen Spaltenentscheidungen. Eingaben bleiben beim Blättern erhalten.':'';
    el('result-columns-prev').disabled=busy||columnPage===0;el('result-columns-next').disabled=busy||(columnPage+1)*columnPageSize>=columnRows.length;el('result-columns-clear').disabled=busy||columnRows.length===0;
  }
  const catalog=value=>JSON.stringify(value.map(item=>[item.id,item.revision]).sort((a,b)=>a[0].localeCompare(b[0])));
  function context(){const target=releases.find(item=>item.id===el('result-target').value);return JSON.stringify([catalog(resources),target?[target.id,target.status,target.nx_version,target.folder]:null]);}
  function cancelCollector(){collectorOwner?.abort();collectorOwner=null;NXRequests.invalidate('results.collector');}
  function invalidate(text){
    if(preview&&el('details').textContent===NXData.stringifyWire(preview,2))el('details').textContent=JSON.stringify({module:'prepared_result_preview',status:'not_verified',reason:text||'Ergebnisvorschau nicht mehr zur Übernahme bereit.'},null,2);
    preview=null;el('result-apply').disabled=true;['result-actions','result-files'].forEach(id=>el(id).replaceChildren());el('result-file-state').textContent='';if(text)el('result-state').textContent=text;}
  function catalogChanged(){const next=context();if(job&&observedContext!==null&&next!==observedContext){NXRequests.invalidate('results.preview');invalidate('Stammdatenbestand oder Zielstand geändert; Ergebnisvorschau erneut prüfen.');}observedContext=next;controls();}
  function checkPreview(value,id,target){
    const fail=()=>{throw new Error('Kein vollständiger gebundener Ergebnisvorschauvertrag. Bestand aktualisieren und Vorschau erneut prüfen.');};
    const check=condition=>{if(!condition)fail();},record=NXData.record,packet=packetsById.get(id),source=packet?.manifest;
    check(record(value)&&record(packet)&&record(source)&&packet.job_id===job?.id&&packet.resource_id===resource?.id);
    check(value.result_id===id&&value.target_release_id===target&&value.resource_id===resource.id&&value.expected_revision===resource.revision&&job.resource_revision===resource.revision);
    check(hex(value.preview_hash)&&hex(value.target_manifest_sha256)&&hex(value.archive_sha256)&&value.archive_sha256===packet.sha256);
    check(typeof value.can_apply==='boolean'&&value.cam_status_after_apply==='draft'&&value.automatic_cam_approval===false&&value.geometry_checks_reset===true&&value.trust==='unverified_external_nx_inventory');
    check(Array.isArray(value.conflicts)&&value.conflicts.every(item=>record(item)&&typeof item.reason==='string'&&item.reason.length>0)&&value.can_apply===(value.conflicts.length===0));
    check(record(value.schema_hashes)&&Object.values(value.schema_hashes).every(hex)&&Array.isArray(value.catalog_revisions));
    check(value.catalog_revisions.every(item=>record(item)&&uuid(item.id)&&revision(item.revision))&&new Set(value.catalog_revisions.map(item=>item.id)).size===value.catalog_revisions.length&&catalog(value.catalog_revisions)===catalog(resources));
    check(Array.isArray(value.files)&&value.files.length>=1&&value.files.length<=80&&Array.isArray(source.files));
    const file=item=>record(item)&&typeof item.path==='string'&&item.path.length>0&&item.path.length<=1024&&item.path.toLowerCase().endsWith('.prt')&&!/[\\:\x00-\x1f]/.test(item.path)&&item.path.split('/').every(part=>part&&!['.','..'].includes(part))&&Number.isSafeInteger(item.size)&&item.size>0&&item.size<=20*1024*1024&&hex(item.sha256);
    check(value.files.every(file)&&new Set(value.files.map(item=>item.path.toLowerCase())).size===value.files.length);
    const files=items=>JSON.stringify(items.map(item=>[item.path,item.size,item.sha256]).sort((a,b)=>a[0].localeCompare(b[0])));
    check(source.files.every(file)&&files(value.files)===files(source.files));
    check(Array.isArray(source.objects)&&Array.isArray(value.actions)&&value.actions.length<=20);
    const identities=new Set();for(const item of source.objects){check(record(item.cam)&&typeof item.cam.library_id==='string'&&Array.isArray(item.cam.records)&&item.cam.records.length>0);const key=item.cam.records[0]?.values?.LIBRF;check(typeof key==='string'&&key.length>0);identities.add(JSON.stringify([item.cam.library_id,key]));}
    const seen=new Set();let roots=0;
    for(const action of value.actions){
      check(record(action)&&typeof action.library==='string'&&typeof action.key==='string');const identity=JSON.stringify([action.library,action.key]);
      check(identities.has(identity)&&!seen.has(identity)&&['update_parent','create_draft','reuse_catalog','reuse_standard'].includes(action.mode));seen.add(identity);
      const root=action.library===source.root_library_id&&action.key===source.root_key;check(root===(action.mode==='update_parent'));if(root)roots++;
      if(action.resource_id===null)check(action.revision===null&&action.mode!=='reuse_catalog');
      else{const owner=resources.find(item=>item.id===action.resource_id);check(uuid(action.resource_id)&&revision(action.revision)&&owner?.revision===action.revision&&['update_parent','reuse_catalog'].includes(action.mode));if(root)check(action.resource_id===resource.id);
        check((owner.cam.library_id||(owner.kind==='tool'?'tool-metric':null))===action.library&&(owner.cam.values?.LIBRF||owner.cam.records?.[0]?.values?.LIBRF)===action.key);}
    }
    if(value.can_apply)check(roots===1&&seen.size===identities.size&&Object.hasOwn(value.schema_hashes,source.root_library_id));
    const refs=value.references;check(record(refs)&&Array.isArray(refs.errors)&&Array.isArray(refs.warnings)&&Number.isSafeInteger(refs.warning_rows)&&refs.warning_rows>=0&&refs.automatic_geometry_validation===false);
    const issue=item=>record(item)&&typeof item.reason==='string'&&item.reason.length>0;
    check(refs.errors.every(issue)&&refs.warnings.every(item=>issue(item)&&Number.isSafeInteger(item.affected_rows)&&item.affected_rows>=1));
    check(refs.warnings.reduce((sum,item)=>sum+item.affected_rows,0)===refs.warning_rows);
    const issueKey=item=>JSON.stringify(['library','key','field','value','target','reason'].map(key=>item[key]??null)),conflicts=new Set(value.conflicts.map(issueKey));
    check(refs.errors.every(item=>conflicts.has(issueKey(item))));
    if(Object.hasOwn(value,'column_decisions')||Object.hasOwn(value,'column_values')||Object.hasOwn(value,'column_decisions_contract')){
      check(value.column_decisions_contract===1&&Array.isArray(value.column_decisions)&&value.column_decisions.length<=100000&&record(value.column_values));const rows=new Set();
      for(const item of value.column_decisions){check(record(item)&&identities.has(JSON.stringify([item.library,item.key]))&&typeof item.block_id==='string'&&item.block_id.length>0&&typeof item.field==='string'&&/^[A-Za-z0-9_]+$/.test(item.field)&&Number.isSafeInteger(item.affected_rows)&&item.affected_rows>0&&typeof item.confirmed==='boolean');const key=coordinate(item);check(!rows.has(key));rows.add(key);
        const values=value.column_values[item.library]?.[item.key]?.[item.block_id],confirmed=NXData.record(values)&&Object.hasOwn(values,item.field);check(item.confirmed===confirmed&&item.confirmed===columnConfirmed.has(key)&&item.value===(confirmed?values[item.field]:null)&&(confirmed?item.value===columnConfirmed.get(key):true));}
      const supplied=new Map();for(const [library,objects] of Object.entries(value.column_values)){check(record(objects));for(const [objectKey,blocks] of Object.entries(objects)){check(record(blocks));for(const [block,fields] of Object.entries(blocks)){check(record(fields));for(const [field,text] of Object.entries(fields)){const key=JSON.stringify([library,objectKey,block,field]);check(typeof text==='string'&&rows.has(key));supplied.set(key,text);}}}}
      check(supplied.size===columnConfirmed.size&&[...supplied].every(([key,text])=>columnConfirmed.get(key)===text));if(value.can_apply)check(value.column_decisions.every(item=>item.confirmed));
    }else check(columnConfirmed.size===0);
    return value;
  }
  function controls(){const stale=!job||job.resource_revision!==resource?.revision;el('result-collector').disabled=busy||writerBlocked||stale;el('result-upload').disabled=busy||writerBlocked||stale;el('result-preview').disabled=busy||stale||!el('result-select').value;el('result-apply').disabled=busy||writerBlocked||!preview?.can_apply;
    el('result-columns-prev').disabled=busy||columnPage===0;el('result-columns-next').disabled=busy||(columnPage+1)*columnPageSize>=columnRows.length;el('result-columns-clear').disabled=busy||columnRows.length===0;}
  function reset(){generation++;cancelCollector();NXRequests.invalidate('results.');job=resource=preview=null;packetsById=new Map();observedContext=null;busy=false;clearColumns();['result-library','result-select','result-target'].forEach(id=>el(id).replaceChildren());invalidate();el('result-key').value='';el('result-file').value='';el('result-state').textContent='Zuerst einen gespeicherten STEP-Auftrag auswählen.';controls();}
  async function packets(current){const data=await api('geometry-jobs/'+job.id+'/prepared-results');if(current!==generation)return;packetsById=new Map(data.map(item=>[item.id,item]));options(el('result-select'),{'':'Ergebnispaket wählen',...Object.fromEntries(data.map(item=>[item.id,item.id.slice(0,8)+' · '+item.manifest.files.length+' PRTs · '+(item.applied?'bereits übernommen':'unverifizierte externe Diagnose')]))});controls();}
  async function select(value,saved){reset();job=value;resource=saved;const current=generation;
    NXRequests.invalidate('results.followup');
    const prefix=saved.kind==='tool_assembly'?'tool_assembly':'tool';options(el('result-library'),Object.fromEntries(['metric','english'].map(unit=>[prefix+'-'+unit,prefix+'-'+unit])));
    if(saved.cam.library_id)el('result-library').value=saved.cam.library_id;
    el('result-key').value=saved.cam.values?.LIBRF||saved.cam.records?.[0]?.values.LIBRF||'';
    options(el('result-target'),{'':'Zielstand wählen',...Object.fromEntries(releases.filter(r=>r.nx_version===value.nx_version&&!['failed','building'].includes(r.status)).map(r=>[r.id,r.name]))});
    observedContext=context();
    el('result-state').textContent=value.resource_revision===saved.revision?'Auftrag '+value.id+': lokalen NX-Export vorbereiten oder Ergebnispaket wählen.':'Ältere Revision: Paket nur historisch ansehen; für Übernahme einen neuen Auftrag anlegen.';await packets(current);controls();
  }
  async function task(fn){if(busy)return;const current=generation;busy=true;controls();try{await fn(current);}catch(error){if(current===generation&&!NXRequests.isStale(error))message(error.message,true);}finally{if(current===generation){busy=false;controls();}}}
  el('result-collector').onclick=()=>task(async current=>{
    const key=el('result-key').value.trim(),library=el('result-library').value,id=job?.id,revision=resource?.revision,selectedChoices=choices;
    if(!key)throw new Error('Exakte LIBRF des lokalen NX-Exports eingeben.');
    if(!id||job.resource_revision!==revision)throw new Error('Aktuellen gespeicherten STEP-Auftrag auswählen.');
    const request=NXRequests.begin('results.collector',()=>current===generation&&selectedChoices===choices&&job?.id===id&&resource?.revision===revision&&el('result-key').value.trim()===key&&el('result-library').value===library);
    const controller=new AbortController();collectorOwner=controller;
    try{
      const response=await request.wait(fetch('/api/v1/geometry-jobs/'+id+'/collector-package',{method:'POST',signal:controller.signal,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({root_library_id:library,root_key:key})}));
      if(!response.ok){const error=await request.wait(response.json());throw new Error(typeof error.detail==='string'?error.detail:JSON.stringify(error.detail));}
      const length=response.headers.get('Content-Length');
      if(typeof length!=='string'||!(/^[1-9][0-9]*$/).test(length))throw new Error('Collector-Download enthält keinen gültigen Größenbeleg.');
      const expected=NXDownload.metadata({size:Number(length),sha256:response.headers.get('X-Content-SHA256')});
      if(expected.size>NXDownload.memoryLimit)throw new Error('Collector-Download überschreitet die Grenze von 64 MiB.');
      const received=await request.wait(NXDownload.receive(response,expected,{alive:request.current}));
      request.check();const url=URL.createObjectURL(received.blob),link=document.createElement('a');
      link.href=url;link.download='NX_Result_Collector_'+id.slice(0,8)+'.zip';
      try{request.check();link.click();}finally{setTimeout(()=>URL.revokeObjectURL(url),1000);}
      message('Collector in neuen lokalen ToolJobs-Ordner entpacken und LIESMICH lesen; Empfangs-SHA und Größe bestätigt. Kein NX-/DLL-Start durch den Server.');
    }finally{controller.abort();if(collectorOwner===controller)collectorOwner=null;}});
  el('result-upload').onclick=()=>task(async current=>{if(writerBlocked)throw new Error('Verwaltungsauftrag erst klären/beobachten.');const file=el('result-file').files[0],id=job.id;if(!file||!file.size||file.size>64*1024*1024)throw new Error('prepared-result.zip mit 1 Byte bis 64 MiB auswählen.');const bytes=await file.arrayBuffer();if(current!==generation)return;
    const selectedChoices=choices,signature=JSON.stringify([el('result-select').value,el('result-target').value,el('result-key').value,el('result-library').value]);
    invalidate();await NXReleaseJobs.start('result_upload',{geometry_job_id:id,content:bytes},{completed:async operation=>{
      if(current!==generation||job?.id!==id||selectedChoices!==choices||signature!==JSON.stringify([el('result-select').value,el('result-target').value,el('result-key').value,el('result-library').value]))return;
      await packets(current);if(current!==generation||job?.id!==id||selectedChoices!==choices||signature!==JSON.stringify([el('result-select').value,el('result-target').value,el('result-key').value,el('result-library').value]))return;
      el('result-select').value=operation.domain_result.result_id;el('result-state').textContent='Paket SHA-geprüft gespeichert. Noch keine Stammdatenübernahme oder CAM-Freigabe.';controls();
    }});});
  el('result-preview').onclick=()=>task(async current=>{invalidate();const selectedChoices=choices,id=el('result-select').value,target=el('result-target').value,stamp=context();if(!id||!target)throw new Error('Paket und vollständigen Zielstand auswählen.');
    const request=NXRequests.begin('results.preview',()=>current===generation&&selectedChoices===choices&&id===el('result-select').value&&target===el('result-target').value&&stamp===context());
    el('result-state').textContent='Ergebnisvorschau wird geprüft …';
    try{const values=columnInput(),report=checkPreview(await request.wait(api('geometry-results/'+id+'/preview','POST',{target_release_id:target,...(columnConfirmed.size?{column_values:values}:{})})),id,target);
      el('result-actions').replaceChildren();report.actions.forEach(item=>{const row=document.createElement('tr');cell(row,item.library+' / '+item.key);cell(row,{update_parent:'Neue Hauptrevision (Entwurf)',create_draft:'Zugehörigen Entwurf anlegen',reuse_catalog:'Bestehende Stammdaten unverändert nutzen',reuse_standard:'Standard unverändert nutzen'}[item.mode]);cell(row,'Keine automatische Freigabe');el('result-actions').append(row);});report.conflicts.forEach(item=>{const row=document.createElement('tr');cell(row,(item.library||'Datei')+' / '+(item.key||item.path||'—'));cell(row,'Konflikt');cell(row,item.reason);el('result-actions').append(row);});
      report.files.forEach(item=>{const row=document.createElement('tr');cell(row,item.path);cell(row,item.size+' Bytes');cell(row,item.sha256);el('result-files').append(row);});
      el('result-file-state').textContent=report.files.length+' PRT-Komponenten mit unveränderten relativen Pfaden. Referenzen: '+report.references.errors.length+' Konflikte, '+report.references.warnings.length+' Warnungsgruppen ('+report.references.warning_rows+' betroffene Zeilen). Keine automatische Geometrieprüfung.';
      columnRows=report.column_decisions||[];renderColumns();preview=report;observedContext=context();el('result-state').textContent=report.can_apply?'Vorschau übernehmbar. Alle Modelle/geometrischen Angaben bleiben ungeprüft; neue Entwürfe und zurückgesetzte Geometriechecks.':report.conflicts.length+' Konflikte. Kein vorhandenes Objekt wird überschrieben.';details(report);
    }catch(error){if(request.current())invalidate('Keine vollständige aktuelle Ergebnisvorschau. Eingaben und Paket bleiben erhalten; Vorschau erneut prüfen.');throw error;}});
  el('result-apply').onclick=()=>task(async current=>{if(!preview?.can_apply)throw new Error('Erst eine aktuelle konfliktfreie Vorschau prüfen.');const report=preview;if(!confirm('Vorbereitetes Ergebnis als NEUE Entwürfe übernehmen? Zugehörige neue Halter/Schaft/Tools werden mit angelegt. ERP und NX-Ordner bleiben unverändert; alle Geometriechecks neu prüfen.'))return;
    if(writerBlocked)throw new Error('Verwaltungsauftrag erst klären/beobachten.');
    invalidate();await NXReleaseJobs.start('result_adopt',{result_id:report.result_id,target_release_id:report.target_release_id,preview_hash:report.preview_hash,...(report.column_values&&Object.keys(report.column_values).length?{column_values:report.column_values}:{})});});
  ['result-select','result-target','result-key','result-library'].forEach(id=>el(id).addEventListener('input',()=>{choices++;cancelCollector();NXRequests.invalidate('results.preview');if(['result-select','result-target'].includes(id))clearColumns();invalidate('Auswahl geändert; Ergebnisvorschau erneut prüfen.');observedContext=context();controls();}));
  el('result-columns-prev').onclick=()=>{if(!busy){columnPage--;renderColumns();}};el('result-columns-next').onclick=()=>{if(!busy){columnPage++;renderColumns();}};
  el('result-columns-clear').onclick=()=>{if(busy)return;columnConfirmed.clear();columnDrafts.clear();columnChanged();renderColumns();};
  return {select,reset,catalogChanged,releaseJobControls:value=>{writerBlocked=value;controls();}};
})();

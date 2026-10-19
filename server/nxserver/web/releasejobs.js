'use strict';
/* Background import/daily-export jobs; the durable receipt is not completion. */
globalThis.NXReleaseJobs=(()=>{
  const el=id=>document.getElementById(id),storageKey='nx.pending-release-job.v1';
  const modes=new Set(['import','derive','catalog','file_adopt','result_upload','result_adopt']),running=new Set(['queued','planning','building','verifying','registering','scanning','extracting']);
  const labels={import:'Ordnerübernahme',derive:'Stammdatenexport',catalog:'Stammdatenübernahme',file_adopt:'Kunden-Dateipaket',result_upload:'Werkzeug-Ergebnispaket',result_adopt:'Werkzeug-Ergebnisübernahme'};
  const uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
  let generation=0,supported=false,pending=null,memory=null,storageError='',owner=null,listBusy=false,watchedRunning=false,timer=null,upgradeBusy=false;
  function blocked(){return !supported||Boolean(pending)||Boolean(storageError)||Boolean(owner)||listBusy||watchedRunning||upgradeBusy;}
  function render(){
    el('release-job-recovery').hidden=!pending&&!storageError;el('release-job-key').textContent=pending?.key||'—';
    el('release-job-recovery-state').textContent=storageError||'Verwaltungsauftragsannahme ungeklärt. Fehlender Beleg beweist keinen Abbruch. Ein angenommener Auftrag wird nicht erneut ausgeführt.';
    el('release-job-receipt').disabled=!supported||Boolean(owner)||!pending;el('release-job-repeat').disabled=!supported||Boolean(owner)||!pending||!memory;
    el('release-job-fence').disabled=!supported||Boolean(owner)||(!pending&&!storageError);
    NXNative.releaseJobControls(blocked());NXExports.releaseJobControls(blocked());globalThis.NXFilePackages?.releaseJobControls(blocked());
    globalThis.NXResults?.releaseJobControls(blocked());
    // Pass only this module's state; do not feed the other module its own gate.
    NXUpgrade.sharedWriterControls(!supported||Boolean(pending)||Boolean(storageError)||Boolean(owner)||listBusy||watchedRunning);
  }
  function load(){try{const raw=sessionStorage.getItem(storageKey);if(!raw)return;const value=JSON.parse(raw);
    if(!value||Object.keys(value).sort().join(',')!=='contract_version,created_at,key,mode'||value.contract_version!==1||!uuid(value.key)||!modes.has(value.mode)||!Number.isFinite(Date.parse(value.created_at)))throw new Error('Ungültiger lokaler Ordnerauftragshinweis.');
    pending=value;
  }catch(error){storageError='Lokaler Ordnerauftragshinweis nicht lesbar. Neue Starts gesperrt: '+error.message;}}
  function clear(){sessionStorage.removeItem(storageKey);pending=null;memory=null;storageError='';render();}
  function validate(ticket,job,action){if(!job||!uuid(job.id)||job.mode!==ticket.mode||action&&action!=='migration.'+ticket.mode)throw new Error('Beleg passt nicht zum ursprünglichen Ordnerauftrag.');}
  async function send(ticket,payload,current){try{
      if(ticket.mode!=='result_upload')return await api('release-jobs/'+ticket.mode,'POST',payload,{'Idempotency-Key':ticket.key});
      const response=await fetch('/api/v1/release-jobs/result_upload?geometry_job_id='+encodeURIComponent(payload.geometry_job_id),{
        method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/zip','Idempotency-Key':ticket.key},body:payload.content});
      const result=await response.json();if(!response.ok){const error=new Error(result.detail||'Ergebnispaket-Annahme fehlgeschlagen.');error.httpStatus=response.status;throw error;}return result;
    }
    catch(error){if(current===generation&&pending?.key===ticket.key&&[400,422].includes(error.httpStatus)){
      const fence=await api('migration-requests/'+ticket.key+'/cancel','POST');if(current!==generation)throw error;
      if(fence.contract_version!==1||fence.request_key!==ticket.key)throw new Error('Ungültiger Sperrbeleg.');
      if(fence.state==='cancelled')clear();else if(fence.state==='accepted'){validate(ticket,fence.result,fence.action);return fence.result;}
    }throw error;}}
  async function accepted(ticket,job,current){
    if(current!==generation||pending?.key!==ticket.key)return;validate(ticket,job);
    const context=memory?.generation===current?memory.context:null;clear();
    message('Verwaltungsauftrag angenommen: '+job.id+'. Erfolg/Fehler werden separat geprüft; keine automatische Freigabe.');
    await watch(job.id,current,context);
  }
  async function operation(fn){if(owner)return;const own={generation};owner=own;render();
    try{return await fn(own.generation);}catch(error){if(own.generation===generation&&!NXRequests.isStale(error))message((pending?'Verwaltungsauftragsannahme ungeklärt: ':'')+error.message+(pending?' Unter „Stammdaten verwalten“ → „Übernahmen und Stammdatenaufträge“ klären.':''),true);}
    finally{if(owner===own){owner=null;render();}}
  }
  function start(mode,payload,context){return operation(async current=>{
    if(!supported||!modes.has(mode))throw new Error('Server unterstützt keine Hintergrund-Ordneraufträge. Server aktualisieren.');
    if(pending||storageError)throw new Error('Unbestätigten Verwaltungsauftrag zuerst klären.');
    const ticket={contract_version:1,key:crypto.randomUUID(),mode,created_at:new Date().toISOString()};
    sessionStorage.setItem(storageKey,JSON.stringify(ticket));pending=ticket;memory={generation:current,payload:structuredClone(payload),context};render();
    await accepted(ticket,await send(ticket,memory.payload,current),current);
  });}
  function recover(mode){return operation(async current=>{
    if(!supported)throw new Error('Server unterstützt diesen Wiederanlaufvertrag nicht.');
    if(storageError&&!pending){if(!confirm('Nur unlesbaren Browserhinweis verwerfen? Frühere Auftragsliste/Audit prüfen. Keine Serverdaten werden gelöscht.'))return;clear();message('Nur lokaler Hinweis verworfen. Vor erneutem Start bestehende Ordneraufträge prüfen.');return;}
    const ticket=pending;if(!ticket)throw new Error('Kein ungeklärter Ordnerauftrag.');
    if(mode==='repeat'){if(!memory)throw new Error('Originaleingaben nicht mehr im Speicher. Nur Beleg prüfen oder vor Annahme sperren.');await accepted(ticket,await send(ticket,memory.payload,current),current);return;}
    if(mode==='fence'&&!confirm('Versuch nur VOR Annahme sperren? War er bereits angenommen, wird weiter beobachtet. Kein Worker-Abbruch oder Löschen einer Kopie.'))return;
    const value=await api('migration-requests/'+ticket.key+(mode==='fence'?'/cancel':''),mode==='fence'?'POST':'GET');
    if(current!==generation||pending?.key!==ticket.key)return;
    if(value.contract_version!==1||value.request_key!==ticket.key)throw new Error('Ungültiger Ordnerannahmebeleg.');
    if(value.state==='accepted'){validate(ticket,value.result,value.action);await accepted(ticket,value.result,current);}
    else if(value.state==='cancelled'){clear();message('Versuch vor Annahme gesperrt. Eingaben, bestehende Aufträge und Ordner bleiben erhalten.');}
    else throw new Error('Kein terminaler Annahmebeleg. Kennung beibehalten.');
  });}
  async function refresh(alive=()=>true){const current=generation,request=NXRequests.begin('releasejobs.list',()=>current===generation&&alive());
    const jobs=await request.wait(api('migrations'));listBusy=jobs.some(job=>running.has(job.status));render();el('release-job-list').replaceChildren();
    jobs.filter(job=>modes.has(job.mode)).forEach(job=>{const row=document.createElement('tr');cell(row,job.id.slice(0,8)+' · '+labels[job.mode]);cell(row,job.request.name||job.request.release_id||job.request.result_id||job.request.geometry_job_id);cell(row,NXJobState.label(job));cell(row,job.message);
      const actions=cell(row,'');actions.append(button('Details',()=>details(job)));
      if(job.report_sha256)actions.append(button('Bericht',async()=>details(await api('migrations/'+job.id+'/report'))));
      if(['file_adopt','result_adopt'].includes(job.mode)&&job.domain_result?.resources.length)actions.append(button('Aktuellen Datensatz öffnen',()=>{const resource=resources.find(item=>item.id===job.domain_result.resources[0].id);if(!resource)throw new Error('Aktuellen Bestand erst aktualisieren.');openEditor(resource);}));
      actions.append(button('Status verfolgen',()=>watch(job.id,generation,null)));el('release-job-list').append(row);});
  }
  async function watch(id,current,context,request=null){request??=NXRequests.begin('releasejobs.watch',()=>current===generation);
    if(!request.current())return;clearTimeout(timer);
    try{const job=await request.wait(api('migrations/'+id));if(!modes.has(job.mode))throw new Error('Kein unterstützter Ordnerauftrag.');
      watchedRunning=running.has(job.status);render();el('release-job-state').textContent=NXJobState.label(job)+' · '+job.message;
      if(watchedRunning){timer=setTimeout(()=>watch(id,current,context,request),1000);return;}
      await request.wait(NXRequests.settle(window.refresh()));
      if(job.status==='completed'&&['catalog','file_adopt','result_upload','result_adopt'].includes(job.mode)){
        if(job.domain_result?.contract_version!==1||job.domain_result.mode!==job.mode||!Array.isArray(job.domain_result.resources))throw new Error('Kein gebundenes Übernahmeergebnis.');
        details(job);if(context?.completed)await context.completed(job);
        const text=job.mode==='result_upload'?'Paket SHA-geprüft gespeichert. Noch keine Stammdatenübernahme oder CAM-Freigabe.':job.mode==='result_adopt'?job.domain_result.resources.length+' neue/aktualisierte Entwürfe gespeichert. CAM-/Geometrieprüfung und neuer NX-Export weiterhin erforderlich.':job.mode==='catalog'?'Stammdaten übernommen: '+job.domain_result.resources.length+' Entwürfe. Werkzeuge brauchen noch CAM-/3D-Prüfung.':'Dateipaket als Entwurf übernommen. Dateihashes und vier NX-Prüfungen vor Freigabe neu bestätigen; geänderte Editorwerte bleiben erhalten.';
        message(text);
      }else if(job.status==='completed'&&uuid(job.release_id)){
        details(job);const focus=context?.completed?context.completed(job):true;
        if(focus)showWorkspace('manage','release-title');
        message(job.mode==='import'?'Neuer Ordner erstellt; noch nicht fachlich freigegeben. Test-Starter im neuen Datenstand verwenden.':'Neuer NX-Datenstand aus geprüfter Exportvorschau erstellt. NX-Test und fachliche Abnahme stehen noch aus.');
      }else message('Verwaltungsauftrag '+job.status+': '+job.message+'. '+(job.domain_result?job.domain_result.resources.length+' Entwurfsrevision(en) bereits committed; Ergebniszuordnung dauerhaft erhalten. ':'')+(job.asset_allocations?.length?job.asset_allocations.length+' Anhangs-Allokation(en) als Diagnose erhalten. ':'')+'Teilstände bleiben erhalten; kein automatischer Neustart.',true);
    }catch(error){if(!request.current()||NXRequests.isStale(error))return;el('release-job-state').textContent='Statusabfrage fehlgeschlagen: '+error.message+'. Kein neuer Auftrag; weiter beobachten.';timer=setTimeout(()=>watch(id,current,context,request),3000);}
  }
  function reset(){generation++;NXRequests.invalidate('releasejobs.');clearTimeout(timer);timer=null;owner=null;supported=false;listBusy=false;watchedRunning=false;el('release-job-list').replaceChildren();el('release-job-state').textContent='';render();}
  el('release-job-receipt').onclick=()=>recover('receipt');el('release-job-repeat').onclick=()=>recover('repeat');el('release-job-fence').onclick=()=>recover('fence');el('release-job-refresh').onclick=()=>action(refresh)();
  load();render();return {start,refresh,reset,blocked,configure:value=>{const cap=value.release_jobs;supported=cap?.contract_version===1&&[...modes].every(mode=>cap.modes?.includes(mode))&&cap.atomic_adoption_result===true&&cap.durable_attachment_intents===true;render();},
    upgradeWriterControls:value=>{if(upgradeBusy!==value){upgradeBusy=value;render();}}};
})();

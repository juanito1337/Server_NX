'use strict';
/* Offline jobs download no API token/credentials. Never executes a DLL or NX. */
globalThis.NXGeometry = (() => {
  const el = id => document.getElementById(id);
  let selected = null, selectedJob = null, generation=0, selectedSignature=null, createOwner=null,reportOwner=null;
  const storageKey='nx.pending-geometry-request.v1',uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
  let supported=false,ticket=null,memory=null,storageError='',knownCommit=null;
  const run = fn => () => action(fn)();
  function controls(){el('step-preview').disabled=!selected||!el('step-asset').value;el('step-job-create').disabled=!supported||Boolean(createOwner)||Boolean(ticket)||Boolean(storageError)||!selected||!el('step-asset').value;el('geometry-report-upload').disabled=Boolean(reportOwner)||!selectedJob||selected?.revision!==selectedJob.resource_revision;
    el('step-recovery-box').hidden=!ticket&&!storageError;el('step-request-key').textContent=ticket?.key||'—';
    el('step-recovery-state').textContent=storageError||(knownCommit?'Auftrag committed. Paket separat prüfen; niemals eine zweite Kopie automatisch erstellen.':'STEP-Auftrag noch nicht bestätigt. Beleg prüfen, exakt wiederholen oder offenen Commit sperren. Keine automatische NX-Ausführung.');
    el('step-receipt').disabled=!supported||Boolean(createOwner)||!ticket;el('step-repeat').disabled=!supported||Boolean(createOwner)||!ticket||!memory;
    el('step-fence').disabled=!supported||Boolean(createOwner)||(!ticket&&!storageError);el('step-ack').disabled=!supported||Boolean(createOwner)||!ticket||!knownCommit;}
  function loadTicket(){try{const raw=sessionStorage.getItem(storageKey);if(!raw)return;const value=JSON.parse(raw);
    if(!value||Object.keys(value).sort().join(',')!=='contract_version,created_at,key'||value.contract_version!==1||!uuid(value.key)||!Number.isFinite(Date.parse(value.created_at)))throw new Error('Ungültiger STEP-Auftragshinweis.');ticket=value;
  }catch(error){storageError='Lokaler STEP-Auftragshinweis unlesbar. Neue Aufträge gesperrt: '+error.message;}}
  function clearTicket(){sessionStorage.removeItem(storageKey);ticket=null;memory=null;storageError='';knownCommit=null;controls();}
  async function operation(fn){if(createOwner)return;const owner={session:connectionGeneration};createOwner=owner;controls();
    try{await fn(owner.session);}catch(error){if(owner.session===connectionGeneration&&!NXRequests.isStale(error))message((ticket?'STEP-Auftrag noch ungeklärt: ':'')+error.message,true);}
    finally{if(createOwner===owner){createOwner=null;controls();}}}
  async function confirmed(own,job,session){if(session!==connectionGeneration||ticket?.key!==own.key)return;
    if(!job||!uuid(job.id)||!uuid(job.resource_id)||!Number.isInteger(job.resource_revision)||job.status!=='awaiting_nx'||!(/^[a-f0-9]{64}$/).test(job.package_sha256)||!Number.isInteger(job.package_size)||job.package_size<=0)throw new Error('Ungültiger STEP-Auftragsbeleg.');
    knownCommit=job;controls();const verified=await api('geometry-jobs/'+job.id+'/package-metadata');
    if(session!==connectionGeneration||ticket?.key!==own.key)return;
    if(verified.job_id!==job.id||verified.sha256!==job.package_sha256||verified.size!==job.package_size||verified.files_verified!==true)throw new Error('STEP-Paketverifikation passt nicht zum ursprünglichen Beleg.');
    const same=memory?.generation===generation&&selected?.id===job.resource_id&&selected?.revision===job.resource_revision&&el('step-asset').value===memory?.body.asset_id&&el('step-tool-type').value===memory?.body.tool_type&&el('step-nx-version').value===(memory?.body.nx_version||'2512');
    clearTicket();let refreshError='';if(selected?.id===job.resource_id){try{await loadJobs();}catch(error){if(!NXRequests.isStale(error))refreshError=' Auftragsbestand manuell aktualisieren: '+error.message;}}
    if(session!==connectionGeneration)return;details(job);message('Auftrag registriert; Importpaket SHA-256-geprüft. '+(same?'Importpaket herunterladen; Werkzeug bleibt Entwurf.':'Aktuelle Auswahl und Eingaben bleiben erhalten. Ursprünglichen Auftrag im Auftragsbestand auswählen.')+' Kein NX-Import, CAM-Export oder fachlicher Nachweis ausgeführt.'+refreshError,Boolean(refreshError));
  }
  async function send(own,body,session){try{return await api('geometry-jobs','POST',body,{'Idempotency-Key':own.key});}
    catch(error){if(session===connectionGeneration&&ticket?.key===own.key&&[400,422].includes(error.httpStatus)){
      const value=await api('geometry-requests/'+own.key+'/cancel','POST');if(session!==connectionGeneration)throw error;
      if(value.contract_version!==1||value.request_key!==own.key)throw new Error('Ungültiger STEP-Sperrbeleg.');
      if(value.state==='cancelled')clearTicket();else if(value.state==='committed')return value.result;
    }throw error;}}
  const recover=mode=>operation(async session=>{
    if(!supported)throw new Error('Server unterstützt keine dauerhaften STEP-Auftragsbelege.');
    if(storageError&&!ticket){if(!confirm('Nur unlesbaren lokalen Hinweis verwerfen? Auftragsbestand/Audit prüfen. Keine Serverdaten oder Teilpakete löschen.'))return;clearTicket();message('Nur lokalen STEP-Hinweis verworfen. Serveraufträge bleiben erhalten.');return;}
    const own=ticket;if(!own)throw new Error('Kein ungeklärter STEP-Auftrag.');
    if(mode==='ack'){if(!knownCommit)throw new Error('Kein bestätigter Auftragscommit.');if(!confirm('Nur registrierten Auftragsbeleg anerkennen, OHNE Paket-/NX-Freigabe? Fehlende/veränderte Dateien bleiben ein Integritätsproblem; nichts löschen.'))return;clearTicket();message('Auftragscommit anerkannt, keine Paket-/NX-Freigabe. Integritätsproblem separat klären.');return;}
    if(mode==='repeat'){if(!memory)throw new Error('Nach Reload nur Belegprüfung oder Commit-Sperre; keine rekonstruierten Auftragsdaten.');await confirmed(own,await send(own,memory.body,session),session);return;}
    if(mode==='fence'&&!confirm('Noch nicht registrierten Auftrag sperren? Ein committed Auftrag bleibt erhalten; keine Teilpakete löschen und keinen NX-Prozess stoppen.'))return;
    const value=await api('geometry-requests/'+own.key+(mode==='fence'?'/cancel':''),mode==='fence'?'POST':'GET');
    if(session!==connectionGeneration||ticket?.key!==own.key)return;
    if(value.contract_version!==1||value.request_key!==own.key)throw new Error('Ungültiger STEP-Auftragsvertrag.');
    if(value.state==='committed')await confirmed(own,value.result,session);
    else if(value.state==='cancelled'){clearTicket();message('STEP-Auftragscommit gesperrt; keine Aufträge/Teilpakete gelöscht. Eingaben bleiben erhalten.');}
    else{details(value);throw new Error('STEP-Auftrag '+value.state+': Teilabsicht diagnostisch erhalten. Vor einem neuen bewussten Versuch Commit sperren; keine automatische Paketüberschreibung.');}
  });
  async function loadAttachments(alive=()=>true,preserve=false) {
    const identifier=el('step-resource').value;
    const resource = resources.find(r => r.id === identifier) || null;
    const signature=JSON.stringify(resource?[resource.id,resource.revision,resource.asset_ids]:null);
    if(!preserve||signature!==selectedSignature){generation++;NXRequests.invalidate('geometry.');selected=selectedJob=null;createOwner=reportOwner=null;
      checkFileDownloadContext();
      globalThis.NXResults?.reset();el('geometry-report-state').textContent='';el('geometry-report-file').value='';renderReport(null);controls();}
    const current=generation,request=NXRequests.begin('geometry.attachments',()=>current===generation&&identifier===el('step-resource').value&&alive());
    const attachments = await request.wait(resource ? Promise.all((resource.asset_ids || []).map(id => api('assets/' + id + '/metadata'))) : Promise.resolve([]));
    selected=resource;
    selectedSignature=signature;
    options(el('step-asset'), Object.fromEntries(attachments.filter(a => /\.(step|stp|p21)$/i.test(a.filename)).map(a => [a.id, a.filename])));
    controls();await request.wait(loadJobs(alive));
  }
  async function loadJobs(alive=()=>true) {
    const current=generation,resource=selected;
    if (!resource){el('geometry-jobs').replaceChildren();return;}
    const request=NXRequests.begin('geometry.jobs',()=>current===generation&&selected?.id===resource.id&&selected?.revision===resource.revision&&alive());
    const jobs = await request.wait(api('geometry-jobs?resource_id=' + encodeURIComponent(resource.id)));
    if(selectedJob)selectedJob=jobs.find(job=>job.id===selectedJob.id)||null;
    el('geometry-jobs').replaceChildren();
    jobs.forEach(job => {
      const tr = document.createElement('tr');
      cell(tr, job.id.slice(0, 8) + ' / Rev. ' + job.resource_revision);
      cell(tr, job.input.filename);const statuses={awaiting_nx:'Wartet auf NX-Import',needs_selection:'Fachliche Auswahl erforderlich',needs_structure:'Baugruppe / Geometrie prüfen',failed:'Import fehlgeschlagen'};cell(tr,(statuses[job.status]||job.status)+' ('+job.status+')'+(job.resource_revision!==resource.revision?' · ältere Stammdatenrevision':''));
      const td = cell(tr, '');
      td.append(button('Importpaket herunterladen', () => download(job)),button('Ergebnispaket',()=>NXResults.select(job,resource)), button('Bericht zuordnen', () => {
        NXRequests.invalidate('geometry.report.');reportOwner=null;
        selectedJob = job;
        const stale=job.resource_revision!==resource.revision;
        el('geometry-report-upload').disabled=stale;
        el('geometry-report-state').textContent = stale?'Dieser Auftrag gehört zu einer älteren Revision. Für die aktuellen Stammdaten einen neuen Auftrag anlegen.':'Bericht für Auftrag ' + job.id + ' wählen.';
        renderReport(job.report);
        details(job);
        controls();
      }), button('Details', () => details(job)));
      el('geometry-jobs').append(tr);
    });
  }
  function renderReport(report) {
    el('geometry-report-parts').replaceChildren();el('geometry-report-summary').textContent='';if(!report)return;
    const count=report.parts.reduce((sum,part)=>sum+part.bodies.length,0);
    el('geometry-report-summary').textContent=`Externe Diagnose: ${report.parts.length} Teile, ${count} Körper, ${report.components.length} Komponenten, ${report.errors.length} Fehler, ${report.warnings.length} Warnungen. Unverifizierter Bericht; keine CAM-/Kollisionsfreigabe und keine automatische Modellübernahme.`;
    report.parts.slice(0,100).forEach(part=>{const row=document.createElement('tr');cell(row,part.path);cell(row,part.units);cell(row,part.fully_loaded?'Vollständig geladen':'Unvollständig');cell(row,part.bodies.length+' Körper / '+part.bodies.filter(body=>body.solid).length+' solide');el('geometry-report-parts').append(row);});
  }
  async function download(job) {
    const current=generation,id=selected?.id,revision=selected?.revision,notice=noticeGeneration;
    await downloadFile('geometry-jobs/'+job.id+'/package','NX_STEP_'+job.id+'.zip',{sha256:job.package_sha256,size:job.package_size,current:()=>current===generation&&id===selected?.id&&revision===selected?.revision});
    if(notice===noticeGeneration)message('Paket an Browser übergeben; LIESMICH.txt lesen. NX wird nicht automatisch gestartet.');
  }
  el('step-resource').onchange = run(()=>{NXRequests.invalidate('results.followup');return loadAttachments();});
  el('step-asset').onchange=()=>{NXRequests.invalidate('geometry.preview');controls();};
  el('step-preview').onclick = run(async () => {
    if (!el('step-asset').value) throw new Error('Gespeichertes Werkzeug mit STEP-Anhang wählen.');
    const current=generation,id=el('step-asset').value,request=NXRequests.begin('geometry.preview',()=>current===generation&&id===el('step-asset').value);
    details(await request.wait(api('assets/' + id + '/step-preview')));
    message('STEP-Text geprüft. Einheiten, Körper und CAM-Eignung sind dadurch nicht bestätigt.');
  });
  el('step-job-create').onclick = () => operation(async session => {
    if(!supported||ticket||storageError)throw new Error('Unbestätigten STEP-Auftrag erst klären oder Server aktualisieren.');
    if (!selected || !el('step-asset').value) throw new Error('Gespeicherten Werkzeugentwurf mit STEP-Anhang wählen.');
    const body={resource_id:selected.id,expected_revision:selected.revision,asset_id:el('step-asset').value,tool_type:el('step-tool-type').value,nx_version:el('step-nx-version').value};
    const own={contract_version:1,key:crypto.randomUUID(),created_at:new Date().toISOString()};sessionStorage.setItem(storageKey,JSON.stringify(own));ticket=own;memory={generation,body};controls();
    await confirmed(own,await send(own,body,session),session);
  });
  el('step-receipt').onclick=()=>recover('receipt');el('step-repeat').onclick=()=>recover('repeat');el('step-fence').onclick=()=>recover('fence');el('step-ack').onclick=()=>recover('ack');
  el('geometry-report-upload').onclick = run(async () => {
    if(reportOwner)return;
    if (!selectedJob) throw new Error('Zuerst beim Auftrag „Bericht zuordnen“ wählen.');
    const job=selectedJob,current=generation;
    if(!selected||selected.revision!==job.resource_revision)throw new Error('Auftrag gehört zu einer älteren Revision. Einen neuen Auftrag anlegen.');
    const file = el('geometry-report-file').files[0];
    if (!file || file.size > 1024 * 1024) throw new Error('output/report.json mit maximal 1 MB wählen.');
    const request=NXRequests.begin('geometry.report.write',()=>current===generation&&selectedJob?.id===job.id);reportOwner=request;controls();
    try{const report=JSON.parse(await request.wait(file.text()));
      const result = await request.wait(api('geometry-jobs/' + job.id + '/report', 'POST', {report}));
      details(result);renderReport(result.report);await request.wait(loadJobs());
      el('geometry-report-state').textContent = 'Diagnose zugeordnet. Keine automatische Übernahme/Freigabe von CAM-Daten oder PRT-Dateien.';
    }finally{if(reportOwner===request){reportOwner=null;controls();}}
  });
  loadTicket();controls();return {
    configure(capabilities){options(el('step-tool-type'),capabilities.geometry_preparation.tool_types);supported=capabilities.geometry_requests?.contract_version===1&&capabilities.geometry_requests.durable_package_intents===true&&capabilities.geometry_requests.atomic_job_receipt===true;controls();},
    async refresh(capabilities,alive=()=>true) {
      if (capabilities) options(el('step-tool-type'), capabilities.geometry_preparation.tool_types);
      options(el('step-resource'), Object.fromEntries(resources.filter(r => ['tool', 'tool_assembly'].includes(r.kind) && r.cam_status === 'draft').map(r => [r.id, r.name])));
      await loadAttachments(alive,true);
    },
    reset() { generation++;NXRequests.invalidate('geometry.');selectedSignature=null;createOwner=reportOwner=null;selected = selectedJob = null;globalThis.NXResults?.reset(); el('geometry-jobs').replaceChildren(); el('step-resource').replaceChildren(); el('step-asset').replaceChildren(); el('geometry-report-file').value = ''; el('geometry-report-state').textContent = '';renderReport(null);controls(); },
    connectionChanged(){supported=false;createOwner=null;controls();}
  };
})();

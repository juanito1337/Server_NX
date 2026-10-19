'use strict';
globalThis.NXBackups=(()=>{
  const el=id=>document.getElementById(id);
  let generation=0,timer=null,busy=false,submitting=false,preview=null,abort=null,writable=null,downloadOwner=null;
  const storageKey='nx.pending-backup-request.v1',modes=['backup','verify','preview','restore'],uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
  let supported=false,ticket=null,memory=null,storageError='',owner=null,selectionVersion=0;
  function body(){return {target:el('recovery-root').value,name:el('recovery-name').value.trim()};}
  function selection(){return JSON.stringify([el('recovery-backup').value,body()]);}
  function controls(){const paused=!supported||busy||Boolean(ticket)||Boolean(storageError)||Boolean(owner);el('backup-create').disabled=paused;el('recovery-preview').disabled=paused||!el('recovery-backup').value;el('recovery-create').disabled=paused||!preview?.report.can_restore||!el('recovery-unsigned').checked;
    el('backup-request-box').hidden=!ticket&&!storageError;el('backup-request-key').textContent=ticket?.key||'—';
    el('backup-request-state').textContent=storageError||(ticket?'Unbestätigte Annahme: '+ticket.mode+'. Beleg prüfen, identisch wiederholen oder nur VOR Annahme sperren. Ein angenommener Worker wird nie gestoppt/neu gestartet.':'');
    el('backup-request-receipt').disabled=!supported||Boolean(owner)||!ticket;el('backup-request-repeat').disabled=!supported||Boolean(owner)||!ticket||!memory;
    el('backup-request-fence').disabled=!supported||Boolean(owner)||(!ticket&&!storageError);}
  function loadTicket(){try{const raw=sessionStorage.getItem(storageKey);if(!raw)return;const value=JSON.parse(raw);
    if(!value||Object.keys(value).sort().join(',')!=='contract_version,created_at,key,mode'||value.contract_version!==1||!uuid(value.key)||!modes.includes(value.mode)||!Number.isFinite(Date.parse(value.created_at)))throw new Error('Ungültiger Sicherungs-Auftragshinweis.');ticket=value;
  }catch(error){storageError='Lokaler Sicherungshinweis unlesbar. Neue Aufträge gesperrt: '+error.message;}}
  function clearTicket(){sessionStorage.removeItem(storageKey);ticket=null;memory=null;storageError='';controls();}
  async function operation(fn){if(owner)return;const own={session:connectionGeneration};owner=own;controls();
    try{await fn(own.session);}catch(error){if(own.session===connectionGeneration&&!NXRequests.isStale(error))message((ticket?'Sicherungsauftrag noch ungeklärt: ':'')+error.message,true);}
    finally{if(owner===own){owner=null;submitting=false;controls();}}}
  async function accepted(own,job,session){if(session!==connectionGeneration||ticket?.key!==own.key)return;
    if(!job||!uuid(job.id)||job.request_key!==own.key||job.mode!==own.mode||job.status!=='running'||job.phase!=='queued')throw new Error('Ungültiger Sicherungs-Annahmebeleg.');
    const context=memory,current=generation,snapshot=context?.generation===current&&context.version===selectionVersion?context.snapshot:null;
    clearTicket();submitting=false;busy=true;controls();await watch(job.id,current,snapshot);
  }
  async function send(own,context,session){try{return await api(context.path,'POST',context.data,{'Idempotency-Key':own.key});}
    catch(error){if(session===connectionGeneration&&ticket?.key===own.key&&[400,422].includes(error.httpStatus)){
      const value=await api('backup-requests/'+own.key+'/cancel','POST');if(session!==connectionGeneration)throw error;
      if(value.contract_version!==1||value.request_key!==own.key)throw new Error('Ungültiger Sicherungs-Sperrbeleg.');
      if(value.state==='cancelled')clearTicket();else if(value.state==='accepted')return value.result;
    }throw error;}}
  const recover=mode=>operation(async session=>{
    if(!supported)throw new Error('Server unterstützt keine dauerhaften Sicherungs-Annahmebelege.');
    if(storageError&&!ticket){if(!confirm('Nur unlesbaren lokalen Hinweis verwerfen? Sicherungsaufträge/Audit prüfen. Keine Serverdaten löschen.'))return;clearTicket();message('Nur lokalen Sicherungshinweis verworfen. Aufträge/Teilordner bleiben erhalten.');return;}
    const own=ticket;if(!own)throw new Error('Kein ungeklärter Sicherungsauftrag.');
    if(mode==='repeat'){if(!memory)throw new Error('Nach Reload nur Belegprüfung/Sperre; keine rekonstruierten Zielentscheidungen.');await accepted(own,await send(own,memory,session),session);return;}
    if(mode==='fence'&&!confirm('Nur noch NICHT angenommene Kennung sperren? Ein angenommener Worker und Teilstände bleiben erhalten, kein Stop/Neustart.'))return;
    const value=await api('backup-requests/'+own.key+(mode==='fence'?'/cancel':''),mode==='fence'?'POST':'GET');
    if(session!==connectionGeneration||ticket?.key!==own.key)return;
    if(value.contract_version!==1||value.request_key!==own.key)throw new Error('Ungültiger Sicherungs-Annahmevertrag.');
    if(value.state==='accepted')await accepted(own,value.result,session);
    else if(value.state==='cancelled'){clearTicket();message('Sicherungsanfrage VOR Annahme gesperrt; keine Daten/Worker gelöscht oder gestoppt.');}
    else throw new Error('Unbekannter Sicherungs-Annahmezustand.');
  });
  function invalidate(){selectionVersion++;preview=null;el('recovery-state').textContent='Archiv und neues Ziel erneut prüfen.';controls();}
  function reset(){generation++;selectionVersion++;supported=false;owner=null;downloadOwner=null;clearTimeout(timer);timer=null;abort?.abort();abort=null;writable?.abort().catch(()=>{});writable=null;busy=submitting=false;preview=null;
    ['backup-snapshots','backup-jobs','recovery-backup','recovery-root'].forEach(id=>el(id).replaceChildren());el('backup-state').textContent='';el('recovery-state').textContent='';el('backup-legacy').checked=false;el('recovery-unsigned').checked=false;el('recovery-name').value='R_Test';controls();}
  async function refresh(alive=()=>true){const current=generation,request=NXRequests.begin('backups.list',()=>current===generation&&alive()),data=await request.wait(api('backups'));
    busy=data.busy||submitting;const previous=selection();options(el('recovery-backup'),{'':'Sicherung auswählen',...Object.fromEntries(data.snapshots.filter(item=>item.status==='ready').map(item=>[item.id,item.created_at+' · '+item.files+' Dateien']))});options(el('recovery-root'),data.restore_roots);if(previous!==selection())invalidate();
    el('backup-snapshots').replaceChildren();data.snapshots.forEach(item=>{const row=document.createElement('tr');cell(row,item.id?item.created_at+' · '+item.id.slice(0,8):'Ungültiger Beleg');cell(row,item.status==='ready'?`${item.files} Dateien · ${item.logical_bytes} Bytes · ${item.warning_count} Hinweise · unsigniert`:item.message);const actions=cell(row,'');if(item.status==='ready'){actions.append(button('Archiv prüfen',()=>start('verify',item.id)),button('Archiv herunterladen',()=>download(item)),button('Prüfsummenbeleg herunterladen',()=>receipt(item.id)));}el('backup-snapshots').append(row);});
    el('backup-jobs').replaceChildren();data.jobs.forEach(job=>{const row=document.createElement('tr');cell(row,job.id.slice(0,8)+' · '+job.mode);cell(row,job.status+' / '+job.phase+' · '+job.message);const actions=cell(row,'');actions.append(button('Details',()=>details(job)));if(job.report_sha256)actions.append(button('Bericht',async()=>details(await api('backup-jobs/'+job.id+'/report'))));if(job.status==='running')actions.append(button('Weiter beobachten',()=>watch(job.id,generation,null)));el('backup-jobs').append(row);});controls();
  }
  async function watch(id,current,snapshot,request=null){request??=NXRequests.begin('backups.watch',()=>current===generation);if(!request.current())return;clearTimeout(timer);try{const job=await request.wait(api('backup-jobs/'+id));el('backup-state').textContent=job.status+' / '+job.phase+' · '+job.message;
    if(job.status==='running'){timer=setTimeout(()=>watch(id,current,snapshot,request),1000);return;}
    busy=false;await request.wait(NXRequests.settle(refresh()));
    if(job.status==='completed'){const report=await request.wait(api('backup-jobs/'+id+'/report'));details(report);
      if(job.mode==='preview'){if(snapshot&&snapshot.value===selection()&&snapshot.version===selectionVersion){preview={id,report,snapshot:snapshot.value};el('recovery-state').textContent=report.can_restore?`Geprüftes neues Ziel: ${report.target_folder} · ${report.files} Dateien. Keine Aktivierung; neue NX-Abnahme erforderlich.`:report.errors.join(' · ');}else el('recovery-state').textContent='Auswahl geändert. Historischer Bericht ist keine aktuelle Wiederherstellungsfreigabe.';}
      else if(job.mode==='restore')el('recovery-state').textContent=`Isolierter Bestand erstellt: ${report.target_folder}. Konfiguration: ${report.settings_file}. Nicht aktiviert; NX-Installation konfigurieren und neu testen.`;
      else el('backup-state').textContent=job.mode==='backup'?'Sicherung vollständig erstellt. Archiv und Prüfsummenbeleg auf einem separaten Datenträger aufbewahren.':'Archiv vollständig geprüft; keine Echtheitsbestätigung oder NX-Freigabe.';
    }else el('backup-state').textContent='Operation beendet: '+job.message+'. Teilstände bleiben erhalten; kein automatischer Neustart.';controls();
  }catch(error){if(!request.current()||NXRequests.isStale(error))return;el('backup-state').textContent='Abfrage fehlgeschlagen: '+error.message+'. Derselbe Auftrag wird weiter beobachtet; kein Neustart.';timer=setTimeout(()=>watch(id,current,snapshot,request),3000);}}
  async function start(mode,backupId){if(owner||busy)return;if(ticket||storageError||!supported)throw new Error('Unbestätigte Sicherungsanfrage erst klären oder Server aktualisieren.');const current=generation,selected=backupId||el('recovery-backup').value;let snapshot={value:selection(),version:selectionVersion},path,data;
    if(mode==='backup'){if(!confirm('Verwaltungsdaten und alle verwalteten Dateien gemeinsam sichern? Schreibaktionen sind währenddessen pausiert. Originalquellen, NX-Installation und Zugangsschlüssel werden nicht gesichert.'))return;path='backups';data={accept_legacy_unbound:el('backup-legacy').checked};}
    else if(mode==='verify'){path='backups/'+selected+'/verify';data={};}
    else if(mode==='preview'){if(!selected)throw new Error('Sicherung auswählen.');path='backups/'+selected+'/restore-preview';data=body();invalidate();snapshot={value:selection(),version:selectionVersion};}
    else{if(!preview?.report.can_restore||preview.snapshot!==snapshot.value||!el('recovery-unsigned').checked)throw new Error('Zuerst passende Wiederherstellungsvorschau prüfen und unsignierten Stand ausdrücklich akzeptieren.');if(!confirm('Wiederherstellung ausschließlich im NEUEN Ordner '+preview.report.target_folder+'? Keine automatische Freigabe oder Aktivierung; alter Datenbestand bleibt bestehen.'))return;path='backups/'+selected+'/restore';data={...body(),preview_id:preview.id,preview_hash:preview.report.preview_hash,accept_unsigned:true};preview=null;}
    return operation(async session=>{const own={contract_version:1,key:crypto.randomUUID(),mode,created_at:new Date().toISOString()};sessionStorage.setItem(storageKey,JSON.stringify(own));ticket=own;memory={path,data,generation:current,snapshot,version:selectionVersion};submitting=true;NXRequests.invalidate('backups.list');controls();await accepted(own,await send(own,memory,session),session);});
  }
  function save(content,name){const url=URL.createObjectURL(new Blob([content],{type:'application/octet-stream'})),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  async function receipt(id){const current=generation,value=await api('backups/'+id+'/receipt');if(current!==generation)return;save(JSON.stringify(value,null,2),'NX_Backup_'+id+'_receipt.json');}
  async function download(item){if(downloadOwner)throw new Error('Ein Archivdownload oder Dateidialog läuft bereits.');
    const expected=NXDownload.metadata({size:item.archive_size,sha256:item.archive_sha256});
    const current=generation,session=connectionGeneration,own={current,session};downloadOwner=own;
    const alive=()=>downloadOwner===own&&current===generation&&session===connectionGeneration;
    let writer=null,handle=null,controller=null;
    try{
      if(window.showSaveFilePicker)handle=await window.showSaveFilePicker({suggestedName:'NX_Backup_'+item.id+'.zip',types:[{description:'NX-Sicherung',accept:{'application/zip':['.zip']}}]});
      else if(expected.size>NXDownload.memoryLimit)throw new Error('Für große Archive einen Browser mit Dateispeicher-API verwenden (Edge/Chrome unter localhost/HTTPS). Kein mehrgigabytegroßer Speicher-Download.');
      if(!alive())return;controller=new AbortController();abort=controller;
      el('backup-state').textContent='Archiv-SHA wird serverseitig geprüft; anschließend Empfang mit fortlaufender SHA-Prüfung …';
      const response=await fetch('/api/v1/backups/'+item.id+'/download',{headers:{Authorization:'Bearer '+token},signal:controller.signal});
      if(!response.ok)throw new Error((await response.json()).detail||'Archivdownload fehlgeschlagen.');if(!alive())throw new Error('Sitzung geändert.');
      if(handle){writer=await handle.createWritable();if(!alive())throw new Error('Sitzung geändert.');writable=writer;}
      const received=await NXDownload.receive(response,expected,{writer,alive:()=>alive()&&!controller.signal.aborted,progress:(size,total)=>{if(alive())el('backup-state').textContent='Archiv empfangen: '+size+' / '+total+' Bytes · Empfangs-SHA wird geprüft, noch nicht veröffentlicht.';}});
      if(!alive())throw new Error('Sitzung geändert.');
      if(writer){await writer.close();}else save(received.blob,'NX_Backup_'+item.id+'.zip');
      if(alive())el('backup-state').textContent=(writer?'Archiv gespeichert.':'Browserdownload des geprüften Archivs gestartet.')+' Empfangs-SHA und Größe bestätigt. Prüfsummenbeleg separat sichern; keine Signatur/NX-Freigabe.';
    }catch(error){controller?.abort();if(writer)await writer.abort().catch(()=>{});if(alive())throw error;}
    finally{if(downloadOwner===own){downloadOwner=null;abort=null;writable=null;}}
  }
  el('backup-create').onclick=()=>action(()=>start('backup'))();el('backup-refresh').onclick=()=>action(refresh)();el('recovery-preview').onclick=()=>action(()=>start('preview'))();el('recovery-create').onclick=()=>action(()=>start('restore'))();
  ['recovery-backup','recovery-root','recovery-name'].forEach(id=>el(id).addEventListener('input',invalidate));el('recovery-unsigned').onchange=controls;
  el('backup-request-receipt').onclick=()=>recover('receipt');el('backup-request-repeat').onclick=()=>recover('repeat');el('backup-request-fence').onclick=()=>recover('fence');
  loadTicket();controls();return {refresh,reset,configure(value){supported=value.backup_requests?.contract_version===1&&value.backup_requests.durable_acceptance===true&&modes.every(mode=>value.backup_requests.modes?.includes(mode));controls();}};
})();

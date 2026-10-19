'use strict';
/* Diagnostic SHA/version checks never substitute for the six human CAM checks. */
globalThis.NXReviews=(()=>{
  const el=id=>document.getElementById(id);let selected=null,pin=null,generation=0,scanGeneration=0,busy=false;
  const keys=Object.keys(tests);
  const storageKey='nx.pending-log-upload.v1',uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
  let supported=false,uploadOwner=null,ticket=null,memory=null,storageError='',knownCommit=null,viewSession=-1;
  const transitionStorage='nx.pending-release-transition.v1';
  let transitionsSupported=false,transitionTicket=null,transitionMemory=null,transitionOwner=null,transitionError='',transitionCommit=null;
  function transitionPending(){return Boolean(transitionTicket||transitionError||transitionOwner);}
  function transitionControls(){const paused=Boolean(transitionOwner);
    el('release-transition-recovery').hidden=!transitionTicket&&!transitionError;
    el('transition-key').textContent=transitionTicket?.key||'—';
    el('transition-state').textContent=transitionError||(transitionCommit?'Historischer Commit bestätigt; heutigen Bestand separat laden. Keine erneute Freigabe/Umschaltung aus dem Beleg.':'Freigabe/Aktivierung unbestätigt. Fehlender Beleg beweist keinen Abbruch. Beleg prüfen oder noch offenen finalen Commit sperren.');
    el('transition-receipt').disabled=!transitionsSupported||paused||!transitionTicket;
    el('transition-repeat').disabled=!transitionsSupported||paused||!transitionTicket||!transitionMemory;
    el('transition-fence').disabled=!transitionsSupported||paused||(!transitionTicket&&!transitionError);
    el('transition-ack').disabled=!transitionsSupported||paused||!transitionTicket||!transitionCommit;
    document.querySelectorAll('[data-release-activate]').forEach(button=>button.disabled=!transitionsSupported||transitionPending()||Boolean(ticket||storageError||uploadOwner));
  }
  function loadTransition(){try{const raw=sessionStorage.getItem(transitionStorage);if(!raw)return;const value=JSON.parse(raw);
    if(!value||Object.keys(value).sort().join(',')!=='action,contract_version,created_at,key'||value.contract_version!==1||!uuid(value.key)||!['review','activate'].includes(value.action)||!Number.isFinite(Date.parse(value.created_at)))throw new Error();transitionTicket=value;
  }catch{transitionError='Lokaler Freigabe-/Aktivierungshinweis unlesbar. Neue Freigaben/Aktivierungen gesperrt; Serverbelege und Audit prüfen.';}}
  function clearTransition(){sessionStorage.removeItem(transitionStorage);transitionTicket=transitionMemory=transitionCommit=null;transitionError='';controls();}
  function checkTransition(value,own){
    if(!value||value.contract_version!==1||value.request_key!==own.key||!['reserved','committed','failed','cancelled'].includes(value.state)||value.current_state!==false||value.files_verified!==false||value.automatic_retry!==false||value.automatic_activation!==false)throw new Error('Ungültiger Freigabe-/Aktivierungsbeleg.');
    if(value.release_id===null){if(value.state!=='cancelled'||value.action!=='cancelled'||value.result!==null)throw new Error('Ungültige finale Commitsperre.');return;}
    if(!uuid(value.release_id)||value.action!==own.action||(transitionMemory&&value.release_id!==transitionMemory.release))throw new Error('Freigabebeleg gehört zu einem anderen Vorgang.');
    if(value.state==='committed'){
      const result=value.result;
      if(!result||!(/^[a-f0-9]{64}$/).test(value.response_sha256)||!Number.isInteger(value.audit_sequence)||value.audit_sequence<1||!value.writer_finished_at)throw new Error('Unvollständiger historischer Commitbeleg.');
      if(own.action==='review'?(result.id!==value.release_id||result.status!=='approved'||result.review?.contract_version!==2):(result.release_id!==value.release_id||!Number.isInteger(result.generation)||result.generation<1||result.usable!==true||result.status!=='approved'))throw new Error('Historische Antwort passt nicht zur finalen Aktion.');
    }else if(value.result!==null)throw new Error('Offener Vorgang darf keine Erfolgsmeldung enthalten.');
  }
  async function transitionConfirmed(own,value,session,notice){if(session!==connectionGeneration||transitionTicket?.key!==own.key)return;checkTransition(value,own);
    if(value.state!=='committed')throw new Error('Freigabe/Aktivierung '+value.state+': noch kein finaler Commit; vor neuer Anfrage finale Commitsperre setzen.');
    transitionCommit=value;controls();const context=transitionMemory;
    // Never adopt the historical result as a current release or active pointer.
    await refresh();if(session!==connectionGeneration||transitionTicket?.key!==own.key)return;
    if(own.action==='review'&&context?.session===session&&context.generation===generation&&context.release===selected?.id)invalidate();
    clearTransition();if(notice===noticeGeneration)message('Historische '+(own.action==='review'?'Freigabe':'lokale Aktivierung')+' bestätigt. '+(own.action==='review'?'Fachliche Abnahme dokumentiert. ':'')+'Heutigen Bestand separat neu geladen; keine Produktivumschaltung oder automatische Wiederholung.');
  }
  async function transitionOperation(fn){if(transitionOwner)return;const owner={session:connectionGeneration,notice:noticeGeneration};transitionOwner=owner;controls();
    try{await fn(owner.session,owner.notice);}catch(error){if(owner.session===connectionGeneration&&owner.notice===noticeGeneration&&!NXRequests.isStale(error))message((transitionTicket?'Freigabe/Aktivierung noch ungeklärt: ':'')+error.message,true);}
    finally{if(transitionOwner===owner){transitionOwner=null;controls();}}}
  async function sendTransition(own,payload,session,notice){const target=transitionMemory?.release;
    if(!target)throw new Error('Exakte Anfrage ist nach Reload nicht mehr im Arbeitsspeicher.');
    await api(own.action==='review'?'releases/'+target+'/review':'active','POST',payload,{'Idempotency-Key':own.key});
    if(session!==connectionGeneration||transitionTicket?.key!==own.key)return;
    await transitionConfirmed(own,await api('release-requests/'+own.key),session,notice);
  }
  async function startTransition(action,release,payload){return transitionOperation(async(session,notice)=>{
    if(!transitionsSupported||transitionTicket||transitionError||ticket||storageError||uploadOwner)throw new Error('Zuerst offenen Vorgang klären; dauerhafte Serverbelege erforderlich.');
    const own={contract_version:1,key:crypto.randomUUID(),action,created_at:new Date().toISOString()};
    sessionStorage.setItem(transitionStorage,JSON.stringify(own));transitionTicket=own;
    transitionMemory={session,generation,release,payload:JSON.parse(JSON.stringify(payload))};controls();
    await sendTransition(own,transitionMemory.payload,session,notice);
  });}
  const recoverTransition=mode=>transitionOperation(async(session,notice)=>{
    if(!transitionsSupported)throw new Error('Server unterstützt keine dauerhaften Freigabe-/Aktivierungsbelege.');
    if(transitionError&&!transitionTicket){if(!confirm('Nur unlesbaren lokalen Hinweis verwerfen? Zuerst Serverbelege/Audit prüfen. Keine Serverdaten löschen, keine Freigabe/Aktivierung zurücknehmen.'))return;clearTransition();message('Nur lokalen Hinweis verworfen; Serverzustand unverändert.');return;}
    const own=transitionTicket;if(!own)throw new Error('Kein offener Freigabe-/Aktivierungshinweis.');
    if(mode==='ack'){if(!transitionCommit)throw new Error('Kein bestätigter historischer Commit.');if(!confirm('Nur damaligen Commit anerkennen, ohne den heutigen Bestand als freigegeben/aktiv zu bestätigen? Bestand separat aktualisieren.'))return;clearTransition();invalidate();message('Nur historischen Commit anerkannt; heutige Freigabe/Aktivierung nicht bestätigt.');return;}
    if(mode==='repeat'){if(!transitionMemory)throw new Error('Nach Reload nur Serverbeleg prüfen oder finalen Commit sperren; Abnahmeinhalt wurde nicht gespeichert.');await sendTransition(own,transitionMemory.payload,session,notice);return;}
    if(mode==='fence'&&!confirm('Nur noch offenen finalen Freigabe-/Aktivierungscommit sperren? Bereits gespeicherte Diagnosen/Freigaben/Zeiger bleiben erhalten; kein Worker-Abbruch.'))return;
    const value=await api('release-requests/'+own.key+(mode==='fence'?'/cancel':''),mode==='fence'?'POST':'GET');
    if(session!==connectionGeneration||transitionTicket?.key!==own.key)return;checkTransition(value,own);
    if(value.state==='committed')await transitionConfirmed(own,value,session,notice);
    else if(value.state==='cancelled'){clearTransition();invalidate();if(notice===noticeGeneration)message('Noch offener finaler Commit gesperrt. Keine vorhandene Freigabe/Aktivierung zurückgenommen; technische Diagnosen können erhalten bleiben.');}
    else throw new Error('Freigabe/Aktivierung '+value.state+': kein automatischer Neustart; vor neuer Anfrage finalen Commit sperren.');
  });
  el('transition-receipt').onclick=()=>recoverTransition('receipt');el('transition-repeat').onclick=()=>recoverTransition('repeat');
  el('transition-fence').onclick=()=>recoverTransition('fence');el('transition-ack').onclick=()=>recoverTransition('ack');
  function uploadControls(){el('log-upload-recovery').hidden=!ticket&&!storageError;el('log-request-key').textContent=ticket?.key||'—';
    el('log-request-state').textContent=storageError||(knownCommit?'Protokoll-Metadaten committed. Datei-/Abnahmezuordnung separat prüfen; keine zweite Kopie erzeugen.':'Protokollupload unbestätigt. Fehlender Beleg beweist keinen Abbruch; Beleg prüfen, identisch wiederholen oder Commit sperren.');
    el('log-receipt').disabled=!supported||Boolean(uploadOwner)||!ticket;el('log-repeat').disabled=!supported||Boolean(uploadOwner)||!ticket||!memory;
    el('log-fence').disabled=!supported||Boolean(uploadOwner)||(!ticket&&!storageError);el('log-ack').disabled=!supported||Boolean(uploadOwner)||!ticket||!knownCommit;}
  function loadTicket(){try{const raw=sessionStorage.getItem(storageKey);if(!raw)return;const value=JSON.parse(raw);
    if(!value||Object.keys(value).sort().join(',')!=='contract_version,created_at,key'||value.contract_version!==1||!uuid(value.key)||!Number.isFinite(Date.parse(value.created_at)))throw new Error('Ungültiger Protokolluploadhinweis.');ticket=value;
  }catch{storageError='Lokaler Protokolluploadhinweis unlesbar. Neue Uploads und Abnahmen gesperrt; Auftragsbelege prüfen.';}}
  function clearTicket(){sessionStorage.removeItem(storageKey);ticket=memory=knownCommit=null;storageError='';controls();}
  function checkLog(log){if(!log||typeof log.id!=='string'||!log.id.startsWith('uploaded-')||!uuid(log.id.slice(9))||typeof log.filename!=='string'||!(/^[a-f0-9]{64}$/).test(log.sha256)||!Number.isInteger(log.size)||log.size<1||log.size>20*1024*1024||log.source_type!=='uploaded'||!Number.isFinite(Date.parse(log.created_at)))throw new Error('Ungültiger Protokolluploadbeleg.');}
  async function confirmed(own,log,session){if(session!==connectionGeneration||ticket?.key!==own.key)return;checkLog(log);knownCommit=log;controls();
    const verified=await api('logs/'+log.id+'/metadata');if(session!==connectionGeneration||ticket?.key!==own.key)return;
    if(['id','filename','sha256','size','created_at','source_type'].some(name=>verified[name]!==log[name]))throw new Error('Protokoll-Dateiverifikation passt nicht zum Uploadbeleg.');
    const context=memory;clearTicket();let refreshError='';try{await refreshLogs();}catch(error){if(!NXRequests.isStale(error))refreshError=' Protokollbestand manuell aktualisieren.';}
    if(session!==connectionGeneration)return;
    const same=context?.generation===generation&&context.release===selected?.id&&context.session===session;
    if(same){el('syslog').value=log.id;if(el('syslog-file').files[0]===context.file)el('syslog-file').value='';invalidate();el('syslog-state').textContent='Beleg gespeichert. Neu prüfen und alle sechs fachlichen Bestätigungen erneut setzen.';details(log);}
    message('NX-Systemprotokoll unveränderlich gespeichert und Datei-SHA geprüft; noch keine Freigabe. '+(same?'Dieser Abnahme zugeordnet; Protokoll neu prüfen.':'Nicht automatisch einer aktuellen Abnahme zugeordnet. Im Protokollbestand ausdrücklich auswählen; aktuelle Eingaben bleiben erhalten.')+refreshError,Boolean(refreshError));}
  async function uploadOperation(fn){if(uploadOwner||busy)return;const own={session:connectionGeneration,generation};uploadOwner=own;controls();
    try{await fn(own.session);}catch(error){if(own.session===connectionGeneration&&!NXRequests.isStale(error))message((ticket?'Protokollupload noch ungeklärt: ':'')+error.message,true);}
    finally{if(uploadOwner===own){uploadOwner=null;controls();}}}
  async function send(own,payload,session){try{return await api('logs','POST',payload,{'Idempotency-Key':own.key});}
    catch(error){if(session===connectionGeneration&&ticket?.key===own.key&&[400,422].includes(error.httpStatus)){
      const fence=await api('log-requests/'+own.key+'/cancel','POST');if(session!==connectionGeneration)throw error;
      if(fence.contract_version!==1||fence.request_key!==own.key)throw new Error('Ungültiger Protokoll-Commitsperrbeleg.');
      if(fence.state==='cancelled')clearTicket();else if(fence.state==='committed')return fence.result;
    }throw error;}}
  function clearChecks(){keys.forEach(key=>el(key).checked=false);}
  function invalidate(){scanGeneration++;pin=null;clearChecks();controls();}
  function checkScan(value,release,alias){
    const hash=item=>typeof item==='string'&&/^[a-f0-9]{64}$/.test(item),count=item=>Number.isSafeInteger(item)&&item>=0;
    const strings=(items,pattern,maximum)=>Array.isArray(items)&&items.length<=maximum&&items.every((item,index)=>typeof item==='string'&&pattern.test(item)&&(!index||items[index-1]<item));
    const version=NXData.record(value)?value.version_check:null;
    let valid=NXData.record(value)&&value.parser_version===2&&value.release_id===release.id&&value.source===alias&&
      value.expected_folder===(release.host_folder||release.folder)&&hash(value.sha256)&&hash(value.manifest_sha256)&&
      typeof value.can_review==='boolean'&&typeof value.contains_cam_activity==='boolean'&&typeof value.contains_release_reference==='boolean'&&
      count(value.blocking_error_count)&&Array.isArray(value.blocking_errors)&&value.blocking_errors.length===Math.min(200,value.blocking_error_count)&&
      value.blocking_errors_truncated===(value.blocking_error_count>value.blocking_errors.length)&&
      value.blocking_errors.every((item,index)=>NXData.record(item)&&Number.isSafeInteger(item.line)&&item.line>0&&
        (!index||value.blocking_errors[index-1].line<item.line)&&typeof item.message==='string'&&item.message.length>0&&item.message.length<=1500)&&
      Array.isArray(value.warnings)&&value.warnings.length<=200&&value.warnings.every(item=>typeof item==='string'&&item.length<=4000)&&
      NXData.record(version)&&version.expected===release.nx_version&&version.supported_marker==='NX_VERSION'&&
      ['match','missing','mismatch','ambiguous','invalid'].includes(version.status)&&count(version.header_count)&&
      strings(version.families,/^\d{4}$/,10000)&&strings(version.versions,/^\d{4}(?:\.\d{1,10}){0,3}$/,200)&&
      version.versions.every(item=>version.families.includes(item.split('.')[0]))&&Array.isArray(version.evidence)&&
      version.evidence.length===Math.min(200,version.header_count)&&version.evidence_truncated===(version.header_count>version.evidence.length)&&
      version.evidence.every((item,index)=>NXData.record(item)&&Number.isSafeInteger(item.line)&&item.line>0&&typeof item.valid==='boolean'&&
        (!index||version.evidence[index-1].line<item.line)&&(!item.valid||typeof item.version==='string'&&version.versions.includes(item.version)&&
          item.family===item.version.split('.')[0]&&version.families.includes(item.family)))&&
      (!version.evidence.some(item=>!item.valid)||version.status==='invalid');
    if(valid){
      const expectedStatus=version.families.length>1?'ambiguous':!version.families.length?'missing':version.families[0]===release.nx_version?'match':'mismatch';
      valid=(version.status==='invalid'||version.status===expectedStatus)&&
        value.can_review===(version.status==='match'&&value.blocking_error_count===0&&value.contains_cam_activity&&value.contains_release_reference);
    }
    if(!valid)throw new Error('Kein vollständiger gebundener NX-Protokollvertrag; neu prüfen, keine Freigabe.');
  }
  function controls(){
    const bound=pin?.release_id===selected?.id&&pin?.alias===el('syslog').value&&pin?.can_review===true;
    const checked=keys.filter(key=>el(key).checked).length,meaningful=el('evidence').value.trim().length>=20;
    const pending=Boolean(uploadOwner)||Boolean(ticket)||Boolean(storageError)||transitionPending(),paused=busy||uploadOwner?.generation===generation;
    el('approve').disabled=!transitionsSupported||busy||pending||!bound||checked!==keys.length||!meaningful;
    el('scan').disabled=busy||pending||!selected||!el('syslog').value;el('syslog-upload').disabled=!supported||busy||pending||!selected;
    ['syslog','syslog-file','evidence',...keys].forEach(id=>el(id).disabled=paused);
    el('review-gate').textContent=!selected?'':busy?'Abnahmeoperation läuft …':
      !transitionsSupported?'Server unterstützt den aktuellen Freigabevertrag mit finaler Byteprüfung nicht. Serverstand prüfen; keine Freigabe.':
      pending?'Offenen Protokoll-/Freigabe-/Aktivierungsvorgang zuerst klären; kein neuer Scan oder Freigabe.':!bound?'Zuerst ein qualifizierendes Protokoll für diesen NX-Stand prüfen.':
      checked!==keys.length?`${checked} von sechs fachlichen Bestätigungen gesetzt.`:
      !meaningful?'Abnahmebeleg mit mindestens 20 aussagekräftigen Zeichen ergänzen.':
      'Versions-/SHA-Vorschau und sechs manuelle Prüfungen vorhanden. Freigabe erfolgt erst nach ausdrücklichem Klick.';
    uploadControls();transitionControls();
  }
  function reset(){generation++;if(viewSession!==connectionGeneration){viewSession=connectionGeneration;supported=transitionsSupported=false;uploadOwner=transitionOwner=null;transitionMemory=null;transitionCommit=null;}selected=pin=null;busy=false;clearChecks();el('review-box').hidden=true;
    ['review-name','review-path','syslog-state','review-gate'].forEach(id=>el(id).textContent='');
    el('evidence').value='';el('syslog-file').value='';controls();}
  function open(release){reset();selected=release;el('review-box').hidden=false;el('review-name').textContent=release.name;
    el('review-path').textContent='Zu prüfender NX'+release.nx_version+'-Ordner: '+(release.host_folder||release.folder);
    el('syslog-state').textContent='Protokoll aus genau diesem Testordner wählen oder hochladen; NX_VERSION-Header erforderlich.';
    controls();showWorkspace('manage','evidence');}
  async function task(fn){if(busy||uploadOwner||ticket||storageError||transitionPending()||!selected)return;const current=generation;busy=true;controls();
    try{await fn(current);}catch(error){if(current===generation&&!NXRequests.isStale(error)){invalidate();message(error.message,true);}}
    finally{if(current===generation){busy=false;controls();}}}
  el('syslog-upload').onclick=()=>uploadOperation(async session=>{
    if(!supported||ticket||storageError||transitionPending()||!selected)throw new Error('Offenen Vorgang zuerst klären oder passende Abnahme öffnen.');const current=generation,release=selected.id;
    const file=el('syslog-file').files[0];if(!file||!file.size||file.size>20*1024*1024)throw new Error('Bitte ein NX-Systemprotokoll mit 1 Byte bis 20 MB wählen.');
    invalidate();const read=NXRequests.begin('reviews.upload.read',()=>current===generation&&release===selected?.id),content=await read.wait(fileBase64(file));
    const own={contract_version:1,key:crypto.randomUUID(),created_at:new Date().toISOString()};sessionStorage.setItem(storageKey,JSON.stringify(own));ticket=own;memory={session,generation:current,release,file,payload:{filename:file.name,content_base64:content}};controls();
    await confirmed(own,await send(own,memory.payload,session),session);
  });
  const recover=mode=>uploadOperation(async session=>{
    if(!supported)throw new Error('Server unterstützt keine dauerhaften Protokolluploadbelege.');
    if(storageError&&!ticket){if(!confirm('Nur unlesbaren lokalen Protokolluploadhinweis verwerfen? Protokollbestand/Audit prüfen; keine Serverdaten löschen.'))return;clearTicket();message('Nur lokalen Protokollhinweis verworfen. Serverbelege und Dateien bleiben erhalten.');return;}
    const own=ticket;if(!own)throw new Error('Kein ungeklärter Protokollupload.');
    if(mode==='ack'){if(!knownCommit)throw new Error('Kein bestätigter Protokoll-Metadatencommit.');if(!confirm('Nur bestätigten Metadatencommit anerkennen, ohne Dateiverifikation/Abnahmezuordnung/Freigabe? Integritätsproblem separat klären.'))return;clearTicket();message('Protokoll-Metadatencommit anerkannt; keine Datei-/Abnahmefreigabe oder Zuordnung.');return;}
    if(mode==='repeat'){if(!memory)throw new Error('Nach Reload nur Beleg prüfen oder Commit sperren; Protokollbytes wurden nicht im Browser gespeichert.');await confirmed(own,await send(own,memory.payload,session),session);return;}
    if(mode==='fence'&&!confirm('Nur noch nicht committed Protokollupload sperren? Gespeicherte Protokolle/Teilpfade bleiben erhalten; kein Worker-Abbruch.'))return;
    const value=await api('log-requests/'+own.key+(mode==='fence'?'/cancel':''),mode==='fence'?'POST':'GET');if(session!==connectionGeneration||ticket?.key!==own.key)return;
    if(value.contract_version!==1||value.request_key!==own.key)throw new Error('Ungültiger Protokolluploadvertrag.');
    if(value.state==='committed')await confirmed(own,value.result,session);else if(value.state==='cancelled'){clearTicket();message('Protokolluploadcommit gesperrt; keine Dateien/Teilpfade gelöscht oder Freigaben gesetzt.');}
    else{details(value);throw new Error('Protokollupload '+value.state+': Allokation bleibt als Diagnose; kein automatisches Überschreiben. Vor neuer Anfrage Commit sperren.');}
  });
  el('log-receipt').onclick=()=>recover('receipt');el('log-repeat').onclick=()=>recover('repeat');el('log-fence').onclick=()=>recover('fence');el('log-ack').onclick=()=>recover('ack');
  el('scan').onclick=()=>task(async current=>{
    const id=selected.id,alias=el('syslog').value;invalidate();
    const scanContext=scanGeneration,alive=()=>current===generation&&scanContext===scanGeneration&&id===selected?.id&&alias===el('syslog').value;
    el('syslog-state').textContent='Protokoll wird geprüft; noch keine vollständige aktuelle Vorschau oder Freigabe.';
    details({scope:'nx_log_scan',status:'pending',release_id:id,source:alias});
    try{
      const report=await api('logs/'+encodeURIComponent(alias)+'/scan','POST',{release_id:id});
      if(!alive())return;
      checkScan(report,selected,alias);
      pin={...report,alias};details(report);
      const labels={match:'passend',missing:'nicht belegt',mismatch:'falsch',ambiguous:'mehrdeutig',invalid:'ungültiger Header'};
      el('syslog-state').textContent=`${report.blocking_error_count} Ladefehler · CAM-Aktivität: ${report.contains_cam_activity?'ja':'nein'} · richtiger NX-Datenstand: ${report.contains_release_reference?'ja':'nein'} · NX-Version: ${labels[report.version_check.status]} (erwartet ${report.version_check.expected}; gefunden ${report.version_check.versions.join(', ')||'—'}). Fachliche Abnahme weiterhin erforderlich.`;
    }catch(error){
      if(!alive())return;
      el('syslog-state').textContent='Keine vollständige aktuelle Protokollvorschau; Freigabe gesperrt. Neu prüfen.';
      details({scope:'nx_log_scan',status:'not_verified',release_id:id,source:alias});
      throw error;
    }
  });
  el('approve').onclick=()=>task(async current=>{
    if(!pin?.can_review||pin.release_id!==selected.id||pin.alias!==el('syslog').value)throw new Error('Erst eine aktuelle qualifizierende Protokollvorschau prüfen.');
    const body={tests:Object.fromEntries(keys.map(key=>[key,el(key).checked])),evidence:el('evidence').value,
      syslog_source:pin.alias,expected_log_sha256:pin.sha256,expected_manifest_sha256:pin.manifest_sha256};
    await startTransition('review',selected.id,body);
  });
  el('syslog').onchange=()=>{invalidate();el('syslog-state').textContent='Protokollauswahl geändert. Neu prüfen und fachliche Bestätigungen erneut setzen.';};
  [...keys,'evidence'].forEach(id=>el(id).addEventListener('input',controls));
  el('review-close').onclick=reset;
  loadTicket();loadTransition();reset();return {open,reset,
    activate:async release=>{if(transitionPending()||ticket||storageError||uploadOwner)throw new Error('Zuerst offenen Vorgang klären.');if(confirm('Nur den lokalen Verwaltungszeiger auf diesen freigegebenen Datenstand umschalten? Keine produktive SMB-Umschaltung.'))await startTransition('activate',release.id,{release_id:release.id,expected_generation:active.generation});},
    configure:value=>{supported=value.log_uploads?.contract_version===1&&value.log_uploads.durable_allocation===true&&value.log_uploads.atomic_log_receipt===true;const contract=value.release_transitions;transitionsSupported=contract?.contract_version===1&&contract.durable_intent===true&&contract.atomic_final_commit===true&&contract.historical_receipt===true&&contract.final_commit_fence===true&&contract.final_evidence_byte_gate===true&&contract.filesystem_atomic_snapshot===false;controls();},logsChanged:()=>{if(pin&&pin.alias!==el('syslog').value)invalidate();else controls();}};
})();

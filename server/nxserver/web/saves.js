'use strict';
/* A tab-persistent non-secret ticket, not a cached token/model/CAM payload. */
globalThis.NXSaves=(()=>{
  const el=id=>document.getElementById(id),storageKey='nx.pending-resource-write.v1';
  const uuid=value=>typeof value==='string'&&(/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/).test(value);
  let supported=false,pending=null,memory=null,storageError='';
  function load(){try{const raw=sessionStorage.getItem(storageKey);if(!raw)return;const value=JSON.parse(raw);
    if(value.contract_version!==1||!uuid(value.key)||!(value.method==='POST'&&value.path==='resources'||value.method==='PUT'&&value.path.startsWith('resources/')&&uuid(value.path.slice(10))))throw new Error('Ungültiger lokaler Schreibhinweis.');
    pending=value;
  }catch(error){storageError='Lokaler Wiederanlaufhinweis nicht lesbar. Keine neue Speicherung freigeben: '+error.message;}}
  function render(){el('save').disabled=saveBusy||!supported||Boolean(pending)||Boolean(storageError);
    el('save-recovery-box').hidden=!pending&&!storageError;el('save-request-key').textContent=pending?.key||'—';
    if(storageError)el('save-recovery-state').textContent=storageError;
    else if(pending)el('save-recovery-state').textContent='Speicherversuch noch nicht bestätigt. Serverbeleg prüfen, exakt denselben Versuch wiederholen oder serverseitig beenden. Eine Wiederholung erstellt keine zweite Revision.';
    el('save-receipt').disabled=saveBusy||!supported||!pending;el('save-repeat').disabled=saveBusy||!supported||!pending||!memory;
    el('save-cancel').disabled=saveBusy||!supported||(!pending&&!storageError);
    globalThis.NXSchemas?.controls?.();
    globalThis.NXNative?.editorControls?.();
  }
  function persist(value){sessionStorage.setItem(storageKey,JSON.stringify(value));pending=value;}
  function clear(){sessionStorage.removeItem(storageKey);pending=null;memory=null;storageError='';render();}
  function checkResult(ticket,result,action){
    const expected=ticket.method==='POST'?'resource.create':'resource.update';
    if(action&&action!==expected||!result||!uuid(result.id)||!Number.isInteger(result.revision)||result.revision<1||ticket.method==='PUT'&&result.id!==ticket.path.slice(10))throw new Error('Serverbeleg passt nicht zum gespeicherten Stammdatenversuch.');
  }
  async function confirmed(ticket,result,session){
    if(session!==connectionGeneration||pending?.key!==ticket.key)return;
    checkResult(ticket,result);const context=memory;let preserved=false;
    clear();
    if(context?.generation===editorGeneration){let unchanged=false;try{unchanged=context.signature===editorSignature();}catch{ /* Keep incomplete JSON entered later. */ }
      if(unchanged)reset();else{preserved=true;editing=result;el('kind').disabled=true;el('editing').textContent='Bearbeite '+result.name+' · bestätigte Revision '+result.revision+' · spätere Eingaben noch ungespeichert';globalThis.NXNative?.syncEditorFields?.();}}
    details(result);let refreshError='';try{await refresh();}catch(error){if(!NXRequests.isStale(error))refreshError=' Bestandsaktualisierung fehlgeschlagen; manuell aktualisieren. '+error.message;}
    if(session!==connectionGeneration)return;
    message('Neue Stammdatenrevision gespeichert. Schreibbeleg bestätigt: '+result.id+' / Rev '+result.revision+'. '+(preserved?'Spätere Editoränderungen bleiben ungespeichert. ':'')+'NX-Dateien wurden nicht verändert.'+refreshError,Boolean(refreshError));
  }
  async function run(operation){if(saveBusy)return;globalThis.NXSchemaImpact?.writeStarted?.();const owner={session:connectionGeneration};saveOwner=owner;saveBusy=true;render();message('Speicherversuch / Serverbeleg wird geprüft …');
    try{await operation(owner.session);}catch(error){if(owner.session===connectionGeneration&&!NXRequests.isStale(error)){render();message((pending?'Speicherung noch nicht bestätigt: ':'')+error.message,true);}}
    finally{if(saveOwner===owner){saveOwner=null;saveBusy=false;el('kind').disabled=Boolean(editing);render();}}}
  function save(){return run(async session=>{
    if(!supported)throw new Error('Server unterstützt keine dauerhaften Schreibbelege. Server aktualisieren.');
    if(pending||storageError)throw new Error('Unbestätigten Speicherversuch erst prüfen oder serverseitig beenden.');
    if(NXAssets.isUploading())throw new Error('Datei-Upload läuft oder ist noch ungeklärt. Erst den Uploadbeleg prüfen bzw. den offenen Commit sperren, danach Stammdaten speichern.');
    const body=editorBody();if(!body.name.trim())throw new Error('Bezeichnung eingeben.');
    const context={generation:editorGeneration,signature:editorSignature(),body:NXData.cloneWire(body)};if(editing)context.body.expected_revision=editing.revision;
    const ticket={contract_version:1,key:crypto.randomUUID(),path:editing?'resources/'+editing.id:'resources',method:editing?'PUT':'POST',created_at:new Date().toISOString()};
    persist(ticket);memory=context;el('kind').disabled=true;render();
    try{const result=await api(ticket.path,ticket.method,context.body,{'Idempotency-Key':ticket.key});await confirmed(ticket,result,session);}
    catch(error){
      // Definite validation rejection: fence this old attempt before allowing
      // edited inputs. Transport/5xx/conflict/authorization uncertainty stays pending.
      if(session===connectionGeneration&&pending?.key===ticket.key&&[400,422].includes(error.httpStatus)){
        const fence=await api('write-requests/'+ticket.key+'/cancel','POST');
        if(session!==connectionGeneration)return;
        if(fence.state==='cancelled')clear();else if(fence.state==='committed'){checkResult(ticket,fence.result,fence.action);await confirmed(ticket,fence.result,session);return;}
      }
      throw error;
    }
  });}
  function receipt(){return run(async session=>{const ticket=pending;if(!ticket)throw new Error('Kein unbestätigter Versuch.');
    const value=await api('write-requests/'+ticket.key);if(session!==connectionGeneration||pending?.key!==ticket.key)return;
    if(value.contract_version!==1||value.request_key!==ticket.key)throw new Error('Ungültiger Schreibbelegvertrag.');
    if(value.state==='committed'){checkResult(ticket,value.result,value.action);await confirmed(ticket,value.result,session);}
    else if(value.state==='cancelled'){clear();message('Speicherversuch serverseitig beendet. Keine gespeicherten Daten gelöscht; aktuelle Eingaben bleiben erhalten.');}
    else throw new Error('Server hat keinen terminalen Schreibbeleg. Gleiche Kennung beibehalten.');
  });}
  function repeat(){return run(async session=>{const ticket=pending,context=memory;if(!ticket||!context)throw new Error('Nach Seitenneuladen nur den Beleg prüfen oder Versuch serverseitig beenden; die ursprünglichen CAM-Daten wurden nicht im Browser gespeichert.');
    const result=await api(ticket.path,ticket.method,context.body,{'Idempotency-Key':ticket.key});await confirmed(ticket,result,session);
  });}
  function cancel(){return run(async session=>{
    if(storageError&&!pending){if(!confirm('Unlesbaren lokalen Wiederanlaufhinweis verwerfen? Ein früherer Serverauftrag könnte gespeichert sein. Bestand/Audit zuerst prüfen; keine serverseitigen Daten werden gelöscht.'))return;clear();message('Nur der unlesbare Browserhinweis wurde entfernt. Serverbestand/Audit auf frühere Speicherung prüfen.');return;}
    const ticket=pending;if(!ticket)return;
    if(!confirm('Diesen unbestätigten Versuch serverseitig beenden? Falls er bereits gespeichert ist, wird der Beleg zurückgegeben; gespeicherte Daten werden nicht gelöscht. Sonst wird die Kennung für spätere Schreibzugriffe gesperrt.'))return;
    const value=await api('write-requests/'+ticket.key+'/cancel','POST');if(session!==connectionGeneration||pending?.key!==ticket.key)return;
    if(value.contract_version!==1||value.request_key!==ticket.key)throw new Error('Ungültiger Abbruchbeleg.');
    if(value.state==='committed'){checkResult(ticket,value.result,value.action);await confirmed(ticket,value.result,session);}
    else if(value.state==='cancelled'){clear();message('Speicherversuch serverseitig gesperrt. Aktuelle Eingaben bleiben erhalten; beim nächsten Speichern neue Kennung.');}
    else throw new Error('Server hat keinen terminalen Abbruchbeleg.');
  });}
  el('save-receipt').onclick=receipt;el('save-repeat').onclick=repeat;el('save-cancel').onclick=cancel;
  load();render();
  return {save,render,configure:value=>{supported=value.write_requests?.contract_version===1;render();},
    blocksSave:()=>!supported||saveBusy||Boolean(pending)||Boolean(storageError),
    connectionChanged:()=>{supported=false;saveOwner=null;saveBusy=false;render();}};
})();

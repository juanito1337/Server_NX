'use strict';
/* Immutable uploads and pending, revisioned assignments. No physical deletion. */
globalThis.NXAssets = (() => {
  const el=id=>document.getElementById(id);
  let generation=0, request=0, offset=0, library=[], uploadBusy=false,assignmentGeneration=0,libraryOwner=null,assignOwner=null;
  const metadataPins=new Map(),metadataFields=['id','filename','sha256','size'];
  const storageKey='nx.pending-asset-upload.v1',uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
  let supported=false,uploadOwner=null,ticket=null,memory=null,storageError='',knownCommit=null;
  function uploadControls(){el('upload').disabled=!supported||Boolean(uploadOwner)||Boolean(ticket)||Boolean(storageError);
    el('upload-recovery-box').hidden=!ticket&&!storageError;el('upload-request-key').textContent=ticket?.key||'—';
    el('upload-recovery-state').textContent=storageError||(knownCommit?'Upload-Metadaten committed. Datei-/Editorzuordnung separat prüfen; keine zweite Datei erzeugen.':'Upload noch nicht bestätigt. Serverbeleg prüfen, identisch wiederholen oder Commit sperren. Teilpfade werden nie überschrieben oder gelöscht.');
    el('upload-receipt').disabled=!supported||Boolean(uploadOwner)||!ticket;el('upload-repeat').disabled=!supported||Boolean(uploadOwner)||!ticket||!memory;
    el('upload-fence').disabled=!supported||Boolean(uploadOwner)||(!ticket&&!storageError);el('upload-ack').disabled=!supported||Boolean(uploadOwner)||!ticket||!knownCommit;
    globalThis.NXSchemas?.controls?.();globalThis.NXNative?.editorControls?.();}
  function loadTicket(){try{const raw=sessionStorage.getItem(storageKey);if(!raw)return;const value=JSON.parse(raw);
    if(!value||Object.keys(value).sort().join(',')!=='contract_version,created_at,key'||value.contract_version!==1||!uuid(value.key)||!Number.isFinite(Date.parse(value.created_at)))throw new Error('Ungültiger Uploadhinweis.');ticket=value;
  }catch(error){storageError='Lokaler Uploadhinweis unlesbar. Neue Uploads gesperrt: '+error.message;}}
  function clearTicket(){sessionStorage.removeItem(storageKey);ticket=null;memory=null;storageError='';knownCommit=null;uploadControls();}
  function checkResult(asset){if(!NXData.record(asset)||!uuid(asset.id)||typeof asset.filename!=='string'||!Array.from(asset.filename).length||Array.from(asset.filename).length>120||asset.filename==='.'||asset.filename==='..'||/[ .]$/.test(asset.filename)||/[\\/:*?"<>|\x00-\x1f]/.test(asset.filename)||/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$/i.test(asset.filename)||!(/^[a-f0-9]{64}$/).test(asset.sha256)||!Number.isSafeInteger(asset.size)||asset.size<1||asset.size>20*1024*1024)throw new Error('Ungültige Dateimetadaten oder Uploadbeleg.');}
  async function readMetadata(identifier,expected=null){if(!uuid(identifier))throw new Error('Ungültige Dateimetadaten-Kennung.');
    const value=await api('assets/'+identifier+'/metadata');checkResult(value);
    if(value.id!==identifier||[expected,metadataPins.get(identifier)].some(pin=>pin&&metadataFields.some(field=>value[field]!==pin[field])))throw new Error('Dateimetadaten passen nicht zur angefragten unveränderlichen Datei. Bestand neu laden; keine Zuordnung oder Prüfbindung.');
    metadataPins.delete(identifier);metadataPins.set(identifier,Object.fromEntries(metadataFields.map(field=>[field,value[field]])));
    while(metadataPins.size>1000)metadataPins.delete(metadataPins.keys().next().value);return value;
  }
  async function confirmed(own,asset,session){if(session!==connectionGeneration||ticket?.key!==own.key)return;checkResult(asset);knownCommit=asset;uploadControls();
    const verified=await readMetadata(asset.id,asset);if(session!==connectionGeneration||ticket?.key!==own.key)return;
    if(['id','filename','sha256','size'].some(name=>verified[name]!==asset[name]))throw new Error('Dateiverifikation passt nicht zum ursprünglichen Uploadbeleg.');
    const context=memory;clearTicket();let assigned=false;
    if(context?.editor===editorGeneration&&context.generation===generation&&context.assignment===assignmentGeneration){
      try{const values=ids();if(!values.includes(asset.id)&&values.length<100){setIds([...values,asset.id]);assigned=true;}
        if(el('assetfile').files[0]===context.file)el('assetfile').value='';
      }catch{/* Incomplete later IDs remain; verified asset can be explicitly assigned from library. */}
    }
    invalidateLibrary();let refreshError='';try{await loadAttachments();}catch(error){if(!NXRequests.isStale(error))refreshError=' Anhangsanzeige manuell aktualisieren: '+error.message;}
    if(session!==connectionGeneration)return;details(asset);message('Datei gespeichert und SHA-256 geprüft. '+(assigned?'Im Editor zugeordnet; Stammdatensatz noch speichern und Geometrie erneut prüfen.':'Nicht automatisch dem aktuellen Editor zugeordnet. Datei im Dateibestand ausdrücklich auswählen; neue Eingaben bleiben erhalten.')+refreshError,Boolean(refreshError));
  }
  async function uploadOperation(fn){if(uploadOwner)return;const owner={session:connectionGeneration};uploadOwner=owner;uploadBusy=true;uploadControls();
    try{return await fn(owner.session);}catch(error){if(owner.session===connectionGeneration&&!NXRequests.isStale(error))message((ticket?'Upload noch ungeklärt: ':'')+error.message,true);}
    finally{if(uploadOwner===owner){uploadOwner=null;uploadBusy=false;uploadControls();}}}
  async function send(own,payload,session){try{return await api('assets','POST',payload,{'Idempotency-Key':own.key});}
    catch(error){if(session===connectionGeneration&&ticket?.key===own.key&&[400,422].includes(error.httpStatus)){
      const value=await api('asset-requests/'+own.key+'/cancel','POST');if(session!==connectionGeneration)throw error;
      if(value.contract_version!==1||value.request_key!==own.key)throw new Error('Ungültiger Upload-Sperrbeleg.');
      if(value.state==='cancelled')clearTicket();else if(value.state==='committed')return value.result;
    }throw error;}}
  function ids() {
    let value;try{value=JSON.parse(el('assetids').value);}catch{throw new Error('Dateianhang-IDs sind noch keine vollständige JSON-Liste.');}
    if(!Array.isArray(value)||value.length>100||value.some(id=>!uuid(id))||new Set(value).size!==value.length)throw new Error('Dateianhang-IDs müssen eine Liste mit bis zu 100 eindeutigen gespeicherten UUIDs sein.');
    return value;
  }
  function size(bytes) { return bytes<1024?bytes+' B':bytes<1024*1024?(bytes/1024).toFixed(1)+' KB':(bytes/1024/1024).toFixed(1)+' MB'; }
  function task(fn) { return async()=>{const current=generation,session=connectionGeneration;try{await fn(current);}catch(error){if(current===generation&&session===connectionGeneration&&!NXRequests.isStale(error))message(error.message,true);}}; }
  function invalidateLibrary() {
    request++;NXRequests.invalidate('assets.library');NXRequests.invalidate('assets.assign');libraryOwner=assignOwner=null;library=[];el('asset-library-load').disabled=false;el('asset-library-items').replaceChildren();el('asset-library-prev').disabled=true;el('asset-library-next').disabled=true;
    el('asset-library-state').textContent='Dateibestand laden. Die Liste enthält auch Dateien ohne aktuelle Zuordnung.';
  }
  function reset() {
    NXRequests.invalidate('assets.');
    generation++;assignmentGeneration++;checkFileDownloadContext();offset=0;uploadControls();
    el('attachments').replaceChildren();el('attachment-state').textContent='Noch keine Dateien zugeordnet.';
    el('asset-query').value='';el('asset-library').open=false;invalidateLibrary();
  }
  async function download(asset) {
    const current=generation,assignment=assignmentGeneration,editor=editorGeneration;
    await downloadFile('assets/'+asset.id,asset.filename,{sha256:asset.sha256,size:asset.size,current:()=>current===generation&&assignment===assignmentGeneration&&editor===editorGeneration});
  }
  async function loadAttachments(current=generation) {
    const identifiers=ids(),snapshot=JSON.stringify(identifiers);
    const pending=NXRequests.begin('assets.attachments',()=>current===generation&&snapshot===JSON.stringify(ids()));
    const files=await pending.wait(Promise.all(identifiers.map(async id=>{try{return {id,metadata:await readMetadata(id)};}catch(error){if(NXRequests.isStale(error))throw error;return {id,error:error.message};}})));
    el('attachments').replaceChildren();
    files.forEach(file=>{const row=document.createElement('tr'),metadata=file.metadata;
      cell(row,metadata?metadata.filename:'Nicht verfügbar · '+file.id);
      const info=cell(row,metadata?size(metadata.size)+' · SHA-256 geprüft':file.error);if(metadata)info.title=metadata.sha256;
      const actions=cell(row,'');if(metadata)actions.append(button('Herunterladen',task(()=>download(metadata))),button('Dateidetails',()=>details(metadata)));
      actions.append(button('Zuordnung entfernen',task(async()=>{
        const id=file.id;
        setIds(ids().filter(value=>value!==id));await loadAttachments();renderLibrary();
        message('Zuordnung vorgemerkt entfernt. Stammdatensatz noch speichern; Datei, Historie und CAM-Dateireferenzen bleiben unverändert.');
      })));el('attachments').append(row);
    });
    el('attachment-state').textContent=identifiers.length?`${identifiers.length} Datei(en) im Editor. Änderungen erst nach dem Speichern wirksam.`:'Noch keine Dateien zugeordnet.';
  }
  function setIds(value) {
    el('assetids').value=JSON.stringify(value);assignmentChanged();
  }
  function assignmentChanged(){assignmentGeneration++;checkFileDownloadContext();NXRequests.invalidate('assets.attachments');NXRequests.invalidate('assets.assign');assignOwner=null;NXNative.assetAdded();globalThis.NXFilePackages?.attachmentsChanged();NXExports.invalidate();
    el('attachments').replaceChildren();el('attachment-state').textContent='Anhangsliste geändert. Nach Abschluss der Eingabe aktualisieren; Stammdatensatz noch speichern.';renderLibrary();}
  function renderLibrary() {
    let assigned=null;try{assigned=ids();}catch{}el('asset-library-items').replaceChildren();
    library.forEach(asset=>{const row=document.createElement('tr');cell(row,asset.filename);cell(row,size(asset.size));cell(row,asset.current_resource_count+' Stammdatensätze');const actions=cell(row,'');
      const listed=request,assign=button(assigned?.includes(asset.id)?'Zugeordnet':'Zuordnen',task(async current=>{
        if(assignOwner)return;const epoch=assignmentGeneration,snapshot=el('assetids').value,pending=NXRequests.begin('assets.assign',()=>current===generation&&listed===request&&epoch===assignmentGeneration&&snapshot===el('assetids').value);assignOwner=pending;renderLibrary();
        try{await pending.wait(readMetadata(asset.id,asset));const identifiers=ids();if(!identifiers.includes(asset.id)){if(identifiers.length>=100)throw new Error('Maximal 100 Anhänge je Stammdatensatz.');setIds([...identifiers,asset.id]);}
          await loadAttachments();renderLibrary();message('Datei zugeordnet. Stammdatensatz noch speichern und CAM-/Geometriebeleg erneut prüfen.');
        }finally{if(assignOwner===pending){assignOwner=null;renderLibrary();}}
      }));assign.disabled=Boolean(assignOwner)||!assigned||assigned.includes(asset.id);actions.append(assign,button('Herunterladen',task(()=>download(asset))));el('asset-library-items').append(row);
    });
  }
  const loadLibrary=task(async current=>{
    if(libraryOwner)return;const query=el('asset-query').value,at=offset;invalidateLibrary();const nonce=request;
    el('asset-library-state').textContent='Lade Dateibestand …';
    const pending=NXRequests.begin('assets.library',()=>current===generation&&nonce===request&&query===el('asset-query').value);
    libraryOwner=pending;el('asset-library-load').disabled=true;
    try{const result=await pending.wait(api('assets?query='+encodeURIComponent(query)+'&offset='+at+'&limit=100'));
      if(!NXData.record(result)||result.offset!==at||result.integrity_checked!==false||!Number.isSafeInteger(result.total)||!Number.isSafeInteger(result.filtered)||result.filtered<0||result.total<result.filtered||!Array.isArray(result.items)||result.items.length!==Math.min(100,Math.max(0,result.filtered-at))||new Set(result.items.map(asset=>asset?.id)).size!==result.items.length)throw new Error('Kein vollständiger Dateibestand mit passenden Seitenmetadaten.');
      for(const asset of result.items){checkResult(asset);if(!Number.isSafeInteger(asset.current_resource_count)||asset.current_resource_count<0)throw new Error('Ungültige Dateizuordnungszahlen.');}
      library=result.items;renderLibrary();
      el('asset-library-state').textContent=`${result.items.length?at+1:0}–${result.items.length?at+result.items.length:0} von ${result.filtered} Treffern · ${result.total} Dateien insgesamt. Integrität wird beim Zuordnen oder Herunterladen geprüft. Zuordnungszahlen beziehen sich nur auf aktuell gespeicherte Stammdaten.`;
      el('asset-library-prev').disabled=at===0;el('asset-library-next').disabled=at+result.items.length>=result.filtered;
    }catch(error){if(pending.current()&&!NXRequests.isStale(error))el('asset-library-state').textContent='Dateibestand nicht bestätigt: '+error.message;throw error;}
    finally{if(libraryOwner===pending){libraryOwner=null;el('asset-library-load').disabled=false;}}
  });
  const upload=()=>uploadOperation(async session=>{
    if(!supported||ticket||storageError)throw new Error('Unbestätigten Upload erst klären oder Server aktualisieren.');
    const current=generation,assignment=assignmentGeneration;
    const file=el('assetfile').files[0];if(!file||file.size>20*1024*1024||file.size===0)throw new Error('Bitte eine Datei mit 1 Byte bis 20 MB wählen.');
    if(ids().length>=100)throw new Error('Maximal 100 Anhänge je Stammdatensatz.');
    const read=NXRequests.begin('assets.upload.read',()=>current===generation&&assignment===assignmentGeneration),content=await read.wait(fileBase64(file));
    const own={contract_version:1,key:crypto.randomUUID(),created_at:new Date().toISOString()};
    sessionStorage.setItem(storageKey,JSON.stringify(own));ticket=own;memory={generation:current,assignment,editor:editorGeneration,file,payload:{filename:file.name,content_base64:content}};uploadControls();
    await confirmed(own,await send(own,memory.payload,session),session);
  });
  const recover=mode=>uploadOperation(async session=>{
    if(!supported)throw new Error('Server unterstützt keine dauerhaften Uploadbelege.');
    if(storageError&&!ticket){if(!confirm('Nur unlesbaren Uploadhinweis verwerfen? Dateibestand/Audit auf früheren Upload prüfen. Keine Datei wird gelöscht.'))return;clearTicket();message('Nur lokaler Uploadhinweis verworfen. Gespeicherte Dateien bleiben erhalten.');return;}
    const own=ticket;if(!own)throw new Error('Kein ungeklärter Upload.');
    if(mode==='ack'){if(!knownCommit)throw new Error('Kein bestätigter Metadatencommit.');if(!confirm('Nur bestätigten Uploadbeleg anerkennen, OHNE Zuweisung/Dateifreigabe? Eine fehlende/veränderte Datei bleibt ein Integritätsproblem. Keine Daten löschen.'))return;clearTicket();message('Metadatencommit anerkannt, keine Editorzuweisung oder Datei-/CAM-Freigabe. Integritätsproblem separat klären.');return;}
    if(mode==='repeat'){if(!memory)throw new Error('Nach Neuladen nur Beleg prüfen oder Commit sperren; Modell wurde nicht im Browser gespeichert.');await confirmed(own,await send(own,memory.payload,session),session);return;}
    if(mode==='fence'&&!confirm('Noch nicht committed Upload sperren? Bereits gespeicherte Datei wird mit Beleg zurückgegeben, nie gelöscht. Teilpfade bleiben als Diagnose.'))return;
    const value=await api('asset-requests/'+own.key+(mode==='fence'?'/cancel':''),mode==='fence'?'POST':'GET');
    if(session!==connectionGeneration||ticket?.key!==own.key)return;
    if(value.contract_version!==1||value.request_key!==own.key)throw new Error('Ungültiger Uploadvertrag.');
    if(value.state==='committed')await confirmed(own,value.result,session);
    else if(value.state==='cancelled'){clearTicket();message('Uploadcommit gesperrt. Keine gespeicherten Dateien/Teilpfade gelöscht; aktuelle Eingaben erhalten.');}
    else{details(value);throw new Error('Upload '+value.state+': Allokation bleibt als Diagnose. Keine automatische Überschreibung; vor neuem Upload zuerst Commit sperren.');}
  });
  el('upload-receipt').onclick=()=>recover('receipt');el('upload-repeat').onclick=()=>recover('repeat');el('upload-fence').onclick=()=>recover('fence');el('upload-ack').onclick=()=>recover('ack');
  el('asset-library-load').onclick=loadLibrary;
  el('asset-library-prev').onclick=()=>{offset=Math.max(0,offset-100);return loadLibrary();};
  el('asset-library-next').onclick=()=>{offset+=100;return loadLibrary();};
  el('asset-query').oninput=()=>{offset=0;invalidateLibrary();};
  el('assetids').oninput=assignmentChanged;
  el('assetids').onchange=task(async()=>{assignmentChanged();await loadAttachments();renderLibrary();});
  loadTicket();uploadControls();return {upload,reset,readMetadata,identifiers:ids,isUploading:()=>uploadBusy||Boolean(ticket)||Boolean(storageError),open:()=>{reset();return task(loadAttachments)();},
    configure:value=>{supported=value.asset_uploads?.contract_version===1&&value.asset_uploads.durable_allocation===true&&value.asset_uploads.atomic_asset_receipt===true;uploadControls();},
    connectionChanged:()=>{generation++;assignmentGeneration++;metadataPins.clear();uploadOwner=null;uploadBusy=false;supported=false;uploadControls();}};
})();

/* Explicit component placement. Preview/save/export remain separate operations. */
globalThis.NXGraphicsBindings=(()=>{
  const el=id=>document.getElementById(id),hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  let enabled=false,specs=new Map(),view=null,report=null,source='',generation=0,loadOwner=null,previewOwner=null;
  const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  function scope(){return JSON.stringify([connectionGeneration,editorGeneration,editing?.id??null,editing?.revision??null,el('kind').value,el('cam').value,el('assetids').value,el('model-bindings').value,NXNative.hasModelPackage(),el('package-enabled').checked]);}
  function library(){const cam=JSON.parse(el('cam').value),kind=el('kind').value;
    if(!NXData.record(cam)||!cam.library_id&&!(kind==='tool'&&typeof cam.class_name==='string'&&NXData.record(cam.values)))throw new Error('Zuerst einen vollständigen CAM-Datensatz wählen.');
    const spec=specs.get(cam.library_id||'tool-metric');if(!spec||spec.kind!==kind||!spec.graphics_dir)throw new Error('Keine Standard-Graphics-Zuordnung für diese Bibliothek. Gegebenenfalls den eigenen Dateipaketeditor verwenden.');return {cam,spec};}
  function bindings(){let value;try{value=JSON.parse(el('model-bindings').value);}catch{throw new Error('PRT-Zielpfade sind noch keine vollständige JSON-Liste. Eingabe erhalten.');}
    if(!Array.isArray(value)||value.length>100||value.some(item=>!NXData.record(item)||Object.keys(item).sort().join(',')!=='asset_id,path'||typeof item.asset_id!=='string'||typeof item.path!=='string')||new Set(value.map(item=>item.asset_id)).size!==value.length)throw new Error('Explizite PRT-Zielpfade müssen eindeutige asset_id/path-Einträge sein.');return value;}
  function blocked(){return saveBusy||NXAssets.isUploading()||Boolean(globalThis.NXSaves?.blocksSave?.());}
  function locked(){return NXNative.hasModelPackage()||el('package-enabled').checked;}
  function proposed(){if(!view)throw new Error('Modelldateien zuerst laden.');return view.rows.filter(row=>row.selected&&row.prt).map(row=>({asset_id:row.asset.id,path:view.spec.graphics_dir+'/'+(row.folder?row.folder+'/':'')+row.asset.filename}));}
  function signature(){return JSON.stringify([scope(),view?.rows.map(row=>[row.asset.id,row.selected,row.folder])]);}
  function clearReport(reason){if(report&&el('details').textContent===NXData.stringifyWire(report.value,2))el('details').textContent=JSON.stringify({module:'graphics_bindings',status:'not_verified',reason},null,2);report=null;}
  function controls(){let spec=null;try{spec=library().spec;}catch{}const busy=Boolean(loadOwner||previewOwner),readonly=locked()||blocked();
    el('graphics-load').disabled=!enabled||busy||!spec;
    el('graphics-preview').disabled=!enabled||busy||readonly||!view||!proposed().length;
    el('graphics-apply').disabled=!enabled||busy||readonly||!report||report.snapshot!==signature();
    el('graphics-default').disabled=!enabled||busy||readonly||!spec||el('model-bindings').value.trim()==='[]';
    for(const input of el('graphics-rows').querySelectorAll('input'))input.disabled=Boolean(loadOwner)||readonly||input.dataset.graphicsPrt!=='true';
  }
  function reset(reason='CAM-Datensatz und Anhänge wählen; noch keine Pfadänderung.'){generation++;NXRequests.invalidate('graphics.');clearReport(reason);view=null;loadOwner=previewOwner=null;source=scope();el('graphics-rows').replaceChildren();
    let text='Keine passende native Graphics-Bibliothek gewählt.';try{const {spec}=library();text=spec.id+' · '+spec.graphics_dir+'/';}catch{}
    el('graphics-context').textContent=text;el('graphics-state').textContent=!enabled?'Server unterstützt die schreibfreie Graphics-Pfadvorschau noch nicht.':locked()?'Gebundenes Ergebnis oder eigener Dateipaketmodus: Graphics-Pfade hier schreibgeschützt. Paketbindung ausdrücklich lösen oder Dateipaketeditor verwenden.':reason;controls();}
  function sourceChanged(){if(source!==scope())reset('Editor, Anhänge oder Modellpfade geändert. Dateien erneut laden; keine Vorschau bleibt freigegeben.');else controls();}
  function invalidate(){generation++;NXRequests.invalidate('graphics.preview');previewOwner=null;clearReport('Auswahl oder Unterordner geändert.');el('graphics-state').textContent='Auswahl oder Unterordner geändert. Pfadzuordnung erneut prüfen; bisherige Editorwerte unverändert.';controls();}
  function render(){el('graphics-rows').replaceChildren();for(const row of view.rows){const tr=document.createElement('tr'),label=document.createElement('label'),check=document.createElement('input');tr.dataset.graphicsId=row.asset.id;check.type='checkbox';check.checked=row.selected;check.dataset.graphicsPrt=String(row.prt);check.dataset.graphicsSelect='';check.setAttribute('aria-label',row.asset.filename+' ausdrücklich exportieren');label.append(check,document.createTextNode(row.asset.filename+(row.prt?'':' · kein PRT, bleibt privater Quellanhang bei expliziter Auswahl')));cell(tr,'').append(label);
      const input=document.createElement('input');input.value=row.folder;input.maxLength=3900;input.dataset.graphicsPrt=String(row.prt);input.dataset.graphicsFolder='';input.setAttribute('aria-label','Unterordner für '+row.asset.filename);cell(tr,'').append(input);const path=cell(tr,'');
      const show=()=>{path.textContent=row.selected?view.spec.graphics_dir+'/'+(row.folder?row.folder+'/':'')+row.asset.filename:'Nicht ausgewählt · privater Anhang nach Übernahme der expliziten Auswahl';};show();
      check.onchange=()=>{row.selected=check.checked;invalidate();show();};input.oninput=()=>{row.folder=input.value;invalidate();show();};el('graphics-rows').append(tr);}controls();}
  async function load(){sourceChanged();if(loadOwner||previewOwner)return;const current=scope(),own=NXRequests.begin('graphics.load',()=>scope()===current),context=library(),ids=NXAssets.identifiers(),old=bindings();
    clearReport('Modelldateien werden neu geprüft.');view=null;el('graphics-rows').replaceChildren();loadOwner=own;controls();el('graphics-state').textContent='Prüfe zugeordnete Dateien und unveränderliche Originalnamen …';
    try{const assets=await own.wait(Promise.all(ids.map(id=>NXAssets.readMetadata(id)))),known=new Map(assets.map(asset=>[asset.id,asset])),previous=new Map();
      for(const item of old){const asset=known.get(item.asset_id),prefix=context.spec.graphics_dir+'/';if(!asset||!asset.filename.toLowerCase().endsWith('.prt')||!item.path.startsWith(prefix)||!(item.path===prefix+asset.filename||item.path.endsWith('/'+asset.filename)))throw new Error('Bestehende Pfade enthalten eine nicht zugeordnete Datei, einen fremden Ordner oder geänderten Basename. Eingaben bleiben erhalten; ausdrücklich korrigieren oder Standardmodus wählen.');previous.set(item.asset_id,item.path.slice(prefix.length,-asset.filename.length).replace(/\/$/,''));}
      view={...context,assets,ids,rows:assets.map(asset=>({asset,prt:asset.filename.toLowerCase().endsWith('.prt'),selected:asset.filename.toLowerCase().endsWith('.prt')&&(!old.length||previous.has(asset.id)),folder:previous.get(asset.id)||''}))};render();el('graphics-state').textContent=locked()?'Paketpfade geladen, schreibgeschützt. Gebundene Komponenten erst nach ausdrücklich gelöster Paketbindung ändern.':old.length?'Explizite Pfade geladen. Auswahl/Unterordner ändern und erneut prüfen. Noch keine Änderung am Editor.':'Standardmodus ist noch aktiv. Auswahl ist nur ein Vorschlag; nach geprüfter Übernahme werden nur ausgewählte PRT-Dateien exportiert.';
    }catch(error){if(own.current()&&!NXRequests.isStale(error))el('graphics-state').textContent='Modelldateien nicht vollständig geladen. Eingaben bleiben erhalten.';throw error;}
    finally{if(loadOwner===own){loadOwner=null;controls();}}}
  function checkReport(value,input){const check=condition=>{if(!condition)throw new Error('Kein vollständiger gebundener Graphics-Pfadvorschauvertrag.');},paths=new Map(input.file_bindings.map(item=>[item.asset_id,item.path]));
    check(NXData.record(value)&&value.contract_version===1&&value.scope==='graphics_paths_only'&&value.mode==='explicit_prt'&&value.library_id===view.spec.id&&value.graphics_dir===view.spec.graphics_dir&&value.nx_version===(input.cam.library_id?input.cam.nx_version:null)&&value.resource_id===input.resource_id&&value.expected_revision===input.expected_revision&&hex(value.source_sha256)&&hex(value.preview_hash));
    check(['saved','nx_files_written','automatic_approval','target_conflicts_checked','provenance_checked'].every(key=>value[key]===false)&&same(value.asset_ids,input.asset_ids)&&same(value.file_bindings,input.file_bindings));
    const expected=view.assets.filter(asset=>paths.has(asset.id)).map(asset=>({asset_id:asset.id,filename:asset.filename,size:asset.size,sha256:asset.sha256,path:paths.get(asset.id)}));
    check(Array.isArray(value.entries)&&value.entries.length===expected.length&&value.entries.every((item,index)=>NXData.record(item)&&Object.keys(expected[index]).every(key=>item[key]===expected[index][key]))&&same(value.private_asset_ids,input.asset_ids.filter(id=>!paths.has(id))));
  }
  async function preview(){sourceChanged();if(el('graphics-preview').disabled)return;clearReport('Pfadzuordnung wird erneut geprüft.');const snapshot=signature(),own=NXRequests.begin('graphics.preview',()=>snapshot===signature()),input={kind:el('kind').value,cam:view.cam,asset_ids:view.ids,file_bindings:proposed(),resource_id:editing?.id??null,expected_revision:editing?.revision??null};previewOwner=own;controls();el('graphics-state').textContent='Prüfe Originalnamen und Standardpfade auf dem Server …';
    try{const value=await own.wait(api('graphics-bindings/preview','POST',input));checkReport(value,input);report={value,snapshot};details(value);el('graphics-state').textContent=value.entries.length+' PRT-Zielpfade geprüft; '+value.private_asset_ids.length+' Anhänge bleiben privat. Keine Zielkollisionen, internen PRT-Verweise oder fachliche NX-Freigabe geprüft. Nur ausdrücklich in Editor übernehmen.';}
    catch(error){if(own.current()&&!NXRequests.isStale(error))el('graphics-state').textContent='Pfadzuordnung nicht bestätigt. Auswahl bleibt zur Korrektur erhalten.';throw error;}
    finally{if(previewOwner===own){previewOwner=null;controls();}}}
  function apply(){sourceChanged();if(el('graphics-apply').disabled)return;const pin=report,current=signature();if(!confirm('Geprüfte PRT-Pfade nur in den Editor übernehmen? Nicht ausgewählte Dateien bleiben Anhänge, werden aber in explizitem Modus nicht exportiert. Geometriechecks zurücksetzen; separat speichern, prüfen und exportieren. Interne PRT-/GRAPHICS-Verweise selbst fachlich prüfen.'))return;
    if(report!==pin||current!==signature()||blocked()||locked())throw NXRequests.stale();
    if(same(bindings(),pin.value.file_bindings)){message('PRT-Pfade unverändert; keine Revision oder Freigabe geändert.');return;}
    el('model-bindings').value=JSON.stringify(pin.value.file_bindings,null,2);if(el('camstatus').value==='approved')el('camstatus').value='draft';NXNative.assetAdded();NXExports.invalidate();reset('Geprüfte PRT-Pfade im Editor übernommen. Separat speichern; keine NX-Datei geändert.');message('Geprüfte PRT-Pfade im Editor übernommen. Stammdaten noch speichern und Geometrie erneut prüfen.');}
  function defaults(){sourceChanged();if(el('graphics-default').disabled)return;const current=scope();if(!confirm('Alle expliziten PRT-Pfade im Editor entfernen? Der bisherige Standardmodus exportiert wieder ALLE Anhänge mit Originalnamen im Graphics-Ordner, auch Quelldokumente. Keine Datei löschen oder jetzt exportieren.'))return;if(current!==scope()||blocked()||locked())throw NXRequests.stale();el('model-bindings').value='[]';if(el('camstatus').value==='approved')el('camstatus').value='draft';NXNative.assetAdded();NXExports.invalidate();reset('Standardmodus im Editor vorgemerkt. Alle Anhänge/Verweise vor Speichern und Export prüfen.');}
  el('graphics-load').onclick=()=>action(load)();el('graphics-preview').onclick=()=>action(preview)();el('graphics-apply').onclick=()=>action(apply)();el('graphics-default').onclick=()=>action(defaults)();
  ['kind','package-enabled'].forEach(id=>el(id).addEventListener('change',sourceChanged));
  return {reset,sourceChanged,controls,configure:value=>{const cap=value.graphics_bindings;enabled=cap?.contract_version===1&&cap.read_only_preview===true&&cap.standard_graphics_rules===true&&cap.original_basenames===true&&cap.explicit_prt_selection===true&&cap.automatic_reference_rewrite===false;specs=new Map((value.native_library_specs||[]).map(spec=>[spec.id,spec]));reset();},clearConnection:()=>{enabled=false;specs.clear();reset();}};
})();

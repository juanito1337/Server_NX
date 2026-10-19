'use strict';
/* Native library workflows share the authenticated API helpers in app.js.
 * No HTML from file names, database values, or API responses is executed. */
globalThis.NXNative = (() => {
  const el = id => document.getElementById(id);
  let nativeData = null, creationData = null, inventoryRelease = '', creationRelease = '', importPreview = null, catalogPreview = null, hashes = {};
  let schemaReadEnabled=false,creationInventory=null,creationContext='',creationInventoryOwner=null,creationSchemaOwner=null;
  const creationSpecs=new Map();
  const creationReleaseKey=id=>{const value=releases.find(item=>item.id===id);return value&&!['building','failed'].includes(value.status)?JSON.stringify([value.id,value.nx_version,value.status,value.folder]):null;};
  function creationReady(){return Boolean(schemaReadEnabled&&creationData&&!creationInventoryOwner&&!creationSchemaOwner&&creationRelease===el('create-release').value&&creationContext===creationReleaseKey(creationRelease));}
  function creationControls(){el('create-inventory').disabled=!schemaReadEnabled||Boolean(creationInventoryOwner);el('create-schema').disabled=!schemaReadEnabled||Boolean(creationInventoryOwner||creationSchemaOwner)||!creationInventory||!creationInventory.libraries.some(item=>item.id===el('create-library').value&&item.can_read_schema);el('native-new').disabled=!creationReady();editorControls();}
  const geometryTests = {units:'Einheiten / Maßstab',orientation:'Werkzeugachse / Orientierung',mount_point:'Bezugspunkt / Montage',cutting_geometry:'Schneidende Geometrie',non_cutting_geometry:'Nichtschneidende Geometrie / Halter',collision_geometry:'Kollisionsgeometrie'};
  let modelPackage=null;
  let objectOwner=null,inventoryContext='',selectionEpoch=0,catalogReadOwner=null;
  const selectedObjects=new Map(),selectionPins=new Map(),unverifiedLibraries=new Set();let selectionSupported=false,catalogPage=0,libraryKinds=new Map();
  const inventoryKey=id=>{const value=releases.find(item=>item.id===id);return value&&!['building','failed'].includes(value.status)?JSON.stringify([value.id,value.nx_version,value.folder]):null;};
  const selectionBlocked=()=>el('catalog-scope').value==='objects'&&[...selectedObjects.keys()].some(library=>unverifiedLibraries.has(library));
  const selectableKey=key=>typeof key==='string'&&key.length>0&&key.length<=1024&&!/[|\r\n\x00]/.test(key);
  const selectionCount=()=>[...selectedObjects.values()].reduce((count,keys)=>count+keys.size,0);
  function renderSelection(){
    const count=selectionCount(),selective=el('catalog-scope').value==='objects',epoch=++selectionEpoch;
    el('catalog-scope').querySelector('[value="objects"]').disabled=!selectionSupported;
    el('catalog-libraries').hidden=selective;
    el('catalog-selection-state').textContent=(selectionSupported?count+' vollständige Objekte ausgewählt (max. 500). ':'Server unterstützt keine Einzelobjektübernahme. ')+(selective?'Vorschau übernimmt nur diese Auswahl.':'Übernahme ganzer Bibliotheken aktiv; Objektauswahl wird dabei nicht verwendet.')+(selectionBlocked()?' Ausgewählte Bibliotheken erneut laden: '+[...selectedObjects.keys()].filter(library=>unverifiedLibraries.has(library)).join(', ')+'. Vorschau und Übernahme bis dahin gesperrt.':'');
    el('catalog-select-page').disabled=!selectionSupported||Boolean(objectOwner)||!nativeData?.items.length;
    el('catalog-clear-selection').disabled=!count;
    el('catalog-selection-list').replaceChildren();
    for(const [library,keys] of selectedObjects)for(const key of keys){const line=document.createElement('p');line.append(document.createTextNode(library+' / '+key+' '),button('Aus Auswahl entfernen',()=>{if(epoch!==selectionEpoch)throw NXRequests.stale();selectObject(library,key,false);}));el('catalog-selection-list').append(line);}
    for(const check of el('native-objects').querySelectorAll('input[data-catalog-key]'))check.checked=selectedObjects.get(check.dataset.catalogLibrary)?.has(check.dataset.catalogKey)===true;
    importControls();
  }
  function clearSelection(){selectedObjects.clear();selectionPins.clear();unverifiedLibraries.clear();invalidateCatalog();renderSelection();}
  function selectObject(library,key,checked){
    try{
      if(checked){if(!selectionSupported)throw new Error('Server unterstützt keine Einzelobjektübernahme.');
        if(!selectableKey(key))throw new Error('Dieser Bibliotheksschlüssel ist für die gezielte Auswahl nicht unterstützt.');
        if(!selectedObjects.get(library)?.has(key)&&selectionCount()>=500)throw new Error('Höchstens 500 vollständige Objekte auswählen. Auswahl zuerst verkleinern.');
        if(objectOwner||!nativeData||nativeData.id!==library||!nativeData.items.some(item=>item.key===key))throw new Error('Bibliotheksseite erneut laden.');
        if(!selectedObjects.has(library))selectedObjects.set(library,new Set());selectedObjects.get(library).add(key);selectionPins.set(library,nativeData.input_sha256);
      }else{selectedObjects.get(library)?.delete(key);if(!selectedObjects.get(library)?.size){selectedObjects.delete(library);selectionPins.delete(library);unverifiedLibraries.delete(library);}}
      invalidateCatalog();
    }finally{renderSelection();}
  }
  function model(value=null){modelPackage=value?structuredClone(value):null;el('model-package-info').textContent=modelPackage?`Gebundenes Ergebnis ${modelPackage.result_id}: ${modelPackage.root_part}. Externe unbeglaubigte Inventardaten; keine CAM-/Geometriefreigabe.`:'';el('model-package-detach').hidden=!modelPackage;}
  const run = fn => () => action(fn)();
  function setGeometry(value={}) {
    el('geometry-mode').value=value.mode||'none';el('geometry-status').value=value.status||'needs_preparation';
    el('geometry-version').value=value.nx_version||'';el('geometry-evidence').value=value.evidence||'';hashes=value.asset_hashes||{};
    Object.keys(geometryTests).forEach(k=>el('geometry-'+k).checked=value.checks?.[k]===true);
  }
  function resourceFields() { const cam=JSON.parse(el('cam').value);if(NXData.record(cam)&&cam.library_id&&libraryKinds.get(cam.library_id)!==el('kind').value)throw new Error('Datensatztyp und native Bibliothek passen nicht zusammen. Passenden Typ bzw. ein neues Bibliotheksschema wählen, bevor gespeichert wird.');return {origin:editing?.origin||null,file_bindings:JSON.parse(el('model-bindings').value),model_package:modelPackage,geometry:{mode:el('geometry-mode').value,status:el('geometry-status').value,nx_version:el('geometry-version').value,evidence:el('geometry-evidence').value,checks:Object.fromEntries(Object.keys(geometryTests).map(k=>[k,el('geometry-'+k).checked])),asset_hashes:hashes}}; }
  const fieldRowSize=10,fieldColumnSize=50;let fieldView=null,fieldPage=0,fieldPages=new Map(),fieldContext='';
  function fieldScope(){return JSON.stringify([editorGeneration,editing?.id??null,el('kind').value]);}
  function keyLocked(){return Boolean(modelPackage||editing&&(editing.origin||releases.some(release=>release.catalog?.exported_native?.[editing.id]||release.catalog?.exported_tools?.[editing.id])));}
  function fieldCurrent(view){return view===fieldView&&view?.valid===true&&view.scope===fieldScope()&&view.raw===el('cam').value;}
  function fieldWritesBlocked(){return saveBusy||NXAssets.isUploading()||Boolean(globalThis.NXSaves?.blocksSave?.());}
  function rowStructureReason(view){
    if(!fieldCurrent(view))return 'Aktuelle Spaltenmaske zuerst ausdrücklich laden.';
    if(fieldWritesBlocked())return 'Speichern oder Datei-Upload läuft oder ist noch ungeklärt.';
    if(modelPackage)return 'Gebundenes Modellpaket: Paketbindung vor einer Strukturänderung ausdrücklich lösen.';
    if(editing?.origin&&!editing.origin.editable)return 'Importierter Siemens-Standard: eigenen Kundenentwurf ableiten, nicht den Standard umbauen.';
    return '';
  }
  function rowRemovalReason(view){
    const reason=rowStructureReason(view);if(reason)return reason;
    if(view.cam.records.length<=1)return 'Die letzte Objektzeile bleibt erhalten. Zum Entfernen eines ganzen Objekts den Datensatz ausdrücklich stilllegen.';
    return '';
  }
  function editorControls(){globalThis.NXGraphicsBindings?.controls();const view=fieldView,current=view?fieldCurrent(view):false;el('native-field-controls').hidden=!view;
    el('native-row-policy').hidden=!view;if(!view)el('native-row-policy').textContent='';
    el('native-add-row').disabled=!current||Boolean(rowStructureReason(view))||!creationReady()||view.cam.library_id!==creationData.id||view.cam.nx_version!==creationData.nx_version;
    el('native-add-row').title=current?rowStructureReason(view):'Aktuelle Spaltenmaske und passendes Schema zuerst laden.';
    el('native-field-prev').disabled=!current||fieldPage===0;el('native-field-next').disabled=!current||(fieldPage+1)*fieldRowSize>=(view?.rows.length||0);
    el('native-field-jump').disabled=!current;el('native-field-row').disabled=!current;el('native-field-query').disabled=!current;el('native-field-block').disabled=!current;
    el('native-key-controls').hidden=!view||view.cam.records.length<2;el('native-key-new').disabled=!current||keyLocked()||fieldWritesBlocked();el('native-key-change').disabled=!current||keyLocked()||fieldWritesBlocked();
    if(view){el('native-key-state').textContent='Kennung der gesamten Gruppe: '+(view.key||'noch leer')+' · '+view.cam.records.length+' Zeilen. '+(keyLocked()?'Import-/Export-/Modellkennung geschützt.':'Neue Kennung nur ausdrücklich für die gesamte Gruppe setzen; Verweise und Dateinamen bleiben unverändert.');
      for(const input of el('native-fields').querySelectorAll('input')){input.disabled=!current;input.readOnly=input.dataset.nativeKey==='true'&&(keyLocked()||view.cam.records.length>1);}
      const query=el('native-field-query').value.trim().toLocaleLowerCase('de');
      for(const button of el('native-fields').querySelectorAll('[data-native-column-prev]'))button.disabled=!current||(fieldPages.get(Number(button.dataset.nativeColumnPrev))||0)===0;
      for(const button of el('native-fields').querySelectorAll('[data-native-column-next]')){const index=Number(button.dataset.nativeColumnNext),schema=view.blocks.get(view.cam.records[index].block_id),count=schema.fields.filter(field=>!query||field.toLocaleLowerCase('de').includes(query)).length;button.disabled=!current||((fieldPages.get(index)||0)+1)*fieldColumnSize>=count;}
      const reason=rowRemovalReason(view);el('native-row-policy').textContent=reason||'Einzelne Zeilen nur nach Bestätigung aus dem ungespeicherten Entwurf entfernen. SEQ/RTYPE, übrige Werte und FORMAT-Definitionen werden nicht automatisch geändert.';
      for(const button of el('native-fields').querySelectorAll('[data-native-remove]')){button.disabled=Boolean(reason);button.title=reason;}
    }
  }
  function staleFields(){if(!fieldView)return;fieldView.valid=false;el('native-field-state').textContent='CAM-Rohdaten oder Editor geändert. Alte Spaltenmaske gesperrt; „Spaltenmaske aus CAM-Daten anzeigen“ erneut ausführen. Keine alten Felder in neue Zeilen schreiben.';el('native-add-row').disabled=true;editorControls();}
  function requireFields(view){if(view!==fieldView)return false;if(!fieldCurrent(view)){staleFields();return false;}return true;}
  function fieldChanged(view){el('cam').value=JSON.stringify(view.cam,null,2);view.raw=el('cam').value;if(el('camstatus').value==='approved')el('camstatus').value='draft';NXNative.assetAdded();NXExports.invalidate();globalThis.NXEditorReferences?.sourceChanged();editorControls();}
  function removeRow(view,index,record){
    if(!requireFields(view))return;const reason=rowRemovalReason(view);if(reason)throw new Error(reason);
    if(view.cam.records[index]!==record)throw NXRequests.stale();
    if(!confirm('Zeile '+(index+1)+' · FORMAT '+record.block_id+' aus diesem ungespeicherten Entwurf entfernen?\nAlle Spalten dieser Zeile sind betroffen, auch ausgeblendete. Die übrigen Zeilen, SEQ/RTYPE-Werte und FORMAT-Definitionen bleiben unverändert; es wird nichts automatisch neu nummeriert. Alte Revisionen und NX-Dateien bleiben erhalten. Anschließend fachlich prüfen und separat als Entwurf speichern.'))return;
    if(!requireFields(view))return;const changed=rowRemovalReason(view);if(changed)throw new Error(changed);
    if(view.cam.records[index]!==record)throw NXRequests.stale();
    view.cam.records.splice(index,1);
    // Old column controls still contain original indices. Retire this mask
    // before shared invalidation/controls inspect the shortened group.
    fieldView=null;fieldChanged(view);renderFields({focusIndex:Math.min(index,view.cam.records.length-1)});
    message('Zeile '+(index+1)+' nur im Editor entfernt. Übrige Werte und FORMAT-Definitionen unverändert; Gruppenstruktur fachlich prüfen und separat als Entwurf speichern.');
  }
  function fieldModel(cam){if(!NXData.record(cam)||!Array.isArray(cam.schemas)||!Array.isArray(cam.records)||!cam.records.length)throw new Error('Native CAM-Daten benötigen FORMAT-Blöcke und mindestens eine vollständige Zeile.');
    const blocks=new Map(),keys=new Set();
    for(const schema of cam.schemas){if(!NXData.record(schema)||typeof schema.id!=='string'||!schema.id||blocks.has(schema.id)||typeof schema.class_name!=='string'||!(/^[A-Za-z0-9_]+$/).test(schema.class_name)||!Array.isArray(schema.fields)||!schema.fields.length||new Set(schema.fields).size!==schema.fields.length||schema.fields.some(field=>typeof field!=='string'||!(/^[A-Za-z0-9_]+$/).test(field)))throw new Error('FORMAT-Blöcke und Feldnamen müssen vollständig und eindeutig sein.');
      blocks.set(schema.id,schema);}
    for(const record of cam.records){const schema=blocks.get(record?.block_id);if(!schema||record.class_name!==schema.class_name||!NXData.record(record.values)||Object.keys(record.values).length!==schema.fields.length||schema.fields.some(field=>!Object.hasOwn(record.values,field)||typeof NXData.get(record.values,field)!=='string'))throw new Error('Zeile und FORMAT-Block passen nicht zusammen. Alle Spalten einschließlich neuer Spalten müssen als Textwerte vorhanden sein.');
      const key=schema.fields.includes('LIBRF')?'LIBRF':'NAME';if(!schema.fields.includes(key))throw new Error('FORMAT-Block enthält keine unterstützte Kennungsspalte LIBRF/NAME.');keys.add(NXData.get(record.values,key).trim());}
    if(keys.size!==1)throw new Error('Ein Stammdatensatz muss genau eine vollständige Bibliotheksgruppe enthalten. Unterschiedliche Kennungen in den Rohdaten zuerst klären.');
    return {cam,blocks,key:[...keys][0],rows:[],scope:fieldScope(),editor:editorGeneration,kind:el('kind').value,raw:el('cam').value,valid:true};
  }
  function drawFields(){const view=fieldView;if(!requireFields(view))return;el('native-fields').replaceChildren();
    const block=el('native-field-block').value,query=el('native-field-query').value.trim().toLocaleLowerCase('de');
    view.rows=view.cam.records.map((record,index)=>({record,index})).filter(item=>!block||item.record.block_id===block);
    fieldPage=Math.max(0,Math.min(fieldPage,Math.ceil(view.rows.length/fieldRowSize)-1));
    for(const {record,index} of view.rows.slice(fieldPage*fieldRowSize,(fieldPage+1)*fieldRowSize)){
      const box=document.createElement('fieldset'),legend=document.createElement('legend');box.dataset.nativeRow=String(index);legend.textContent='Zeile '+(index+1)+': '+record.block_id;box.append(legend);
      const grid=document.createElement('div');grid.className='grid';const schema=view.blocks.get(record.block_id),fields=schema.fields.filter(field=>!query||field.toLocaleLowerCase('de').includes(query));
      let column=Math.max(0,Math.min(fieldPages.get(index)||0,Math.ceil(fields.length/fieldColumnSize)-1));fieldPages.set(index,column);
      for(const field of fields.slice(column*fieldColumnSize,(column+1)*fieldColumnSize)){const label=document.createElement('label');label.textContent=field;const input=document.createElement('input');input.value=NXData.get(record.values,field);input.dataset.nativeField=field;input.dataset.nativeKey=String(field===(schema.fields.includes('LIBRF')?'LIBRF':'NAME'));
        input.oninput=run(()=>{if(!requireFields(view))return;if(input.dataset.nativeKey==='true'&&(keyLocked()||view.cam.records.length>1)){input.value=NXData.get(view.cam.records[index].values,field);return;}
          NXData.set(view.cam.records[index].values,field,input.value);if(input.dataset.nativeKey==='true')view.key=input.value.trim();fieldChanged(view);});label.append(input);grid.append(label);}
      box.append(grid);
      if(query||fields.length>fieldColumnSize){const state=document.createElement('p');state.textContent=(fields.length?column*fieldColumnSize+1:0)+'–'+Math.min((column+1)*fieldColumnSize,fields.length)+' von '+fields.length+' passenden Spalten · '+schema.fields.length+' Spalten im FORMAT insgesamt. Alle Werte bleiben im vollständigen Datensatz.';state.dataset.nativeColumns=String(index);box.append(state);
        if(fields.length>fieldColumnSize){const previous=button('Vorherige Spalten',()=>{if(requireFields(view)){fieldPages.set(index,column-1);drawFields();}}),next=button('Weitere Spalten',()=>{if(requireFields(view)){fieldPages.set(index,column+1);drawFields();}});previous.dataset.nativeColumnPrev=String(index);next.dataset.nativeColumnNext=String(index);previous.disabled=column===0;next.disabled=(column+1)*fieldColumnSize>=fields.length;box.append(previous,next);}}
      const remove=button('Zeile '+(index+1)+' aus Entwurf entfernen',()=>removeRow(view,index,record));remove.className='secondary';remove.dataset.nativeRemove=String(index);box.append(remove);
      el('native-fields').append(box);
    }
    el('native-field-state').textContent=(view.rows.length?fieldPage*fieldRowSize+1:0)+'–'+Math.min((fieldPage+1)*fieldRowSize,view.rows.length)+' von '+view.rows.length+' Zeilen im Blockfilter · '+view.cam.records.length+' Zeilen insgesamt. Nur Anzeige gefiltert; Speichern nutzt die vollständige Gruppe.';
    el('native-field-row').max=String(view.cam.records.length);editorControls();
  }
  function renderFields(settings={}){globalThis.NXEditorReferences?.sourceChanged();el('native-fields').replaceChildren();el('native-new').disabled=!creationReady();el('native-add-row').disabled=true;
    const scope=fieldScope();if(fieldContext!==scope&&!settings.keepNavigation){fieldPage=0;fieldPages.clear();el('native-field-query').value='';el('native-field-block').value='';el('native-field-row').value='';el('native-key-new').value='';}fieldContext=scope;fieldView=null;editorControls();
    let cam;try{cam=JSON.parse(el('cam').value);}catch{el('native-field-state').textContent='CAM-Rohdaten sind noch kein vollständiges JSON. Text bleibt erhalten; keine Spaltenmaske erzeugt.';return;}
    if(!NXData.record(cam)||!cam.records||!cam.schemas){if(NXData.record(cam)&&Object.keys(cam).length)document.querySelector('#editor-section details.technical').open=true;return;}
    try{fieldView=fieldModel(cam);}catch(error){document.querySelector('#editor-section details.technical').open=true;el('native-field-controls').hidden=false;el('native-field-state').textContent=error.message+' Rohdaten unverändert; keine unvollständige Spaltenmaske erzeugt.';return;}
    const blocks=Object.create(null);NXData.set(blocks,'','Alle FORMAT-Blöcke');for(const schema of cam.schemas)NXData.set(blocks,schema.id,schema.id+' · '+schema.class_name);options(el('native-field-block'),blocks);
    if(Number.isInteger(settings.focusIndex)){el('native-field-block').value='';el('native-field-query').value='';fieldPages.delete(settings.focusIndex);fieldPage=Math.floor(settings.focusIndex/fieldRowSize);}
    el('native-add-row').disabled=!creationData||cam.library_id!==creationData.id||cam.nx_version!==creationData.nx_version;drawFields();
  }
  function syncFields(){const view=fieldView;if(!view)return;if(view.valid&&view.raw===el('cam').value&&view.editor===editorGeneration&&view.kind===el('kind').value&&view.scope!==fieldScope()){view.scope=fieldScope();fieldContext=view.scope;editorControls();return;}if(view.valid&&!fieldCurrent(view))staleFields();else editorControls();}
  el('native-field-prev').onclick=run(()=>{if(requireFields(fieldView)){fieldPage--;drawFields();}});
  el('native-field-next').onclick=run(()=>{if(requireFields(fieldView)){fieldPage++;drawFields();}});
  el('native-field-block').onchange=run(()=>{if(requireFields(fieldView)){fieldPage=0;drawFields();}});
  el('native-field-query').oninput=run(()=>{if(requireFields(fieldView)){fieldPages.clear();drawFields();}});
  el('native-field-jump').onclick=run(()=>{if(!requireFields(fieldView))return;const index=Number(el('native-field-row').value)-1;if(!Number.isInteger(index)||index<0||index>=fieldView.cam.records.length)throw new Error('Eine vorhandene Zeilennummer zwischen 1 und '+fieldView.cam.records.length+' wählen.');el('native-field-block').value='';fieldPage=Math.floor(index/fieldRowSize);drawFields();el('native-fields').querySelector('[data-native-row="'+index+'"]')?.scrollIntoView({block:'nearest'});});
  el('native-field-row').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();el('native-field-jump').click();}};
  el('native-key-change').onclick=run(()=>{const view=fieldView;if(!requireFields(view))return;if(keyLocked()||fieldWritesBlocked())throw new Error('Import-/Export-/Modellkennung geschützt oder Speichern/Upload noch ungeklärt.');
    const value=el('native-key-new').value;if(!selectableKey(value)||!value.trim()||value!==value.trim())throw new Error('Nichtleere Kennung ohne Rand-Leerzeichen, Trennzeichen oder Zeilenumbrüche wählen (max. 1024 Zeichen).');if(value===view.key)return;
    if(!confirm('Kennung '+JSON.stringify(view.key)+' → '+JSON.stringify(value)+' in allen '+view.cam.records.length+' Zeilen dieser Gruppe ausdrücklich ändern?\nAuch ausgeblendete Zeilen sind betroffen. Nur ungespeicherter Editor, keine Dateiumbenennung oder Änderung von Verweisen/GRAPHICS. Anschließend fachlich prüfen und als Entwurf speichern.'))return;
    for(const record of view.cam.records){const schema=view.blocks.get(record.block_id);NXData.set(record.values,schema.fields.includes('LIBRF')?'LIBRF':'NAME',value);}view.key=value;fieldChanged(view);drawFields();message('Kennung nur in der vollständigen Editorgruppe geändert. Verweise/Dateinamen unverändert; separat prüfen und als Entwurf speichern.');
  });
  function importBody() { return {source:el('source').value,...NXFolders.fields(),nx_version:el('nxversion').value,name:el('release-name').value,normalize_libraries:el('normalize').checked,legacy_version:el('legacy-version').value,defaults:JSON.parse(el('import-defaults').value),resolutions:JSON.parse(el('import-resolutions').value),reference_resolutions:NXReferences.fields(),legacy_omissions:JSON.parse(el('legacy-omissions').value)}; }
  let importOwner=null,catalogOwner=null,jobsBlocked=true,conflictView=null;
  // Changing decisions keeps controls usable; changing their source retires them.
  const conflictScope=()=>JSON.stringify([...[ 'source','source-path','target','target-path','legacy-version','nxversion','upgrade-mode'].map(id=>el(id).value),el('normalize').checked]);
  function clearConflictView(){conflictView=null;el('conflicts').replaceChildren();el('reference-applied').replaceChildren();}
  function importControls(){el('import').disabled=jobsBlocked||Boolean(importOwner)||!importPreview?.can_import;el('catalog-import').disabled=jobsBlocked||Boolean(catalogOwner)||selectionBlocked()||!catalogPreview?.can_import||!catalogPreview?.count;el('catalog-preview').disabled=Boolean(catalogReadOwner)||selectionBlocked();}
  function invalidateCatalog(){NXRequests.invalidate('native.catalog.');catalogPreview=null;catalogOwner=catalogReadOwner=null;catalogPage=0;importControls();el('catalog-objects').replaceChildren();el('catalog-page-state').textContent='';el('catalog-source-state').textContent='';el('catalog-objects-prev').disabled=true;el('catalog-objects-next').disabled=true;el('catalog-state').textContent='Auswahl oder Daten geändert. Stammdatenvorschau neu prüfen.';}
  function invalidateImport() { if(conflictView&&conflictView.scope!==conflictScope())clearConflictView();NXRequests.invalidate('native.import.');importPreview=null;importOwner=null;el('import-preview').disabled=false;el('import').disabled=true;el('import-state').textContent='Vorschau neu berechnen, bevor importiert werden kann.';globalThis.NXUpgrade?.invalidate(); }
  function renderConflicts(preview,alive=()=>el('upgrade-mode').value==='prepared') {
    const view={scope:conflictScope()};conflictView=view;
    const visible=()=>conflictView===view&&view.scope===conflictScope()&&workspace==='update'&&Boolean(token)&&alive();
    NXReferences.show(preview,visible);
    el('conflicts').replaceChildren();preview.conflicts.forEach(c=>{const tr=document.createElement('tr'),current=()=>visible()&&tr.isConnected;cell(tr,c.library+' / '+(c.key||'—'));cell(tr,c.source||'—');cell(tr,c.reason);const td=cell(tr,'');td.append(button('Vergleich',()=>{if(current())details(c);}));
      if(c.type==='reference')td.append(NXReferences.control(c,current));
      else if(c.key){const select=document.createElement('select');options(select,{'':'Noch entscheiden',standard:'Ziel behalten / Kundengruppe nicht übernehmen',...(c.type==='data'?{customer:'Kundengruppe übernehmen'}:{})});select.onchange=action(()=>{if(!current())return;invalidateImport();const decisions=JSON.parse(el('import-resolutions').value),choices=NXData.branch(NXData.branch(decisions,c.library),c.source);if(select.value)NXData.set(choices,c.key,select.value);else NXData.remove(choices,c.key);el('import-resolutions').value=JSON.stringify(decisions,null,2);});td.append(select);}
      else if(c.library==='unmapped-legacy-database'){const select=document.createElement('select');options(select,{'':'Noch klären',omit:'Diese zusätzliche Alt-Datenbank ausdrücklich nicht übernehmen'});select.onchange=action(()=>{if(!current())return;const omitted=JSON.parse(el('legacy-omissions').value).filter(p=>p!==c.source);if(select.value==='omit')omitted.push(c.source);el('legacy-omissions').value=JSON.stringify(omitted,null,2);invalidateImport();});td.append(select);}el('conflicts').append(tr);});
  }
  async function loadInventory() {
    if(!el('library-release').value)throw new Error('Zuerst einen vollständigen Datenstand importieren.');
    const release=el('library-release').value,context=inventoryKey(release);if(!context)throw new Error('Zuerst einen vollständigen Datenstand importieren.');clearInventory();
    const request=NXRequests.begin('native.inventory.list',()=>el('library-release').value===release&&inventoryKey(release)===context);
    const data=await request.wait(api('releases/'+release+'/libraries'));if(!Array.isArray(data.libraries)||new Set(data.libraries.map(item=>item.id)).size!==data.libraries.length||data.libraries.some(item=>!creationSpecs.has(item.id)))throw new Error('Keine eindeutige unterstützte Bibliotheksliste.');details(data);inventoryRelease=release;inventoryContext=context;
    options(el('native-library'),Object.fromEntries(data.libraries.map(s=>[s.id,`${s.label} · ${s.id} · ${s.object_count??'?'} Objekte`])));
    el('catalog-libraries').replaceChildren();data.libraries.forEach(s=>{const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.value=s.id;check.checked=true;check.onchange=invalidateCatalog;label.append(check,document.createTextNode(`${s.label} (${s.id})`));el('catalog-libraries').append(label);});clearObjectPage();invalidateCatalog();renderSelection();
  }
  function objectButtons(){el('library-prev').disabled=Boolean(objectOwner)||!nativeData||nativeData.offset===0;el('library-next').disabled=Boolean(objectOwner)||!nativeData||nativeData.offset+nativeData.items.length>=nativeData.filtered;}
  function clearObjectPage(){NXRequests.invalidate('native.inventory.objects');objectOwner=null;nativeData=null;el('library-read').disabled=false;el('native-objects').replaceChildren();el('library-result-state').textContent='Bibliothek/Filter gewählt. Objekte laden; keine automatische NX-Freigabe.';objectButtons();renderSelection();}
  function checkObjectPage(data,release,library,offset,limit=100){
    const source=releases.find(item=>item.id===release),spec=creationSpecs.get(library),fail=()=>{throw new Error('Keine vollständige passende manifestgebundene Bibliotheksseite. Bibliothek erneut laden.');};
    if(!schemaNat(offset)||!Number.isSafeInteger(limit)||limit<1||limit>500||!NXData.record(data)||!spec||!source||['id','kind','label','path','legacy_path','graphics_dir'].some(key=>data[key]!==spec[key])||data.nx_version!==source.nx_version||data.offset!==offset||data.limit!==limit||!schemaNat(data.total)||!schemaNat(data.filtered)||data.filtered>data.total||!Array.isArray(data.items)||data.items.length!==Math.min(limit,Math.max(0,data.filtered-offset))||data.integrity_checked!==true||data.scope!=='selected_native_library_bytes'||data.automatic_approval!==false||!schemaHex(data.input_sha256)||!schemaHex(data.manifest_sha256)||!Array.isArray(data.schemas))fail();
    const blocks=new Map(),occurrences=new Map();
    for(const block of data.schemas){
      if(!NXData.record(block)||typeof block.class_name!=='string'||!(/^[A-Za-z0-9_]+$/).test(block.class_name)||!Array.isArray(block.fields)||!block.fields.length||new Set(block.fields).size!==block.fields.length||!block.fields.some(field=>field==='LIBRF'||field==='NAME')||block.fields.some(field=>typeof field!=='string'||!(/^[A-Za-z0-9_]+$/).test(field))||NXUpgradePlan.hash([block.class_name,block.fields])!==block.schema_hash)fail();
      const occurrence=(occurrences.get(block.class_name)||0)+1;if(block.id!==block.class_name+':'+occurrence||blocks.has(block.id))fail();occurrences.set(block.class_name,occurrence);blocks.set(block.id,block);
    }
    const keys=new Set();for(const item of data.items){
      const cam=item?.cam;if(!NXData.record(item)||typeof item.key!=='string'||!item.key||keys.has(item.key)||!NXData.record(cam)||Object.keys(cam).sort().join(',')!=='library_id,nx_version,records,schemas'||cam.library_id!==library||cam.nx_version!==source.nx_version||!Array.isArray(cam.schemas)||!cam.schemas.length||!Array.isArray(cam.records)||!cam.records.length)fail();keys.add(item.key);
      const subset=new Map(),used=new Set();for(const block of cam.schemas){const expected=blocks.get(block?.id);if(!expected||subset.has(block.id)||block.class_name!==expected.class_name||block.schema_hash!==expected.schema_hash||JSON.stringify(block.fields)!==JSON.stringify(expected.fields))fail();subset.set(block.id,expected);}
      for(const record of cam.records){const block=subset.get(record?.block_id);if(!NXData.record(record)||!block||record.class_name!==block.class_name||!NXData.record(record.values)||Object.keys(record.values).length!==block.fields.length||block.fields.some(field=>!Object.hasOwn(record.values,field)||typeof NXData.get(record.values,field)!=='string')||NXData.get(record.values,block.fields.includes('LIBRF')?'LIBRF':'NAME')!==item.key)fail();used.add(block.id);}
      if(used.size!==subset.size||JSON.stringify([...subset.keys()])!==JSON.stringify([...blocks.keys()].filter(id=>used.has(id))))fail();
    }
  }
  async function loadNative(offset=0) {
    if(objectOwner)return;
    const release=el('library-release').value,library=el('native-library').value,query=el('library-query').value,context=inventoryContext;
    if(!library||release!==inventoryRelease||!context||context!==inventoryKey(release))throw new Error('Bibliotheken für diesen Datenstand zuerst laden.');
    if(!schemaNat(offset))throw new Error('Ungültiger Seitenanfang.');
    const request=NXRequests.begin('native.inventory.objects',()=>release===el('library-release').value&&library===el('native-library').value&&query===el('library-query').value&&context===inventoryContext&&context===inventoryKey(release));objectOwner=request;nativeData=null;el('native-objects').replaceChildren();if(selectedObjects.has(library)){unverifiedLibraries.add(library);invalidateCatalog();}else if(catalogPreview?.libraries.includes(library))invalidateCatalog();el('library-read').disabled=true;objectButtons();renderSelection();
    el('library-result-state').textContent='Bibliotheksobjekte werden geladen …';
    try{const data=await request.wait(api('releases/'+release+'/libraries/'+encodeURIComponent(library)+'?'+new URLSearchParams({query,offset,limit:100})));
      checkObjectPage(data,release,library,offset);
      if(selectionPins.has(library)&&selectionPins.get(library)!==data.input_sha256){clearSelection();message('Bibliotheksstand geändert. Alte Objektauswahl verworfen; neue Auswahl und Vorschau erforderlich.',true);}
      const rows=document.createDocumentFragment(),current=()=>{if(nativeData!==data||objectOwner||!request.current())throw NXRequests.stale();};
      data.items.forEach(item=>{const tr=document.createElement('tr'),check=document.createElement('input');check.type='checkbox';check.dataset.catalogKey=item.key;check.dataset.catalogLibrary=library;check.setAttribute('aria-label','Objekt '+library+' / '+item.key+' auswählen');check.disabled=!selectionSupported||!selectableKey(item.key);check.onchange=action(()=>{current();selectObject(library,item.key,check.checked);});const choice=cell(tr,'');choice.append(check);if(!selectableKey(item.key))choice.append(document.createTextNode('Schlüssel außerhalb Auswahlgrenze'));cell(tr,item.key);cell(tr,item.cam.records.length+' Zeilen');const actions=cell(tr,'');actions.append(button('Datensatz ansehen',()=>{current();details(item.cam);}));const clone=button('Neuen Entwurf ableiten',()=>{current();NXClone.open(release,library,item.key,data.input_sha256,item.cam);});clone.disabled=!globalThis.NXClone?.supported()||!selectableKey(item.key);actions.append(clone);rows.append(tr);});
      details(data);nativeData=data;unverifiedLibraries.delete(library);el('native-objects').replaceChildren(rows);renderSelection();
      el('library-result-state').textContent=(data.items.length?offset+1:0)+'–'+(offset+data.items.length)+' von '+data.filtered+' Treffern · '+data.total+' Objekte insgesamt. Ganze FORMAT-/RTYPE-/SEQ-Gruppen; ausgewählte DAT-Datei SHA-geprüft, keine NX-Freigabe.';
    }catch(error){if(request.current()&&!NXRequests.isStale(error)){nativeData=null;el('native-objects').replaceChildren();el('library-result-state').textContent='Bibliothek nicht aktualisiert: '+error.message;}throw error;}
    finally{if(objectOwner===request){objectOwner=null;el('library-read').disabled=false;objectButtons();renderSelection();}}
  }
  function clearCreation() {
    NXRequests.invalidate('native.creation.');
    creationInventoryOwner=creationSchemaOwner=null;creationInventory=creationData=null;creationRelease=creationContext='';el('create-library').replaceChildren();el('native-block').replaceChildren();
    el('create-schema-state').textContent=schemaReadEnabled?'NX-Datenstand wählen und Bibliotheken für den Entwurf laden. Vorhandene Editorwerte bleiben erhalten.':'Schemaabfrage benötigt einen unterstützten Serververtrag. Server aktualisieren; Editorwerte bleiben erhalten.';creationControls();
  }
  function clearInventory() { NXRequests.invalidate('native.inventory.');clearSelection();clearObjectPage();inventoryRelease=inventoryContext='';el('native-library').replaceChildren();el('catalog-libraries').replaceChildren(); }
  function syncReleaseSelections() {
    syncFields();
    if(inventoryRelease && (inventoryRelease!==el('library-release').value||inventoryContext!==inventoryKey(inventoryRelease)))clearInventory();
    if(creationRelease && (creationRelease!==el('create-release').value||creationContext!==creationReleaseKey(creationRelease)))clearCreation();
    const signature=list=>list.map(value=>value.id+':'+value.revision).sort().join('|');
    if(catalogPreview&&signature(resources)!==signature(catalogPreview.catalog_revisions))invalidateCatalog();
  }
  async function loadCreationInventory() {
    if(!schemaReadEnabled)throw new Error('Server unterstützt keine reine Schemaabfrage. Server aktualisieren.');
    const release=el('create-release').value,context=creationReleaseKey(release);if(!context)throw new Error('Zuerst einen vollständigen NX-Datenstand importieren.');
    clearCreation();const request=NXRequests.begin('native.creation.inventory',()=>el('create-release').value===release&&context===creationReleaseKey(release));creationInventoryOwner=request;creationControls();el('create-schema-state').textContent='Lese nur Bibliotheksmetadaten aus dem verwalteten Manifest …';
    try{const data=await request.wait(api('releases/'+release+'/library-schemas'));checkCreationReport(data,release,'native_schema_inventory');
      if(data.files_verified!==false||!Array.isArray(data.libraries)||data.libraries.length>creationSpecs.size||new Set(data.libraries.map(item=>item.id)).size!==data.libraries.length)throw new Error('Keine eindeutige reine Schema-Bibliotheksliste.');
      data.libraries.forEach(item=>{checkCreationEntry(item);if(Object.hasOwn(item,'schemas'))throw new Error('Schema-Inventar darf noch keine Definitionen laden.');});creationInventory=data;creationRelease=release;creationContext=context;
      options(el('create-library'),Object.fromEntries(data.libraries.map(item=>[item.id,item.label+' · '+item.id+(item.can_read_schema?'':' · leer/über Lesergrenze')])));
      el('create-schema-state').textContent=data.libraries.length?'Bibliotheksmetadaten geladen; noch keine DAT-Datei oder Objektwerte gelesen. Bibliothek wählen und Schema laden.':'Keine unterstützte native Bibliothek im gewählten Manifest. Editor unverändert.';
    }catch(error){if(request.current()&&!NXRequests.isStale(error))el('create-schema-state').textContent='Schema-Bibliotheksliste nicht bestätigt: '+error.message;throw error;}
    finally{if(creationInventoryOwner===request){creationInventoryOwner=null;creationControls();}}
  }
  const schemaHex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value),schemaNat=value=>Number.isSafeInteger(value)&&value>=0;
  function checkCreationReport(value,release,scope){const source=releases.find(item=>item.id===release),body={...value};delete body.report_sha256;
    if(!source||!NXData.record(value)||value.contract_version!==1||value.scope!==scope||value.release_id!==release||value.nx_version!==source.nx_version||value.release_state?.status!==source.status||value.release_state?.folder!==source.folder||value.read_only!==true||value.sqlite_read_only!==true||value.sqlite_read_transaction!==true||value.reference_validation!==false||value.nx_files_written!==false||value.automatic_approval!==false||!schemaHex(value.inventory_sha256)||!schemaHex(value.manifest_sha256)||!schemaHex(value.report_sha256)||NXUpgradePlan.hash(body)!==value.report_sha256||value.max_library_bytes!==32*1024*1024||value.max_report_bytes!==4*1024*1024||Object.hasOwn(value,'items')||Object.hasOwn(value,'records'))throw new Error('Kein vollständiger unveränderter Schema-Lesevertrag.');
  }
  function checkCreationEntry(value){const spec=creationSpecs.get(value?.id);if(!spec||['id','kind','label','path','graphics_dir'].some(key=>value[key]!==spec[key])||typeof value.manifest_path!=='string'||value.manifest_path.toLowerCase()!==spec.path.toLowerCase()||!schemaNat(value.input_size)||!schemaHex(value.input_sha256)||value.can_read_schema!==(value.input_size>0&&value.input_size<=32*1024*1024)||Object.hasOwn(value,'items')||Object.hasOwn(value,'records'))throw new Error('Kein passender manifestgebundener Schema-Bibliothekseintrag.');}
  function sampleEditorSnapshot(){const file=el('assetfile').files[0];return JSON.stringify([editorGeneration,editing?.id??null,editing?.revision??null,modelPackage,hashes,[...el('editor-section').querySelectorAll('input,select,textarea')].map(node=>[node.id,node.value,node.checked]),file?[file.name,file.size,file.lastModified]:null]);}
  async function loadToolExample(release){
    const blocked=()=>{if(fieldWritesBlocked())throw new Error('Laufenden oder ungeklärten Speichervorgang/Upload zuerst klären.');};blocked();
    const id=release?.id,context=inventoryKey(id);if(!context||context!==JSON.stringify([id,release.nx_version,release.folder]))throw new Error('Vollständigen aktuellen NX-Datenstand zuerst im Bestand laden.');
    const editor=sampleEditorSnapshot(),request=NXRequests.begin('editor.sample',()=>editor===sampleEditorSnapshot()&&context===inventoryKey(id));
    const data=await request.wait(api('releases/'+id+'/libraries/tool-metric?offset=0&limit=1'));
    checkObjectPage(data,id,'tool-metric',0,1);if(data.filtered!==data.total)throw new Error('Ungefiltertes vollständiges Werkzeugbeispiel erneut laden.');
    if(!data.items.length)throw new Error('Werkzeugbibliothek enthält kein Beispiel. Entwurf über das native Schema anlegen.');
    const sample=structuredClone(data.items[0].cam),key='CUSTOM_'+crypto.randomUUID().replaceAll('-','');
    sample.records.forEach(record=>{const schema=sample.schemas.find(schema=>schema.id===record.block_id),field=schema.fields.includes('LIBRF')?'LIBRF':'NAME';NXData.set(record.values,field,key);});
    const raw=JSON.stringify(sample,null,2);blocked();request.check();
    if(!confirm('Vollständige Werkzeuggruppe mit '+sample.records.length+' Zeilen als NEUEN ungespeicherten Entwurf öffnen? Aktuelle Editorwerte werden ersetzt. Nur Kennungsspalten erhalten eine neue CUSTOM-Kennung; Maße, Verweise und Graphics-Namen bleiben zur fachlichen Prüfung erhalten. Keine ERP-Daten, Anhänge, Modellbindung oder Freigaben übernehmen. Noch nichts speichern oder exportieren.'))return;
    blocked();request.check();openEditor();el('cam').value=raw;el('geometry-version').value=data.nx_version;renderFields();NXExports.invalidate();
    message('Vollständige native Werkzeuggruppe als ungespeichertes Beispiel geladen. Alle Maße, Referenzen und Geometrie vor Freigabe fachlich prüfen.');
  }
  async function loadCreationSchema() {
    const release=el('create-release').value,library=el('create-library').value;
    if(!schemaReadEnabled||!library||!creationInventory||creationRelease!==release||creationContext!==creationReleaseKey(release))throw new Error('Bibliotheken für den gewählten NX-Datenstand zuerst laden.');
    const inventory=creationInventory,entry=inventory.libraries.find(item=>item.id===library);if(!entry?.can_read_schema)throw new Error('Diese Bibliothek kann nicht als Schema gelesen werden.');
    creationData=null;el('native-block').replaceChildren();const request=NXRequests.begin('native.creation.schema',()=>release===el('create-release').value&&library===el('create-library').value&&inventory===creationInventory&&creationContext===creationReleaseKey(release));creationSchemaOwner=request;creationControls();el('create-schema-state').textContent='Prüfe nur die gewählte Bibliothek; keine Objektseite laden …';
    try{const data=await request.wait(api('releases/'+release+'/library-schemas/'+encodeURIComponent(library)+'?'+new URLSearchParams({inventory_sha256:inventory.inventory_sha256})));checkCreationReport(data,release,'native_library_schema');checkCreationEntry(data);
      if(data.id!==library||data.inventory_sha256!==inventory.inventory_sha256||data.manifest_sha256!==inventory.manifest_sha256||data.input_sha256!==entry.input_sha256||data.input_size!==entry.input_size||data.manifest_path!==entry.manifest_path||data.integrity_checked!==true||data.object_values_included!==false||!Array.isArray(data.schemas)||!data.schemas.length||data.schema_count!==data.schemas.length||data.schema_count>1000||!schemaNat(data.row_count)||NXUpgradePlan.hash(data.schemas)!==data.schema_sha256)throw new Error('Kein passendes vollständiges Schema ohne Objektwerte.');
      const ids=new Set(),occurrences=new Map();let fields=0;for(const block of data.schemas){if(!NXData.record(block)||typeof block.id!=='string'||!block.id||ids.has(block.id)||typeof block.class_name!=='string'||!(/^[A-Za-z0-9_]+$/).test(block.class_name)||!Array.isArray(block.fields)||!block.fields.length||new Set(block.fields).size!==block.fields.length||!block.fields.some(field=>field==='LIBRF'||field==='NAME')||block.fields.some(field=>typeof field!=='string'||!(/^[A-Za-z0-9_]+$/).test(field))||NXUpgradePlan.hash([block.class_name,block.fields])!==block.schema_hash)throw new Error('Ungültiger FORMAT-Block im Entwurfsschema.');const occurrence=(occurrences.get(block.class_name)||0)+1;if(block.id!==block.class_name+':'+occurrence)throw new Error('FORMAT-Folge im Entwurfsschema ist unvollständig.');occurrences.set(block.class_name,occurrence);ids.add(block.id);fields+=block.fields.length;}
      if(fields>100000)throw new Error('Schema überschreitet die Feldgrenze.');creationData=data;options(el('native-block'),Object.fromEntries(data.schemas.map(block=>[block.id,block.id+' · '+block.fields.length+' Spalten'])));
      el('create-schema-state').textContent=`Entwurfsschema: ${library} · NX${data.nx_version} · ${data.schemas.length} FORMAT-Blöcke. DAT-Bytes geprüft; keine Objektwerte geladen. Editorwerte sind noch unverändert.`;
    }catch(error){if(request.current()&&!NXRequests.isStale(error))el('create-schema-state').textContent='Entwurfsschema nicht bestätigt: '+error.message;throw error;}
    finally{if(creationSchemaOwner===request){creationSchemaOwner=null;creationControls();}}
  }
  function catalogBody() {
    const standard=el('include-standard').checked;
    if(el('catalog-scope').value!=='objects')return {libraries:[...el('catalog-libraries').querySelectorAll('input:checked')].map(i=>i.value),include_standard:standard};
    if(!selectionSupported)throw new Error('Server unterstützt keine Einzelobjektübernahme.');
    if(!selectionCount())throw new Error('Mindestens ein vollständiges Objekt in der Bibliotheksansicht auswählen. Leere Auswahl übernimmt niemals ganze Bibliotheken.');
    if(selectionBlocked())throw new Error('Ausgewählte Bibliotheken erneut laden, bevor Vorschau oder Übernahme möglich sind.');
    const selection=Object.create(null);for(const [library,keys] of [...selectedObjects].sort())NXData.set(selection,library,[...keys].sort());
    return {libraries:Object.keys(selection),include_standard:standard,selection};
  }
  function checkCatalog(report,release,body){const hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value),integer=value=>Number.isSafeInteger(value)&&value>=0;
    const sameLibraries=value=>Array.isArray(value)&&JSON.stringify([...value].sort())===JSON.stringify([...body.libraries].sort());
    const source=releases.find(item=>item.id===release),state=report?.source_state;
    if(!NXData.record(report)||report.commit_contract_version!==1||report.draft_only!==true||report.nx_files_changed!==false||report.format_comparison!=='same_block_id_only'||report.release_id!==release||report.include_standard!==body.include_standard||!sameLibraries(report.libraries)||!hex(report.preview_hash)||!hex(report.catalog_state_sha256)||!NXData.record(state)||state.nx_version!==source?.nx_version||state.folder!==source?.folder||!hex(state.manifest_sha256)||!NXData.record(report.source_inputs)||!NXData.record(report.standard_inputs)||!sameLibraries(Object.keys(report.source_inputs))||!sameLibraries(Object.keys(report.standard_inputs))||Object.values(report.source_inputs).some(value=>!hex(value))||Object.values(report.standard_inputs).some(value=>value!==null&&!hex(value))||!Array.isArray(report.objects)||!integer(report.count)||report.count!==report.objects.length||!Array.isArray(report.conflicts)||report.can_import!==(report.conflicts.length===0)||!Array.isArray(report.already_managed)||!Array.isArray(report.catalog_revisions)||report.catalog_revisions.some(item=>typeof item.id!=='string'||!integer(item.revision)||item.revision<1)||JSON.stringify(report.catalog_revisions.map(item=>item.id+':'+item.revision).sort())!==JSON.stringify(resources.map(item=>item.id+':'+item.revision).sort())||JSON.stringify(report.selection??null)!==JSON.stringify(body.selection??null))throw new Error('Kein unterstützter quell-/kataloggebundener Übernahmevertrag. Bestand aktualisieren und Vorschau erneut prüfen.');
    const identifiers=new Set();for(const item of report.objects){if(!NXData.record(item)||typeof item.id!=='string'||identifiers.has(item.id)||!body.libraries.includes(item.library)||libraryKinds.get(item.library)!==item.kind||typeof item.key!=='string'||body.selection&&!NXData.get(body.selection,item.library)?.includes(item.key)||typeof item.name!=='string'||typeof item.editable!=='boolean'||!integer(item.record_count)||item.record_count<1||!Array.isArray(item.format_blocks)||!item.format_blocks.length)throw new Error('Ungültige vollständige Objektgruppe in der Übernahmevorschau.');identifiers.add(item.id);
      const blocks=new Set();let count=0;for(const block of item.format_blocks){if(!NXData.record(block)||typeof block.id!=='string'||blocks.has(block.id)||typeof block.class_name!=='string'||!integer(block.record_count)||block.record_count<1||!Array.isArray(block.fields)||!block.fields.length||new Set(block.fields).size!==block.fields.length||block.fields.some(field=>typeof field!=='string'||!/^\w+$/.test(field))||(!block.fields.includes('LIBRF')&&!block.fields.includes('NAME'))||block.baseline_fields!==null&&(!Array.isArray(block.baseline_fields)||block.baseline_fields.some(field=>typeof field!=='string')))throw new Error('Ungültige FORMAT-Metadaten in der Übernahmevorschau.');blocks.add(block.id);count+=block.record_count;}if(count!==item.record_count)throw new Error('Zeilenanzahl der vollständigen Objektgruppe stimmt nicht.');
    }
    if(report.already_managed.some(item=>!NXData.record(item)||typeof item.library!=='string'||typeof item.key!=='string'||typeof item.resource_id!=='string'||!integer(item.revision)||item.revision<1)||report.conflicts.some(item=>!NXData.record(item)||typeof item.reason!=='string'))throw new Error('Unvollständige Besitz-/Konfliktmetadaten.');
  }
  function formatDetails(item){return {library:item.library,key:item.key,record_count:item.record_count,
    format_comparison:'Nur gleiche FORMAT-IDs vergleichen; keine automatische Spaltenzuordnung oder Schemaänderung.',
    format_blocks:item.format_blocks.map(block=>({...block,source_only_fields:block.baseline_fields===null?null:block.fields.filter(field=>!block.baseline_fields.includes(field)),baseline_only_fields:block.baseline_fields===null?null:block.baseline_fields.filter(field=>!block.fields.includes(field))})),
    automatic_save:false,automatic_approval:false,nx_files_changed:false};}
  function formatView(item){const box=document.createElement('details'),summary=document.createElement('summary');box.className='catalog-formats';summary.textContent='FORMAT-Spalten anzeigen';box.append(summary);
    const table=document.createElement('table'),header=document.createElement('thead'),titles=document.createElement('tr'),body=document.createElement('tbody');
    ['FORMAT / Zeilen','Quellspalten (bleiben erhalten)','Siemens-Baseline bei gleicher FORMAT-ID','Spaltenabweichung'].forEach(title=>cell(titles,title));header.append(titles);table.append(header,body);
    for(const block of formatDetails(item).format_blocks){const row=document.createElement('tr');cell(row,block.id+' · '+block.record_count+' Zeilen');cell(row,block.fields.join(', '));cell(row,block.baseline_fields?.join(', ')??'Kein gleicher FORMAT-Block in der verfügbaren Baseline.');cell(row,block.baseline_fields===null?'Keine automatische Zuordnung.':'Nur Quelle: '+(block.source_only_fields.join(', ')||'keine')+' · Nur Baseline: '+(block.baseline_only_fields.join(', ')||'keine'));body.append(row);}
    const scroll=document.createElement('div');scroll.className='scroll';scroll.append(table);box.append(scroll);
    const note=document.createElement('small');note.textContent='Nur Metadatenvergleich gleicher FORMAT-IDs; keine semantische Zuordnung oder Schemaänderung. Originalzeilen und Spalten werden übernommen; anschließend fachlich prüfen. ';box.append(note,button('Metadaten ansehen',()=>details(formatDetails(item))));return box;}
  function renderCatalog(report){el('catalog-objects').replaceChildren();
    const items=[...report.objects.map(item=>({item,mode:'new'})),...report.already_managed.map(item=>({item,mode:'managed'})),...report.conflicts.map(item=>({item,mode:'conflict'}))];
    catalogPage=Math.max(0,Math.min(catalogPage,Math.ceil(items.length/100)-1));
    for(const {item,mode} of items.slice(catalogPage*100,(catalogPage+1)*100)){const row=document.createElement('tr');cell(row,item.library+' / '+item.key);
      if(mode==='new'){cell(row,'Neuer Entwurf · '+item.record_count+' Zeilen · '+item.format_blocks.length+' FORMAT-Blöcke');const hint=cell(row,item.editable?'Kundenobjekt; CAM-/Geometrieprüfung noch erforderlich.':'Siemens-/unbekannter Standard; keine Überschreibung erlaubt.');hint.append(formatView(item));}
      else if(mode==='managed'){cell(row,'Bereits verwaltet · Revision '+item.revision);cell(row,'').append(button('Stammdaten öffnen',()=>{const resource=resources.find(value=>value.id===item.resource_id);if(!resource)throw new Error('Stammdatenbestand zuerst aktualisieren.');openEditor(resource);}));}
      else{cell(row,'Übernahme gesperrt');cell(row,item.reason);}
      el('catalog-objects').append(row);
    }
    el('catalog-page-state').textContent=(items.length?catalogPage*100+1:0)+'–'+Math.min((catalogPage+1)*100,items.length)+' von '+items.length+' Vorschauobjekten. Gesamte geprüfte Auswahl wird übernommen, nicht nur diese Seite.';
    el('catalog-objects-prev').disabled=catalogPage===0;el('catalog-objects-next').disabled=(catalogPage+1)*100>=items.length;
    const baselines=Object.values(report.standard_inputs).filter(value=>value!==null).length;
    el('catalog-source-state').textContent=Object.keys(report.source_inputs).length+' Quellbibliotheken und '+baselines+' Siemens-Baselines gebunden. Quelle, Baseline und Kataloginhalt werden vor dem Commit erneut geprüft – auch bei ganzen Bibliotheken. Nur Entwürfe; keine Dateiänderung, ERP-Anlage oder NX-Freigabe.';
  }
  Object.entries(geometryTests).forEach(([key,text])=>{const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.id='geometry-'+key;label.append(input,document.createTextNode(text));el('geometry-checks').append(label);});
  el('fields-refresh').onclick=run(renderFields);
  el('cam').oninput=()=>{staleFields();if(el('camstatus').value==='approved')el('camstatus').value='draft';NXNative.assetAdded();NXExports.invalidate();};
  ['input','change'].forEach(event=>el('editor-section').addEventListener(event,()=>NXRequests.invalidate('editor.sample')));
  el('kind').addEventListener('change',()=>{if(fieldView&&!fieldCurrent(fieldView))staleFields();});
  el('geometry-bind').onclick=run(async()=>{hashes={};el('geometry-status').value='needs_preparation';Object.keys(geometryTests).forEach(key=>el('geometry-'+key).checked=false);
    const editor=editorGeneration,ids=el('assetids').value,request=NXRequests.begin('native.geometry-bind',()=>editor===editorGeneration&&ids===el('assetids').value);
    const assets=await request.wait(Promise.all(NXAssets.identifiers().map(id=>NXAssets.readMetadata(id))));hashes=Object.fromEntries(assets.map(a=>[a.id,a.sha256]));details(assets);message('Prüfbeleg an diese Dateiprüfsummen gebunden. Geometriechecks erneut ausdrücklich bestätigen; keine automatische Geometrieprüfung.');});
  ['source','nxversion','legacy-version','normalize','import-defaults','import-resolutions','legacy-omissions'].forEach(id=>{el(id).oninput=invalidateImport;el(id).onchange=invalidateImport;});
  el('import-preview').onclick=run(async()=>{invalidateImport();clearConflictView();const body=importBody(),snapshot=JSON.stringify(body);if(!body.normalize_libraries)throw new Error('Standardbibliotheken-Zusammenführung aktivieren.');const request=NXRequests.begin('native.import.preview',()=>snapshot===JSON.stringify(importBody()));el('import-preview').disabled=true;message('Prüfe Quell-/Zielordner und vergleiche Bibliotheken mit Siemens-Standards …');try{importPreview=await request.wait(api('import-preview','POST',body));renderConflicts(importPreview);details(importPreview);importControls();el('import-state').textContent=importPreview.can_import?'Vorschau konfliktfrei. Neuer Zielordner: '+(importPreview.host_destination||importPreview.destination):`${importPreview.conflicts.length} Konflikte. Entscheidungen / neue Spaltenwerte ergänzen und Vorschau neu berechnen.`;}finally{if(request.current())el('import-preview').disabled=false;}});
  async function importFolder() { if(importOwner)return;if(jobsBlocked)throw new Error('Bestehenden Ordnerauftrag abwarten oder ungeklärte Annahme prüfen.');if(!importPreview?.can_import)throw new Error('Zuerst eine konfliktfreie Vorschau erstellen.');const body={...importBody(),preview_hash:importPreview.preview_hash},snapshot=JSON.stringify(importBody()),editor=editorGeneration,request=NXRequests.begin('native.import.write');if(!confirm('Geprüften Merge als neuen lokalen Ordner importieren?\nQuelle: '+body.source_path+'\nZiel: '+body.target_path+'\nQuellen bleiben unverändert.'))return;importOwner=request;importControls();message('Importauftrag, Prüfsummenberechnung und Testpaket-Erstellung laufen …');try{await request.wait(NXReleaseJobs.start('import',body,{completed:()=>{let same=false;try{same=snapshot===JSON.stringify(importBody());}catch{}return workspace==='update'&&editor===editorGeneration&&same;}}));}finally{if(importOwner===request)invalidateImport();} }
  el('library-inventory').onclick=run(loadInventory);
  el('create-inventory').onclick=run(loadCreationInventory);el('create-schema').onclick=run(loadCreationSchema);
  el('native-new').onclick=run(()=>{if(!creationReady())throw new Error('Bibliothek und aktuelles Schema für den Entwurf laden.');const schema=creationData.schemas.find(b=>b.id===el('native-block').value);if(!schema)throw new Error('FORMAT-Block wählen.');reset();el('kind').value=creationData.kind;const keyField=schema.fields.includes('LIBRF')?'LIBRF':'NAME';el('cam').value=JSON.stringify({library_id:creationData.id,nx_version:creationData.nx_version,schemas:[schema],records:[{block_id:schema.id,class_name:schema.class_name,values:Object.fromEntries(schema.fields.map(f=>[f,f===keyField?'CUSTOM_'+Date.now():'']))}]},null,2);el('geometry-version').value=creationData.nx_version;renderFields();showWorkspace('prepare','name');message('Neuer Schema-Entwurf. Maße, Pflichtwerte und Referenzen fachlich ergänzen.');});
  el('native-add-row').onclick=run(()=>{
    if(!creationReady()||!requireFields(fieldView))throw new Error('Passende Bibliothek und aktuelle Spaltenmaske für den Entwurf laden.');
    const view=fieldView,cam=view.cam,reason=rowStructureReason(view);if(reason)throw new Error(reason);
    if(cam.library_id!==creationData.id||cam.nx_version!==creationData.nx_version)throw new Error('Schema und Datensatz gehören zu verschiedenen Bibliotheken oder NX-Versionen.');
    const schema=creationData.schemas.find(b=>b.id===el('native-block').value);if(!schema)throw new Error('Vorhandenen FORMAT-Block für die neue Zeile wählen.');
    const current=view.blocks.get(schema.id);if(current&&(current.class_name!==schema.class_name||JSON.stringify(current.fields)!==JSON.stringify(schema.fields)))throw new Error('FORMAT-Definition im Editor weicht vom geladenen Schema ab. Quelle/Schema zuerst ausdrücklich klären.');
    if(!current)cam.schemas.push(structuredClone(schema));const keyField=schema.fields.includes('LIBRF')?'LIBRF':'NAME';cam.records.push({block_id:schema.id,class_name:schema.class_name,values:Object.fromEntries(schema.fields.map(f=>[f,f===keyField?view.key:'']))});
    fieldChanged(view);renderFields({focusIndex:cam.records.length-1});message('Neue Zeile '+cam.records.length+' aus dem gewählten Schema im Editor ergänzt. Alle anderen Zeilen bleiben erhalten; Werte ergänzen und separat speichern.');
  });
  const clearObjects=clearObjectPage;
  el('library-prev').onclick=run(()=>{if(!nativeData)throw new Error('Bibliotheksseite zuerst laden.');return loadNative(Math.max(0,nativeData.offset-100));});el('library-next').onclick=run(()=>{if(!nativeData)throw new Error('Bibliotheksseite zuerst laden.');return loadNative(nativeData.offset+100);});
  el('library-query').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();el('library-read').click();}};
  el('native-library').onchange=clearObjects;el('library-query').oninput=clearObjects;el('library-release').onchange=clearInventory;el('include-standard').onchange=invalidateCatalog;
  el('create-release').onchange=clearCreation;el('create-library').onchange=()=>{NXRequests.invalidate('native.creation.schema');creationSchemaOwner=null;creationData=null;el('native-block').replaceChildren();el('create-schema-state').textContent='Bibliothek geändert. Schema neu laden. Editorwerte bleiben erhalten.';creationControls();};
  el('catalog-preview').onclick=run(async()=>{invalidateCatalog();const release=el('library-release').value,body=catalogBody(),snapshot=JSON.stringify([release,body]);if(release!==inventoryRelease||inventoryContext!==inventoryKey(release))throw new Error('Bibliotheken für den gewählten Datenstand zuerst laden.');const request=NXRequests.begin('native.catalog.preview',()=>snapshot===JSON.stringify([el('library-release').value,catalogBody()]));catalogReadOwner=request;importControls();try{const report=await request.wait(api('releases/'+release+'/catalog-preview','POST',body));checkCatalog(report,release,body);catalogPreview=report;details(catalogPreview);renderCatalog(catalogPreview);importControls();el('catalog-state').textContent=`${catalogPreview.count} neue Objekte, ${catalogPreview.conflicts.length} Konflikte, ${catalogPreview.already_managed.length} bereits verwaltet. ${body.selection?'Nur ausdrücklich ausgewählte Objekte. ':''}Import legt Entwürfe an, keine ERP-Artikel und keine NX-Freigaben.`;}finally{if(catalogReadOwner===request){catalogReadOwner=null;importControls();}}});
  el('catalog-import').onclick=run(async()=>{if(catalogOwner)return;if(jobsBlocked)throw new Error('Laufenden Verwaltungsauftrag abwarten oder unbestätigte Annahme klären.');if(!catalogPreview?.can_import)throw new Error('Zuerst konfliktfreie Stammdatenvorschau laden.');if(!confirm(`${catalogPreview.count} Datensätze als Entwürfe übernehmen?`))return;const release=el('library-release').value,request=NXRequests.begin('native.catalog.write');catalogOwner=request;importControls();try{await request.wait(NXReleaseJobs.start('catalog',{release_id:release,...catalogBody(),preview_hash:catalogPreview.preview_hash}));}finally{if(catalogOwner===request)invalidateCatalog();}});
  el('model-bindings').oninput=()=>{NXNative.assetAdded();NXExports.invalidate();};
  el('catalog-scope').onchange=()=>{invalidateCatalog();renderSelection();};el('catalog-clear-selection').onclick=run(clearSelection);
  el('catalog-select-page').onclick=run(()=>{if(!selectionSupported||objectOwner||!nativeData)throw new Error('Unterstützte Bibliotheksseite zuerst laden.');const library=nativeData.id,keys=nativeData.items.map(item=>item.key).filter(selectableKey),extra=keys.filter(key=>!selectedObjects.get(library)?.has(key));if(selectionCount()+extra.length>500)throw new Error('Diese Seite überschreitet die Grenze von 500 Objekten. Einzelne Objekte auswählen oder Auswahl verkleinern.');if(keys.length){if(!selectedObjects.has(library))selectedObjects.set(library,new Set());for(const key of keys)selectedObjects.get(library).add(key);selectionPins.set(library,nativeData.input_sha256);invalidateCatalog();renderSelection();}});
  el('catalog-objects-prev').onclick=run(()=>{if(!catalogPreview)return;catalogPage--;renderCatalog(catalogPreview);});el('catalog-objects-next').onclick=run(()=>{if(!catalogPreview)return;catalogPage++;renderCatalog(catalogPreview);});
  el('model-package-detach').onclick=run(()=>{if(!confirm('Paket-Provenienz im Editor ausdrücklich lösen? Dateien und Historie bleiben erhalten; PRT-Zielpfade separat prüfen, Geometriechecks erneut bestätigen und Stammdatensatz noch speichern.'))return;model();NXNative.assetAdded();NXExports.invalidate();});
  renderSelection();
  return {resourceFields,renderFields,editorControls,hasModelPackage:()=>Boolean(modelPackage),syncEditorFields:syncFields,loadNative,loadToolExample,importFolder,invalidateImport,syncReleaseSelections,renderConflicts,
    configure:value=>{libraryKinds=new Map((value.native_library_specs||[]).map(spec=>[spec.id,spec.kind]));creationSpecs.clear();for(const spec of value.native_library_specs||[])creationSpecs.set(spec.id,spec);const cap=value.native_schema_read;schemaReadEnabled=cap?.contract_version===1&&cap.inventory_metadata_only===true&&cap.schema_only===true&&cap.manifest_bound===true&&cap.read_only===true&&cap.object_values_included===false&&cap.max_library_bytes===32*1024*1024&&cap.max_report_bytes===4*1024*1024;clearCreation();selectionSupported=value.catalog_selection?.contract_version===1&&value.catalog_selection.whole_objects===true&&value.catalog_selection.max_objects===500;renderSelection();},
    releaseJobControls:value=>{jobsBlocked=value;importControls();},
    clearConnection:()=>{schemaReadEnabled=false;creationSpecs.clear();selectionSupported=false;libraryKinds.clear();el('catalog-scope').value='libraries';clearCreation();clearInventory();clearConflictView();invalidateImport();NXReferences.reset();el('create-release').replaceChildren();el('library-release').replaceChildren();['import-defaults','import-resolutions'].forEach(id=>el(id).value='{}');el('legacy-omissions').value='[]';el('library-query').value='';},
    reset:()=>{NXRequests.invalidate('native.geometry-bind');setGeometry();model();el('model-bindings').value='[]';renderFields();},edit:r=>{NXRequests.invalidate('native.geometry-bind');setGeometry(r.geometry);model(r.model_package);el('model-bindings').value=JSON.stringify(r.file_bindings||[],null,2);renderFields();},assetAdded:()=>{if(fieldView?.valid&&!fieldCurrent(fieldView))staleFields();NXRequests.invalidate('native.geometry-bind');hashes={};el('geometry-status').value='needs_preparation';Object.keys(geometryTests).forEach(key=>el('geometry-'+key).checked=false);globalThis.NXSchemas?.sourceChanged();globalThis.NXFilePackages?.changed();globalThis.NXGraphicsBindings?.sourceChanged();editorControls();}};
})();

globalThis.NXClone=(()=>{
  const el=id=>document.getElementById(id);let enabled=false,generation=0,source=null,preview=null,busy=false,owner=null,kinds=new Map(),valuePage=0,valueRows=[];
  const hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value),empty=value=>NXData.record(value)&&Object.keys(value).length===0;
  const canonical=value=>Array.isArray(value)?['array',value.map(canonical)]:NXData.record(value)?['object',Object.keys(value).sort().map(key=>[key,canonical(value[key])])]:value;
  const same=(left,right)=>JSON.stringify(canonical(left))===JSON.stringify(canonical(right));
  function expectedCam(input){const cam=structuredClone(source.cam);for(const row of cam.records){const block=cam.schemas.find(block=>block.id===row.block_id),field=block.fields.includes('LIBRF')?'LIBRF':'NAME';NXData.set(row.values,field,input.new_key);}return cam;}
  function validResource(value,input,rows){
    const expected=['asset_ids','cam','cam_status','erp','file_bindings','geometry','kind','model_package','name','origin'];
    if(!NXData.record(value)||Object.keys(value).sort().join(',')!==expected.join(',')||value.kind!==kinds.get(input.library_id)||value.name!==input.name||value.cam_status!=='draft'||value.erp!==null||value.origin!==null||value.model_package!==null||!Array.isArray(value.asset_ids)||value.asset_ids.length||!Array.isArray(value.file_bindings)||value.file_bindings.length)return false;
    const geometry=value.geometry,version=releases.find(item=>item.id===source.release)?.nx_version,cam=value.cam;
    if(!NXData.record(geometry)||geometry.mode!=='none'||geometry.status!=='needs_preparation'||geometry.nx_version!==version||geometry.evidence!==''||!empty(geometry.checks)||!empty(geometry.asset_hashes)||!NXData.record(cam)||cam.library_id!==input.library_id||cam.nx_version!==version||!Array.isArray(cam.schemas)||!cam.schemas.length||cam.schemas.length>1000||!Array.isArray(cam.records)||cam.records.length!==rows||rows<1||rows>1000)return false;
    const schemas=new Map();for(const block of cam.schemas){if(!NXData.record(block)||typeof block.id!=='string'||schemas.has(block.id)||typeof block.class_name!=='string'||!hex(block.schema_hash)||!Array.isArray(block.fields)||!block.fields.length||block.fields.some(field=>typeof field!=='string'||!(/^[A-Za-z0-9_]+$/).test(field))||new Set(block.fields).size!==block.fields.length||!block.fields.some(field=>['LIBRF','NAME'].includes(field)))return false;schemas.set(block.id,block);}
    let cells=0;for(const row of cam.records){const block=schemas.get(row?.block_id);if(!NXData.record(row)||!block||row.class_name!==block.class_name||!NXData.record(row.values)||!same(Object.keys(row.values).sort(),[...block.fields].sort())||Object.values(row.values).some(value=>typeof value!=='string'||/[|\r\n\x00]/.test(value))||row.values[block.fields.includes('LIBRF')?'LIBRF':'NAME']!==input.new_key)return false;cells+=block.fields.length;if(cells>100000)return false;}
    return true;
  }
  function checkPreview(value,input,release){
    if(!NXData.record(value)||value.contract_version!==1||value.source_release_id!==release||value.source_library!==input.library_id||value.source_key!==input.source_key||value.source_sha256!==input.source_sha256||!hex(value.source_records_sha256)||!hex(value.source_manifest_sha256)||!hex(value.catalog_sha256)||!hex(value.preview_hash)||value.new_key!==input.new_key||!Number.isSafeInteger(value.rows)||!Array.isArray(value.conflicts)||value.conflicts.some(text=>typeof text!=='string'||!text)||typeof value.can_apply!=='boolean'||value.can_apply!==(value.conflicts.length===0)||value.saved!==false||value.nx_files_written!==false||value.assets_copied!==false||value.erp_copied!==false||value.approval_copied!==false||value.schema_conversion_performed!==false||!validResource(value.resource,input,value.rows)||!same(value.resource.cam,expectedCam(input)))throw new Error('Kein vollständiger gebundener Vorlagenvertrag. Quelle und Kopiervorschau erneut prüfen.');
  }
  function clearPreview(){if(preview){const {snapshot,...report}=preview;if(el('details').textContent===NXData.stringifyWire(report,2))el('details').textContent=JSON.stringify({module:'native_clone',status:'not_verified'},null,2);}preview=null;valuePage=0;valueRows=[];renderValues();}
  function renderValues(){const pageSize=25;valuePage=Math.max(0,Math.min(valuePage,Math.max(0,Math.ceil(valueRows.length/pageSize)-1)));el('clone-values-box').hidden=!valueRows.length;el('clone-values').replaceChildren();
    for(const item of valueRows.slice(valuePage*pageSize,(valuePage+1)*pageSize)){const row=document.createElement('tr');cell(row,'Zeile '+(item.index+1)+' · '+item.block);cell(row,item.field);cell(row,item.before);cell(row,item.after);el('clone-values').append(row);}
    el('clone-values-page').textContent=valueRows.length?(valuePage*pageSize+1)+'–'+Math.min((valuePage+1)*pageSize,valueRows.length)+' von '+valueRows.length+' Feldwerten der vollständigen Gruppe. Nur die Kennungsspalte wird geändert.':'';
    el('clone-values-prev').disabled=busy||valuePage===0;el('clone-values-next').disabled=busy||(valuePage+1)*pageSize>=valueRows.length;
  }
  function inspectValues(value,input){valueRows=[];valuePage=0;const schemas=new Map(value.cam.schemas.map(block=>[block.id,block]));value.cam.records.forEach((row,index)=>{const block=schemas.get(row.block_id),key=block.fields.includes('LIBRF')?'LIBRF':'NAME';for(const field of block.fields)valueRows.push({index,block:block.id,field,before:field===key?input.source_key:row.values[field],after:row.values[field]});});renderValues();}
  function body(){if(!source)throw new Error('Vorlage aus der Bibliotheksansicht wählen.');return {library_id:source.library,source_key:source.key,source_sha256:source.sha256,new_key:el('clone-key').value,name:el('clone-name').value.trim()};}
  function editorSnapshot(){return JSON.stringify([editorGeneration,editing?.id??null,[...document.querySelectorAll('#editor-section input,#editor-section select,#editor-section textarea')].map(node=>[node.id,node.value,node.checked]),el('assetfile').files[0]?[el('assetfile').files[0].name,el('assetfile').files[0].size,el('assetfile').files[0].lastModified]:null]);}
  function controls(){el('clone-preview').disabled=!enabled||!source||busy;el('clone-apply').disabled=!enabled||busy||!preview?.can_apply;el('clone-values-prev').disabled=busy||valuePage===0;el('clone-values-next').disabled=busy||(valuePage+1)*25>=valueRows.length;}
  function invalidate(){NXRequests.invalidate('clone.');clearPreview();busy=false;owner=null;el('clone-state').textContent='Vorlagenwerte geändert. Kopiervorschau erneut prüfen.';controls();}
  function reset(){generation++;NXRequests.invalidate('clone.');source=null;clearPreview();busy=false;owner=null;el('clone-box').hidden=true;el('clone-source').textContent='';el('clone-key').value='';el('clone-name').value='';el('clone-state').textContent='';controls();}
  function open(release,library,key,sha256,cam){if(!enabled)throw new Error('Server unterstützt keine geschützte Vorlagenkopie.');if(!NXData.record(cam)||!Array.isArray(cam.records)||!Array.isArray(cam.schemas))throw new Error('Vollständige manifestgebundene Vorlagengruppe erneut laden.');reset();source={release,library,key,sha256,cam:structuredClone(cam)};el('clone-box').hidden=false;el('clone-source').textContent='Vorlage: '+library+' / '+key+' · Datenstand '+release+'. Editorwerte noch unverändert.';invalidate();showWorkspace('prepare','clone-title');}
  async function inspect(){if(busy||!enabled)return;const input=body(),snapshot=JSON.stringify(input),release=source.release,current=generation;
    const request=NXRequests.begin('clone.preview',()=>current===generation&&release===source?.release&&snapshot===JSON.stringify(body()));busy=true;owner=request;clearPreview();controls();el('clone-state').textContent='Prüfe aktive Vorlagengruppe und freie neue Bibliotheksnummer …';
    try{const result=await request.wait(api('releases/'+release+'/library-clone/preview','POST',input));checkPreview(result,input,release);inspectValues(result.resource,input);preview={...result,snapshot};details(result);el('clone-state').textContent=result.can_apply?result.rows+' vollständige Zeilen als ungespeicherter Entwurf vorbereitet. Maße, Verweise und Geometrie prüfen; noch nichts gespeichert.':result.conflicts.join(' · ');}
    catch(error){if(request.current()&&!NXRequests.isStale(error)){clearPreview();el('clone-state').textContent='Keine vollständige aktuelle Kopiervorschau. Eingaben bleiben erhalten; erneut prüfen.';message(error.message,true);}}
    finally{if(current===generation&&owner===request){busy=false;owner=null;controls();}}
  }
  async function apply(){if(busy||!preview?.can_apply)return;if(saveBusy||NXAssets.isUploading()||NXSaves.blocksSave())throw new Error('Laufenden oder ungeklärten Speichervorgang/Upload zuerst klären.');const input=body(),snapshot=JSON.stringify(input),release=source.release,current=generation,editor=editorSnapshot();
    if(preview.snapshot!==snapshot)throw new Error('Neue Kennung/Bezeichnung geändert. Vorschau neu prüfen.');
    if(!confirm('Geprüfte Gruppe als NEUEN ungespeicherten Entwurf in den Editor übernehmen? Aktuelle Editorwerte werden ersetzt, keine ERP-Daten/Anhänge/Freigaben mitkopiert. Original bleibt unverändert. Danach ausdrücklich speichern.'))return;
    const request=NXRequests.begin('clone.apply',()=>current===generation&&release===source?.release&&snapshot===JSON.stringify(body())&&editor===editorSnapshot());busy=true;owner=request;controls();
    try{const result=await request.wait(api('releases/'+release+'/library-clone/apply','POST',{...input,preview_hash:preview.preview_hash}));if(!NXData.record(result)||result.contract_version!==1||result.preview_hash!==preview.preview_hash||result.saved!==false||result.nx_files_written!==false||result.draft_required!==true||result.geometry_recheck_required!==true||!validResource(result.resource,input,preview.rows)||!same(result.resource,preview.resource))throw new Error('Kein vollständiger unveränderter Vorlagenentwurf ohne geerbte Rechte.');
      const value=result.resource;openEditor();el('kind').value=value.kind;el('name').value=value.name;el('cam').value=JSON.stringify(value.cam,null,2);NXNative.edit(value);NXExports.invalidate();reset();message('Neue Bibliotheksgruppe nur im Editor vorbereitet. Original, Serverdaten und Freigaben unverändert. Maße/Verweise/Geometrie prüfen und als neuen Entwurf speichern.');
    }catch(error){if(request.current()&&!NXRequests.isStale(error)){clearPreview();el('clone-state').textContent='Vorlagenentwurf nicht bestätigt. Editor unverändert; Kopiervorschau erneut prüfen.';message(error.message,true);}}
    finally{if(current===generation&&owner===request){busy=false;owner=null;controls();}}
  }
  el('clone-preview').onclick=()=>action(inspect)();el('clone-apply').onclick=()=>action(apply)();el('clone-close').onclick=reset;
  ['clone-key','clone-name'].forEach(id=>el(id).addEventListener('input',invalidate));
  el('clone-values-prev').onclick=()=>{if(!busy){valuePage--;renderValues();}};el('clone-values-next').onclick=()=>{if(!busy){valuePage++;renderValues();}};
  return {open,reset,supported:()=>enabled,configure:value=>{kinds=new Map((value.native_library_specs||[]).map(spec=>[spec.id,spec.kind]));enabled=value.native_cloning?.contract_version===1&&value.native_cloning.editor_only===true&&value.native_cloning.whole_group===true;controls();},clearConnection:()=>{enabled=false;kinds.clear();reset();}};
})();

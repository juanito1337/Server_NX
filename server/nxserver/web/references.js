'use strict';
globalThis.NXReferences=(()=>{
  const el=id=>document.getElementById(id);
  let issues=[],counter=0;
  function fields(){return JSON.parse(el('reference-resolutions').value);}
  // Library keys are arbitrary data, including __proto__/constructor.
  function branch(parent,key){return NXData.branch(parent,key);}
  function changed(){NXNative.invalidateImport();}
  function show(report,current=()=>true){issues=report.references?.errors||[];el('reference-applied').replaceChildren();
    (report.reference_resolutions||report.libraries?.reference_resolutions||[]).forEach(item=>{const li=document.createElement('li');li.append(document.createTextNode(`${item.library} / ${item.key} · ${item.field}: ${item.previous} → ${item.value} (${item.affected_rows} Zeilen). `));li.append(button('Zuordnung entfernen',()=>{if(!current()||!li.isConnected)return;changed();const data=fields(),values=NXData.get(NXData.get(NXData.get(data,item.library),item.key),item.field);if(NXData.record(values))NXData.remove(values,item.previous);el('reference-resolutions').value=JSON.stringify(data,null,2);}));el('reference-applied').append(li);});
  }
  function control(issue,current=()=>true){const box=document.createElement('div'),label=document.createElement('label'),input=document.createElement('input'),list=document.createElement('datalist');
    if(issue.standard_object){box.textContent='Unverändertes Siemens-Standardobjekt: fehlende Zieldefinition/Bibliothek in der Arbeitskopie ergänzen. Keine Umdeutung des Standards.';return box;}
    list.id='reference-candidates-'+(++counter);input.setAttribute('list',list.id);input.setAttribute('aria-label',`Zielschlüssel für ${issue.library} / ${issue.key} / ${issue.field}`);label.textContent=`${issue.field}: ${issue.value} → vorhandener Schlüssel in ${issue.target}`;label.append(input);box.append(label,list);
    (issue.candidates||[]).forEach(candidate=>{const option=document.createElement('option');option.value=candidate.key;option.label=candidate.label;list.append(option);});
    const matching=issues.filter(item=>!item.standard_object&&item.library===issue.library&&item.field===issue.field&&item.value===issue.value),keys=[...new Set(matching.map(item=>item.key))];
    const all=document.createElement('input');all.type='checkbox';if(keys.length>1){const scope=document.createElement('label');scope.className='checklist';scope.append(all,document.createTextNode(`Für alle ${keys.length} betroffenen Kundenobjekte dieser Bibliothek bestätigen`));box.append(scope);}
    const apply=button('Zuordnung bestätigen',()=>{if(!current()||!box.isConnected||apply.disabled)return;const target=input.value.trim();if(!target||target===issue.value)throw new Error('Einen abweichenden vorhandenen Zielschlüssel eingeben.');const chosen=all.checked?keys:[issue.key];if(!confirm(`${chosen.length} Objekt(e) in ${issue.library}: ${issue.field} von ${issue.value} auf ${target} ändern?\nNur im neuen Zielordner. Fachliche Bedeutung selbst prüfen; danach neue Vorschau erforderlich.`)||!current()||!box.isConnected)return;
      const data=fields();for(const key of chosen){const values=branch(branch(branch(data,issue.library),key),issue.field);Object.defineProperty(values,issue.value,{value:target,enumerable:true,writable:true,configurable:true});}el('reference-resolutions').value=JSON.stringify(data,null,2);changed();apply.disabled=true;message('Zuordnung vorgemerkt. Vorschau erneut prüfen; keine Datei geändert.');});
    apply.disabled=true;input.oninput=()=>{if(current()&&box.isConnected)apply.disabled=!input.value.trim()||input.value.trim()===issue.value;};box.append(apply);const note=document.createElement('small');note.textContent=`${issue.candidate_count??0} vorhandene Zielobjekte; bis zu 20 ähnliche Schlüssel vorgeschlagen. Keine automatische fachliche Zuordnung.`;box.append(note);return box;
  }
  function reset(){issues=[];el('reference-resolutions').value='{}';el('reference-applied').replaceChildren();}
  el('reference-resolutions').addEventListener('input',changed);
  return {fields,show,control,reset};
})();

/* Daily editor relations are separate from preview decisions for a share upgrade. */
globalThis.NXEditorReferences=(()=>{
  const el=id=>document.getElementById(id),status={present:'Vorhanden',missing:'Zielschlüssel fehlt',empty:'Leer · keine automatische Ergänzung',target_missing:'Zielbibliothek fehlt'};
  let supported=false,generation=0,busy=false,owner=null,preview=null,row=null,data=null,page=0,source='',choiceNonce=0;
  function signature(){return JSON.stringify([editorGeneration,editing?.id??null,editing?.revision??null,el('kind').value,el('cam').value]);}
  function body(){return {target_release_id:el('editor-ref-target').value,kind:el('kind').value,cam:JSON.parse(el('cam').value),resource_id:editing?.id??null,expected_revision:editing?.revision??null};}
  function controls(){el('editor-ref-preview').disabled=!supported||busy||!el('editor-ref-target').value;
    el('editor-ref-search').disabled=busy||!row;el('editor-ref-apply').disabled=busy||!preview||!row||!data||!el('editor-ref-key').value||el('editor-ref-key').value===row.value;
    el('editor-ref-options-prev').disabled=busy||!data||data.offset===0;el('editor-ref-options-next').disabled=busy||!data||data.offset+data.items.length>=data.filtered;
  }
  function clearChoice(){choiceNonce++;NXRequests.invalidate('editorrefs.options');NXRequests.invalidate('editorrefs.apply');row=null;data=null;el('editor-ref-choice').hidden=true;el('editor-ref-key').replaceChildren();el('editor-ref-options-state').textContent='';controls();}
  function reset(){generation++;NXRequests.invalidate('editorrefs.');busy=false;owner=null;preview=null;page=0;source=signature();clearChoice();el('editor-ref-rows').replaceChildren();el('editor-ref-page').textContent='';el('editor-ref-prev').disabled=true;el('editor-ref-next').disabled=true;el('editor-ref-state').textContent='Editor/Datenstand geändert. Verweise neu prüfen; noch keine Datei oder Revision geändert.';controls();}
  function sourceChanged(){if(source!==signature())reset();}
  function sync(){const previous=el('editor-ref-target').value;options(el('editor-ref-target'),{'':'NX-Datenstand wählen',...Object.fromEntries(releases.filter(item=>!['building','failed'].includes(item.status)).map(item=>[item.id,item.name+' · NX'+item.nx_version]))});if(previous!==el('editor-ref-target').value)reset();}
  function render(){el('editor-ref-rows').replaceChildren();const rows=preview?.rows||[];page=Math.max(0,Math.min(page,Math.ceil(rows.length/25)-1));
    for(const item of rows.slice(page*25,(page+1)*25)){const tr=document.createElement('tr');cell(tr,'Zeile '+(item.row_index+1)+' · '+item.field);cell(tr,item.value||'—');cell(tr,item.target_library+' · '+(status[item.status]||item.status)+(item.label?' · '+item.label:''));const choice=button('Zielobjekt auswählen',()=>choose(item));choice.disabled=busy||item.status==='target_missing';cell(tr,'').append(choice);el('editor-ref-rows').append(tr);}
    el('editor-ref-page').textContent=(rows.length?page*25+1:0)+'–'+Math.min((page+1)*25,rows.length)+' von '+rows.length+' bekannten Verweisen';el('editor-ref-prev').disabled=busy||page===0;el('editor-ref-next').disabled=busy||(page+1)*25>=rows.length;
  }
  async function inspect(){if(busy||!supported)return;sourceChanged();const snapshot=JSON.stringify(body()),request=NXRequests.begin('editorrefs.preview',()=>snapshot===JSON.stringify(body())),current=generation;
    preview=null;clearChoice();busy=true;owner=request;render();controls();el('editor-ref-state').textContent='Prüfe bekannte Beziehungen und aktive Zielbibliotheken …';
    try{const result=await request.wait(api('editor-references/preview','POST',body()));if(result.contract_version!==1||!Array.isArray(result.rows)||!(/^[a-f0-9]{64}$/).test(result.preview_hash))throw new Error('Kein unterstützter Verweisprüfvertrag.');preview={...result,snapshot};page=0;render();details(result);el('editor-ref-state').textContent=result.rows.length?result.unresolved+' Verweise ohne vorhandenen Zielschlüssel. Leere Felder werden nicht fachlich bewertet. Nur Editoränderungen, keine automatische Schemaumstellung oder Freigabe.':'Dieser Datensatz enthält keine registrierten Bibliotheksbeziehungen. Beschreibungsspalten werden nicht als Verweise erfunden.';}
    catch(error){if(request.current()&&!NXRequests.isStale(error))message(error.message,true);}
    finally{if(current===generation&&owner===request){busy=false;owner=null;render();controls();}}
  }
  async function choose(item){if(busy||!preview)return;clearChoice();row=item;el('editor-ref-choice').hidden=false;el('editor-ref-context').textContent='Zeile '+(row.row_index+1)+' · '+row.field+' → '+row.target_library;el('editor-ref-query').value='';await search(0);}
  function queryChanged(){choiceNonce++;NXRequests.invalidate('editorrefs.options');NXRequests.invalidate('editorrefs.apply');busy=false;owner=null;data=null;el('editor-ref-key').replaceChildren();el('editor-ref-options-state').textContent='Suche geändert. Referenzobjekte neu laden.';controls();}
  async function search(offset=0){if(busy||!row||!preview)return;const own=row,nonce=choiceNonce,current=generation,query=el('editor-ref-query').value,snapshot=JSON.stringify(body());
    const request=NXRequests.begin('editorrefs.options',()=>row===own&&nonce===choiceNonce&&snapshot===JSON.stringify(body())&&query===el('editor-ref-query').value);
    busy=true;owner=request;data=null;el('editor-ref-key').replaceChildren();el('editor-ref-options-state').textContent='Referenzobjekte werden geladen …';controls();
    try{const result=await request.wait(api('releases/'+body().target_release_id+'/reference-options?'+new URLSearchParams({library_id:preview.library_id,field:own.field,query,offset,limit:100})));
      if(result.contract_version!==1||result.scope!=='selected_native_library_bytes'||result.integrity_checked!==true||result.target_library!==own.target_library||result.offset!==offset||result.limit!==100||!Array.isArray(result.items))throw new Error('Kein unterstützter manifestgebundener Referenzbestand.');
      if(result.input_sha256!==preview.target_inputs[own.target_library]||result.manifest_sha256!==preview.target_manifest_sha256){reset();message('Zielbibliothek geändert. Verweise vor neuer Auswahl erneut prüfen.',true);return;}
      data=result;const values=Object.create(null);NXData.set(values,'','Zielobjekt ausdrücklich wählen');for(const item of result.items)NXData.set(values,item.key,item.key+' · '+item.label+' · '+item.rows+' Zeilen');options(el('editor-ref-key'),values);el('editor-ref-key').value='';el('editor-ref-options-state').textContent=(result.items.length?result.offset+1:0)+'–'+(result.offset+result.items.length)+' von '+result.filtered+' Treffern · '+result.total+' aktive Objekte. Kein Objekt automatisch ausgewählt.';
    }catch(error){if(request.current()&&!NXRequests.isStale(error))message(error.message,true);}
    finally{if(current===generation&&owner===request){busy=false;owner=null;controls();}}
  }
  async function apply(){if(busy||!row||!preview||!data)return;const value=el('editor-ref-key').value;if(!value||value===row.value)return;
    if(saveBusy||NXAssets.isUploading())throw new Error('Speichern/Upload läuft noch. Danach Verweis übernehmen.');
    if(!confirm('Zeile '+(row.row_index+1)+' · '+row.field+': '+(row.value||'leer')+' → '+value+'? Nur diese Editorzeile wird geändert; Entwurf und Geometrieprüfung zurücksetzen. Danach ausdrücklich speichern.'))return;
    const input=body(),snapshot=JSON.stringify(input),own=row,nonce=choiceNonce,current=generation;
    if(preview.snapshot!==snapshot)throw new Error('Editor inzwischen geändert. Neue Verweisprüfung erforderlich.');
    const request=NXRequests.begin('editorrefs.apply',()=>nonce===choiceNonce&&row===own&&snapshot===JSON.stringify(body())&&value===el('editor-ref-key').value);busy=true;owner=request;controls();
    try{const result=await request.wait(api('editor-references/apply','POST',{...input,preview_hash:preview.preview_hash,row_index:own.row_index,field:own.field,value}));
      if(result.contract_version!==1||result.saved!==false||result.nx_files_written!==false||result.row_index!==own.row_index||result.field!==own.field||result.value!==value)throw new Error('Kein unterstützter Editoränderungsvertrag.');
      el('cam').value=JSON.stringify(result.cam,null,2);el('camstatus').value='draft';NXNative.assetAdded();NXNative.renderFields();NXExports.invalidate();reset();message('Verweis nur in diese Editorzeile übernommen. Stammdaten speichern erzeugt eine neue Entwurfsrevision; CAM/Geometrie und Export erneut prüfen.');
    }catch(error){if(request.current()&&!NXRequests.isStale(error))message(error.message,true);}
    finally{if(current===generation&&owner===request){busy=false;owner=null;controls();}}
  }
  el('editor-ref-target').onchange=reset;el('editor-ref-preview').onclick=()=>action(inspect)();el('editor-ref-search').onclick=()=>action(()=>search(0))();el('editor-ref-query').oninput=queryChanged;el('editor-ref-query').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();action(()=>search(0))();}};
  el('editor-ref-key').onchange=()=>{NXRequests.invalidate('editorrefs.apply');controls();};el('editor-ref-apply').onclick=()=>action(apply)();
  el('editor-ref-options-prev').onclick=()=>action(()=>search(Math.max(0,data.offset-100)))();el('editor-ref-options-next').onclick=()=>action(()=>search(data.offset+100))();
  el('editor-ref-prev').onclick=()=>{page--;render();};el('editor-ref-next').onclick=()=>{page++;render();};
  el('cam').addEventListener('input',sourceChanged);el('kind').addEventListener('change',sourceChanged);el('native-fields').addEventListener('input',sourceChanged);
  return {reset,sourceChanged,sync,configure:value=>{supported=value.editor_references?.contract_version===1&&value.editor_references.manifest_bound===true&&value.editor_references.editor_only===true;controls();},
    clearConnection:()=>{supported=false;reset();el('editor-ref-target').replaceChildren();el('editor-reference-box').open=false;controls();}};
})();

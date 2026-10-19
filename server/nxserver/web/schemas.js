'use strict';
/* Schema decisions are read-only server previews; apply prepares one editor. */
globalThis.NXSchemas=(()=>{
  const el=id=>document.getElementById(id),pageSize=10,has=(value,key)=>Object.prototype.hasOwnProperty.call(value||{},key);
  const newDecision=()=>({field_map:Object.create(null),discard_fields:[],values:Object.create(null),accept_class_change:false});
  let decisions=Object.create(null),draftValues=Object.create(null),preview=null,report=null,page=0,generation=0,busy=false,owner=null,source='',layoutDirty=false,bulkControls=[];
  function sourceSignature(){return JSON.stringify([editorGeneration,editing?.id??null,editing?.revision??null,el('kind').value,el('cam').value]);}
  function canonical(value){if(Array.isArray(value))return ['array',value.map(canonical)];if(NXData.record(value))return ['object',Object.keys(value).sort().map(key=>[key,canonical(NXData.get(value,key))])];return value;}
  function signature(){return JSON.stringify([connectionGeneration,sourceSignature(),el('schema-target-release').value,decisions]);}
  function body(){return {target_release_id:el('schema-target-release').value,kind:el('kind').value,
    cam:JSON.parse(el('cam').value),resource_id:editing?.id??null,expected_revision:editing?.revision??null,decisions};}
  function blocked(){return saveBusy||NXAssets.isUploading()||Boolean(globalThis.NXSaves?.blocksSave?.());}
  function currentDecision(index){return NXData.get(decisions,String(index));}
  function decision(index){return decisions[String(index)]??=newDecision();}
  function drafts(index){return draftValues[String(index)]??=Object.create(null);}
  function candidates(row,field){return (report?.rows||[]).filter(item=>item.target_block?.id===row.target_block.id&&item.new_fields.some(value=>value.field===field)&&!has(currentDecision(item.index)?.values,field));}
  function controls(){el('schema-preview').disabled=busy||!el('schema-target-release').value;
    el('schema-apply').disabled=busy||blocked()||!preview?.can_apply||preview.snapshot!==signature();
    const rows=report?.rows||[];el('schema-page').textContent=rows.length?(page*pageSize+1)+'–'+Math.min((page+1)*pageSize,rows.length)+' von '+rows.length+' Schemazeilen. Entscheidungen bleiben über alle Seiten erhalten.':'';
    el('schema-prev').disabled=busy||!rows.length||page===0;el('schema-next').disabled=busy||(page+1)*pageSize>=rows.length;
    for(const item of bulkControls){const count=candidates(item.row,item.field).length;
      item.button.disabled=busy||layoutDirty||count===0;
      item.button.textContent='Wert für '+count+' unbestätigte Zeilen mit Ziel-FORMAT '+item.row.target_block.id+' bestätigen';
    }
  }
  function clearVerified(reason){if(report&&el('details').textContent===NXData.stringifyWire(report,2))el('details').textContent=JSON.stringify({module:'schema_adaptation',status:'not_verified',reason},null,2);preview=null;}
  function invalidate(structural=false){NXRequests.invalidate('schemas.');preview=null;busy=false;owner=null;layoutDirty||=structural;
    clearVerified('Schemaauswahl oder Entscheidungen geändert.');
    el('schema-state').textContent='Schemaauswahl oder Entscheidungen geändert. Vorschau erneut prüfen.';controls();}
  function reset(){generation++;NXRequests.invalidate('schemas.');clearVerified('Schema- oder Editor-Kontext gewechselt.');decisions=Object.create(null);draftValues=Object.create(null);preview=report=null;page=0;busy=false;owner=null;layoutDirty=false;bulkControls=[];source=sourceSignature();
    el('schema-rows').replaceChildren();el('schema-state').textContent='Aktuelle CAM-Werte gegen ein Zielschema prüfen. Noch keine Änderung am Editor oder NX-Ordner.';controls();}
  function sourceChanged(){const current=sourceSignature();if(source===current)return;
    if(source){const before=JSON.parse(source),after=JSON.parse(current);
      if([0,1,3,4].every(index=>before[index]===after[index])){source=current;invalidate();el('schema-state').textContent='Stammdatenrevision geändert. Entscheidungen bleiben erhalten; Vorschau erneut prüfen.';return;}
    }
    reset();
  }
  function sync(){sourceChanged();const old=el('schema-target-release').value;options(el('schema-target-release'),
    {'':'Zieldatenstand wählen',...Object.fromEntries(releases.filter(item=>!['failed','building'].includes(item.status)).map(item=>[item.id,item.name+' · NX'+item.nx_version]))});
    if(old!==el('schema-target-release').value)reset();else controls();}
  function bulk(row,field,input){if(busy||layoutDirty)return;const selected=candidates(row,field);if(!selected.length)return;
    if(!confirm(field+': Wert '+JSON.stringify(input.value)+' für '+selected.length+' noch unbestätigte Zeilen mit Ziel-FORMAT '+row.target_block.id+' ausdrücklich bestätigen?\nAuch Zeilen auf anderen Seiten sind betroffen. Unbestätigte Eingaben werden ersetzt; bereits bestätigte Einzelwerte bleiben erhalten. Nur Vorschauentscheidungen, keine Datei oder Revision. Fachliche Bedeutung selbst prüfen.'))return;
    for(const item of selected){NXData.set(decision(item.index).values,field,input.value);NXData.set(drafts(item.index),field,input.value);}
    invalidate();render();message(selected.length+' neue Spaltenwerte ausdrücklich vorgemerkt. Vorschau erneut prüfen; keine Revision, Datei oder Freigabe geändert.');
  }
  function render(){el('schema-rows').replaceChildren();bulkControls=[];const rows=report?.rows||[];
    page=Math.max(0,Math.min(page,Math.ceil(rows.length/pageSize)-1));
    for(const row of rows.slice(page*pageSize,(page+1)*pageSize)){
      const box=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent='Zeile '+(row.index+1)+' · Quelle '+row.source_block.id;box.dataset.schemaRow=String(row.index);box.append(legend);
      const targetLabel=document.createElement('label'),select=document.createElement('select');targetLabel.textContent='Ziel-FORMAT-Block';
      options(select,{'':'Explizit wählen',...Object.fromEntries(report.target_schemas.map(block=>[block.id,block.fields.join(', ')]))});select.value=currentDecision(row.index)?.target_block_id??row.target_block?.id??'';targetLabel.append(select);box.append(targetLabel);
      select.onchange=()=>{decisions[String(row.index)]={...newDecision(),target_block_id:select.value||null};delete draftValues[String(row.index)];invalidate(true);};
      if(row.target_block){
        if(row.source_block.class_name!==row.target_block.class_name){const label=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=currentDecision(row.index)?.accept_class_change===true;label.append(check,document.createTextNode('Klassenwechsel auf '+row.target_block.class_name+' ausdrücklich bestätigen'));box.append(label);check.onchange=()=>{decision(row.index).accept_class_change=check.checked;invalidate();};}
        const table=document.createElement('table'),head=document.createElement('thead'),header=document.createElement('tr'),records=document.createElement('tbody');['Quellspalte','Vorhandener Wert','Ziel / Entscheidung','Status'].forEach(text=>cell(header,text));head.append(header);table.append(head,records);
        row.fields.forEach(field=>{const tr=document.createElement('tr');cell(tr,field.source);cell(tr,field.value);const td=cell(tr,''),choice=document.createElement('select');
          options(choice,{'':'Noch zuordnen',...(!field.identifier?{'!discard':'Ausdrücklich weglassen'}:{}),...Object.fromEntries(row.target_block.fields.map(name=>[name,name]))});
          const item=currentDecision(row.index);choice.value=item?.discard_fields.includes(field.source)?'!discard':NXData.get(item?.field_map,field.source)??field.target??'';choice.disabled=field.identifier;td.append(choice);cell(tr,{kept:'Erhalten',renamed:'Umbenannt',discarded:'Nur in neuer Revision weggelassen',unmapped:'Entscheidung fehlt'}[field.status]);records.append(tr);
          choice.onchange=()=>{const item=decision(row.index);item.discard_fields=item.discard_fields.filter(name=>name!==field.source);NXData.remove(item.field_map,field.source);
            if(choice.value==='!discard')item.discard_fields.push(field.source);else if(choice.value&&choice.value!==field.source)NXData.set(item.field_map,field.source,choice.value);
            if(choice.value&&choice.value!=='!discard')NXData.remove(item.values,choice.value);invalidate(true);};
        });const scroll=document.createElement('div');scroll.className='scroll';scroll.append(table);box.append(scroll);
        row.new_fields.forEach(field=>{const label=document.createElement('label'),input=document.createElement('input'),confirmLabel=document.createElement('label'),check=document.createElement('input');label.textContent='Neue Zielspalte '+field.field;
          const values=currentDecision(row.index)?.values,local=NXData.get(draftValues,String(row.index));input.value=NXData.get(local,field.field)??NXData.get(values,field.field)??field.value??'';input.dataset.schemaField=field.field;label.append(input);box.append(label);
          check.type='checkbox';check.checked=has(values,field.field);check.dataset.schemaConfirm=field.field;confirmLabel.append(check,document.createTextNode('Wert ausdrücklich bestätigen (auch Leerwert)'));box.append(confirmLabel);
          const update=()=>{NXData.set(drafts(row.index),field.field,input.value);const item=decision(row.index);if(check.checked)NXData.set(item.values,field.field,input.value);else NXData.remove(item.values,field.field);invalidate();};input.oninput=update;check.onchange=update;
          const identifier=row.target_block.fields.includes('LIBRF')?'LIBRF':'NAME';
          if(field.field!==identifier&&(report.rows.filter(item=>item.target_block?.id===row.target_block.id&&item.new_fields.some(value=>value.field===field.field)).length>1)){
            const apply=button('',()=>bulk(row,field.field,input));apply.className='secondary';apply.dataset.schemaBulk=field.field;box.append(apply);bulkControls.push({row,field:field.field,button:apply});
          }
        });
      }
      row.conflicts.forEach(reason=>{const note=document.createElement('p');note.textContent=reason;note.className='error';box.append(note);});el('schema-rows').append(box);
    }
    controls();
  }
  async function run(channel,operation){sourceChanged();if(busy)return;const current=generation,snapshot=signature(),request=NXRequests.begin('schemas.'+channel,()=>current===generation&&snapshot===signature()&&(channel!=='apply'||!blocked()));
    busy=true;owner=request;controls();
    try{await operation(request,snapshot);}catch(error){if(request.current()&&!NXRequests.isStale(error)){clearVerified('Schemaantwort nicht vollständig bestätigt.');layoutDirty=true;el('schema-state').textContent='Keine vollständige aktuelle Schema-Vorschau. Eingaben bleiben erhalten; erneut prüfen.';message(error.message,true);}}
    finally{if(current===generation&&owner===request){busy=false;owner=null;controls();}}
  }
  function checkReport(value,input){
    const fail=()=>{throw new Error('Kein unterstützter manifestgebundener Schema-Vorschauvertrag. Werte, Entscheidungen und Zielstand erneut prüfen.');},check=condition=>{if(!condition)fail();};
    const record=NXData.record,hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value),same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
    const native=Boolean(input.cam.library_id),identifier=native?input.cam.library_id:'tool-metric',rows=native?input.cam.records:[{block_id:input.cam.class_name+':1',class_name:input.cam.class_name,values:input.cam.values}];
    const oldBlocks=native?input.cam.schemas:[{id:input.cam.class_name+':1',class_name:input.cam.class_name,fields:Object.keys(input.cam.values)}];
    check(record(value)&&Array.isArray(rows)&&rows.length>0&&rows.length<=1000&&Array.isArray(oldBlocks));
    const sourceBlocks=new Map(oldBlocks.map(block=>[block.id,block])),first=sourceBlocks.get(rows[0].block_id),keyField=first?.fields.includes('LIBRF')?'LIBRF':'NAME';
    check(value.library_id===identifier&&value.key===rows[0].values[keyField]?.trim()&&value.source_representation===(native?'native':'legacy_tool')&&hex(value.source_sha256)&&hex(value.target_library_sha256)&&hex(value.preview_hash));
    check(value.target_release_id===input.target_release_id&&value.target_nx_version===releases.find(item=>item.id===input.target_release_id)?.nx_version&&value.resource_id===input.resource_id&&value.expected_revision===input.expected_revision&&value.automatic_save===false&&value.automatic_approval===false&&value.nx_files_written===false);
    check(Array.isArray(value.rows)&&value.rows.length===rows.length&&Array.isArray(value.target_schemas)&&value.target_schemas.length>0&&value.target_schemas.length<=1000&&Array.isArray(value.conflicts)&&typeof value.can_apply==='boolean'&&value.can_apply===(value.conflicts.length===0));
    const normalized=Object.create(null);for(const [index,item] of Object.entries(input.decisions))NXData.set(normalized,index,{target_block_id:item.target_block_id??null,field_map:item.field_map||{},discard_fields:item.discard_fields||[],values:item.values||{},accept_class_change:item.accept_class_change??false});
    check(same(value.decisions,normalized));const targets=new Map();let cells=0;
    for(const block of value.target_schemas){check(record(block)&&typeof block.id==='string'&&!targets.has(block.id)&&typeof block.class_name==='string'&&hex(block.schema_hash)&&Array.isArray(block.fields)&&block.fields.length>0&&new Set(block.fields).size===block.fields.length&&block.fields.every(field=>typeof field==='string'&&/^[A-Za-z0-9_]+$/.test(field))&&block.fields.some(field=>['LIBRF','NAME'].includes(field)));targets.set(block.id,block);}
    const output=[],counts={kept:0,renamed:0,discarded:0,added:0,class_changes:0},conflicts=[];
    for(let index=0;index<rows.length;index++){
      const row=value.rows[index],original=rows[index],block=sourceBlocks.get(original.block_id),decision=input.decisions[String(index)]||{},fields=decision.field_map||{},discard=decision.discard_fields||[],provided=decision.values||{};
      check(record(row)&&row.index===index&&block&&record(row.source_block)&&row.source_block.id===block.id&&row.source_block.class_name===block.class_name&&same(row.source_block.fields,block.fields)&&same(row.source_values,original.values)&&Array.isArray(row.fields)&&Array.isArray(row.new_fields)&&Array.isArray(row.conflicts)&&row.conflicts.every(text=>typeof text==='string'&&text.length>0)&&Array.isArray(row.candidate_ids)&&row.candidate_ids.every(id=>targets.has(id)));
      for(const reason of row.conflicts)conflicts.push({index,reason});
      check(same(row.candidate_ids,value.target_schemas.filter(target=>target.class_name===block.class_name).map(target=>target.id)));
      if(row.target_block===null){check(same(row.conflicts,['Target FORMAT block is missing/ambiguous; choose explicitly.'])&&row.fields.length===0&&row.new_fields.length===0);continue;}
      const target=targets.get(row.target_block?.id);check(target&&same(row.target_block,target)&&(!decision.target_block_id||decision.target_block_id===target.id));
      if(!decision.target_block_id)check(target.class_name===block.class_name);if(target.class_name!==block.class_name){counts.class_changes++;if(value.can_apply)check(decision.accept_class_change===true);}
      check(row.fields.length===block.fields.length);const planned=Object.create(null),oldKey=block.fields.includes('LIBRF')?'LIBRF':'NAME',newKey=target.fields.includes('LIBRF')?'LIBRF':'NAME',reasons=[];
      if(newKey!==oldKey)reasons.push('Identifier-column changes require a dedicated library adapter.');
      if(target.class_name!==block.class_name&&decision.accept_class_change!==true)reasons.push('Changed NX class must be explicitly confirmed.');
      for(let position=0;position<block.fields.length;position++){
        const name=block.fields[position],destination=has(fields,name)?NXData.get(fields,name):name,item=row.fields[position],status=discard.includes(name)?'discarded':!target.fields.includes(destination)?'unmapped':destination!==name?'renamed':'kept';
        check(record(item)&&item.source===name&&item.value===original.values[name]&&item.identifier===(name===oldKey)&&item.status===status&&item.target===(['kept','renamed'].includes(status)?destination:null));
        if(status==='unmapped')reasons.push('Explicit mapping/discard decision required for '+name+'.');
        else if(status==='discarded')counts.discarded++;else{if(has(planned,destination))reasons.push('Multiple source columns map to '+destination+'.');NXData.set(planned,destination,original.values[name]);counts[status]++;}
      }
      const added=target.fields.filter(name=>!has(planned,name));check(row.new_fields.length===added.length);
      for(let position=0;position<added.length;position++){const name=added[position],item=row.new_fields[position],confirmed=has(provided,name),text=confirmed?NXData.get(provided,name):null;
        check(record(item)&&item.field===name&&item.confirmed===confirmed&&item.value===text);if(confirmed){check(typeof text==='string'&&text===text.trim()&&!/[|\r\n\x00]/.test(text));NXData.set(planned,name,text);counts.added++;}else reasons.push('Explicit value required for new column '+name+'.');}
      if(has(planned,newKey)&&NXData.get(planned,newKey)!==original.values[oldKey])reasons.push('Library key must stay unchanged.');check(same(row.conflicts,reasons));
      check(same(row.target_values,planned));cells+=target.fields.length;check(cells<=100000);output.push({block_id:target.id,class_name:target.class_name,values:planned});
    }
    check(same(value.conflicts,conflicts)&&same(value.changes,counts));
    if(value.can_apply){const used=new Set(output.map(row=>row.block_id)),cam=native?{library_id:identifier,nx_version:value.target_nx_version,schemas:value.target_schemas.filter(block=>used.has(block.id)),records:output}:{class_name:output[0].class_name,values:output[0].values};check(output.length===rows.length&&same(value.cam,cam));}
    else check(value.cam===null);
  }
  el('schema-preview').onclick=()=>run('preview',async(request,snapshot)=>{const input=body();if(!input.target_release_id)throw new Error('Zieldatenstand für das Schema wählen.');
    clearVerified('Schema-Vorschau wird erneut geprüft.');el('schema-state').textContent='Prüfe Zielschema, Bibliotheksnummer und Spaltenentscheidungen …';
    const value=await request.wait(api('schema/preview','POST',input));checkReport(value,input);report=value;layoutDirty=false;preview={...value,snapshot};render();details(value);
    el('schema-state').textContent=value.can_apply?'Schema-Vorschau vollständig: '+value.changes.kept+' Werte erhalten, '+value.changes.renamed+' umbenannt, '+value.changes.discarded+' ausdrücklich weggelassen, '+value.changes.added+' ergänzt. Nur in Editor übernehmen; danach als Entwurf speichern und erneut prüfen.':value.conflicts.length+' offene Entscheidung(en) in '+value.rows.length+' Zeilen. Zuordnungen/Werte bestätigen und Vorschau erneut prüfen.';
  });
  el('schema-apply').onclick=()=>run('apply',async(request,snapshot)=>{
    const input=body(),bound=preview;if(!bound?.can_apply||bound.snapshot!==snapshot)throw new Error('Zuerst eine passende vollständige Schema-Vorschau prüfen.');
    if(blocked())throw new Error('Laufenden oder ungeklärten Speichervorgang/Upload zuerst klären.');
    if(!confirm('Geprüfte Schemaänderung nur in den aktuellen Editor übernehmen? CAM-Status wird Entwurf, Geometrieprüfung wird zurückgesetzt. Erst Speichern legt eine neue Revision an.'))return;
    const result=await request.wait(api('schema/apply','POST',{...input,preview_hash:bound.preview_hash}));
    if(!NXData.record(result)||result.saved!==false||result.nx_files_written!==false||result.draft_required!==true||result.geometry_recheck_required!==true||result.preview_hash!==bound.preview_hash||result.nx_version!==bound.target_nx_version||!NXData.record(result.cam)||JSON.stringify(canonical(result.cam))!==JSON.stringify(canonical(bound.cam)))throw new Error('Kein unterstützter schreibfreier Schemaänderungsvertrag.');
    el('cam').value=JSON.stringify(result.cam,null,2);el('camstatus').value='draft';el('geometry-version').value=result.nx_version;NXNative.assetAdded();NXExports.invalidate();NXNative.renderFields();reset();
    message('Schema in Editor übernommen. Stammdaten speichern erzeugt eine neue Entwurfsrevision. NX-Ordner und alte Revisionen bleiben unverändert.');
  });
  el('schema-prev').onclick=()=>{if(!busy){page--;render();}};el('schema-next').onclick=()=>{if(!busy){page++;render();}};
  el('schema-target-release').onchange=reset;el('kind').addEventListener('change',sourceChanged);el('cam').addEventListener('input',sourceChanged);
  return {reset,sourceChanged,sync,controls,clearConnection:()=>{reset();el('schema-target-release').replaceChildren();el('schema-adaptation').open=false;controls();}};
})();

/* Read-only catalog worklist; opening one row never applies or saves a schema. */
globalThis.NXSchemaImpact=(()=>{
  const el=id=>document.getElementById('schema-impact-'+id),record=value=>NXData.record(value),hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const nat=value=>Number.isSafeInteger(value)&&value>=0,uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
  const labels={current:'Bereits passend',ready:'Ohne neue Werte projizierbar',decisions_required:'Entscheidungen erforderlich',blocked:'Blockiert',read_only:'Siemens-Standard schreibgeschützt',not_applicable:'Andere Bearbeitung / stillgelegt'};
  let enabled=false,specs=[],kindNames={},data=null,owner=null,opening=null,observed=null,generation=0,shown=null,editorVersion=0;
  const editorSnapshot=()=>JSON.stringify([editorGeneration,editorVersion,workspace,[...document.querySelectorAll('#editor-section input,#editor-section select,#editor-section textarea')].map(node=>[node.id,node.value,node.checked]),document.getElementById('assetfile').files[0]?[document.getElementById('assetfile').files[0].name,document.getElementById('assetfile').files[0].size,document.getElementById('assetfile').files[0].lastModified]:null]);
  const target=value=>Object.fromEntries(['id','nx_version','status','folder','parent_id','manifest','catalog'].map(key=>[key,value[key]]));
  const ordered=values=>[...values].sort((a,b)=>a.id<b.id?-1:a.id>b.id?1:0),hash=value=>NXUpgradePlan.hash(value);
  const body=(offset=0,paging=false)=>({library_id:el('library').value,kind:el('kind').value,cam_status:el('status').value,state:el('filter').value,query:el('query').value,offset,limit:50,context_sha256:paging?data?.context_sha256??null:null});
  const selection=()=>JSON.stringify([el('release').value,body()]);
  const signature=()=>JSON.stringify([ordered(resources),releases.map(value=>[value.id,value.nx_version,value.status,value.folder,value.parent_id,value.catalog])]);
  function controls(){const busy=Boolean(owner);el('read').disabled=!enabled||busy||!el('release').value;el('prev').disabled=!enabled||busy||!data||data.offset===0;el('next').disabled=!enabled||busy||!data||data.offset+data.items.length>=data.filtered;el('items').querySelectorAll('button').forEach(button=>button.disabled=busy||!enabled);}
  function erase(){if(shown&&document.getElementById('details').textContent===NXData.stringifyWire(shown,2))document.getElementById('details').textContent=JSON.stringify({module:'catalog_schema_impact',status:'not_verified'},null,2);shown=null;data=null;el('items').replaceChildren();el('counts').textContent='';el('page').textContent='';}
  function invalidate(reason='Auswahl geändert; Schema-Arbeitsliste erneut prüfen.'){generation++;NXRequests.invalidate('schema-impact.');owner=opening=null;erase();el('state').textContent=reason;controls();}
  function abandonOpen(){NXRequests.invalidate('schema-impact.open');if(opening&&owner===opening){owner=null;controls();}opening=null;}
  const writingBlocked=()=>saveBusy||NXAssets.isUploading()||NXSaves.blocksSave();
  const currentRow=(row,view,item)=>enabled&&workspace==='manage'&&el('box').open&&row.isConnected&&row.parentElement===el('items')&&data===view&&view.items.includes(item);
  function sync(){const current=signature();if(observed!==null&&observed!==current)invalidate('Bestand geändert; Schema-Arbeitsliste erneut prüfen.');observed=current;
    const previous=el('release').value;options(el('release'),{'':'Zieldatenstand wählen',...Object.fromEntries(releases.filter(value=>!['failed','building'].includes(value.status)).map(value=>[value.id,value.name+' · NX'+value.nx_version]))});if(previous!==el('release').value)invalidate();controls();}
  function clearConnection(){enabled=false;observed=null;specs=[];kindNames={};invalidate('');['release','library','kind'].forEach(id=>el(id).replaceChildren());el('query').value='';el('status').value='';el('filter').value='';el('box').open=false;controls();}
  function configure(cap){clearConnection();const contract=cap.catalog_schema_impact;enabled=contract?.contract_version===1&&contract.read_only===true&&contract.editor_entry_only===true&&contract.automatic_save===false&&contract.automatic_approval===false&&contract.target_library_bounded_reader===true;
    specs=cap.native_library_specs||[];kindNames=cap.kind_labels||{};options(el('library'),{'':'Alle Bibliotheken und Dateipakete',...Object.fromEntries(specs.map(value=>[value.id,value.label+' · '+value.id]))});options(el('kind'),{'':'Alle Typen',...kindNames});el('state').textContent=enabled?'Zieldatenstand wählen und Arbeitsliste ausdrücklich prüfen.':'Server unterstützt die Schema-Arbeitsliste noch nicht.';controls();}
  function verify(value,input,release,source){const fail=()=>{throw new Error('Keine vollständige gebundene Schema-Arbeitsliste. Bestand aktualisieren und erneut prüfen.');};
    const filters=Object.fromEntries(['library_id','kind','cam_status','state','query'].map(key=>[key,input[key]])),context={target:target(release),resources:ordered(source)};
    if(!record(value)||value.contract_version!==1||value.scope!=='catalog_schema_impact'||value.release_id!==release.id||value.target_nx_version!==release.nx_version||value.read_only!==true||value.automatic_save!==false||value.automatic_approval!==false||value.nx_files_written!==false||value.geometry_validation!==false||value.reference_validation!==false||value.file_package_validation!==false||hash(value.filters)!==hash(filters)||value.context_sha256!==hash(context)||value.catalog_sha256!==hash(context.resources)||value.target_sha256!==hash(context.target)||input.context_sha256!==null&&input.context_sha256!==value.context_sha256)fail();
    const unsigned={...value};delete unsigned.report_sha256;if(!hex(value.report_sha256)||hash(unsigned)!==value.report_sha256)fail();
    if(!record(value.limits)||value.limits.resources!==10000||value.limits.source_cells!==1000000||value.limits.target_bytes!==128*1024*1024||value.limits.metadata_bytes!==32*1024*1024||!record(value.counts)||Object.keys(value.counts).sort().join(',')!==Object.keys(labels).sort().join(',')||Object.values(value.counts).some(count=>!nat(count))||!nat(value.total_resources)||value.total_resources!==source.length||!nat(value.scoped_resources)||Object.values(value.counts).reduce((sum,count)=>sum+count,0)!==value.scoped_resources||!nat(value.filtered)||value.filtered>value.scoped_resources||value.scoped_resources>value.total_resources||value.offset!==input.offset||value.limit!==input.limit||!Array.isArray(value.items)||value.items.length!==Math.min(input.limit,Math.max(0,value.filtered-input.offset)))fail();
    const sourceMap=new Map(source.map(item=>[item.id,item])),known=new Map(specs.map(item=>[item.id,item])),seen=new Set();let previous='';
    for(const item of value.items){const resource=sourceMap.get(item?.resource_id),cam=resource?.cam||{},identifier=cam.library_id||(resource?.kind==='tool'&&cam.values?'tool-metric':null);
      if(!record(item)||!resource||!uuid(item.resource_id)||seen.has(item.resource_id)||previous&&previous>=item.resource_id||item.revision!==resource.revision||item.name!==resource.name||item.kind!==resource.kind||item.cam_status!==resource.cam_status||item.resource_sha256!==hash(resource)||item.cam_sha256!==hash(cam)||item.library_id!==identifier||identifier!==null&&!known.has(identifier)||item.source_nx_version!==(cam.nx_version??null)||!Object.hasOwn(labels,item.state)||typeof item.reason!=='string'||!item.reason||item.reason.length>4096||!['rows','new_values','unmapped_fields','ambiguous_rows','class_changes','conflict_count'].every(key=>nat(item[key]))||!Array.isArray(item.conflicts)||item.conflicts.length!==Math.min(item.conflict_count,20)||item.conflicts_truncated!==(item.conflict_count>20)||item.conflicts.some(conflict=>!record(conflict)||!nat(conflict.index)||conflict.index>=item.rows||typeof conflict.reason!=='string'||!conflict.reason)||item.can_open_schema!==['current','ready','decisions_required'].includes(item.state)||input.kind&&item.kind!==input.kind||input.cam_status&&item.cam_status!==input.cam_status||input.library_id&&item.library_id!==input.library_id||input.state&&item.state!==input.state)fail();
      if(item.key!==null&&(typeof item.key!=='string'||!item.key||item.key.length>1024)||!['native','legacy_tool',null].includes(item.source_representation)||item.target_library_sha256!==null&&!hex(item.target_library_sha256)||item.projected_cam_sha256!==null&&!hex(item.projected_cam_sha256))fail();
      if(item.source_representation!==null){const native=Boolean(cam.library_id),rows=native?cam.records:[{values:cam.values}],key=rows?.[0]?.values?.LIBRF??rows?.[0]?.values?.NAME;if(!Array.isArray(rows)||item.source_representation!==(native?'native':'legacy_tool')||item.rows!==rows.length||item.key!==key)fail();}
      if(item.can_open_schema){const path=known.get(identifier)?.path,rows=release.manifest.filter(file=>file.path.toLowerCase()===path?.toLowerCase());if(!item.rows||item.key===null||rows.length!==1||rows[0].sha256!==item.target_library_sha256||item.source_representation===null)fail();}
      if(['current','ready'].includes(item.state)){if(item.conflict_count||item.new_values||item.unmapped_fields||item.ambiguous_rows||item.class_changes||!hex(item.projected_cam_sha256)||(item.state==='current')!==(item.cam_sha256===item.projected_cam_sha256))fail();}else if(item.projected_cam_sha256!==null)fail();
      if(['read_only','not_applicable'].includes(item.state)&&(item.rows||item.new_values||item.unmapped_fields||item.ambiguous_rows||item.class_changes||item.conflict_count||item.source_representation!==null||item.key!==null||item.target_library_sha256!==null))fail();
      if(item.state==='decisions_required'&&!item.conflict_count)fail();seen.add(item.resource_id);previous=item.resource_id;
    }
    if(!input.query&&!input.state&&value.filtered!==value.scoped_resources)fail();
    const scoped=source.filter(resource=>(!input.kind||resource.kind===input.kind)&&(!input.cam_status||resource.cam_status===input.cam_status)&&(!input.library_id||(resource.cam?.library_id||(resource.kind==='tool'&&resource.cam?.values?'tool-metric':null))===input.library_id));
    if(value.scoped_resources!==scoped.length)fail();
    for(const [state,count] of Object.entries(value.counts)){const pageCount=value.items.filter(item=>item.state===state).length;if(pageCount>count||value.items.length===value.scoped_resources&&count!==pageCount)fail();}
    return true;
  }
  function render(){const view=data;el('items').replaceChildren();for(const item of view.items){const row=document.createElement('tr');row.dataset.resourceId=item.resource_id;cell(row,item.name+' · Revision '+item.revision+' · '+(kindNames[item.kind]||item.kind));cell(row,(item.library_id||'—')+' / '+(item.key??'—'));cell(row,labels[item.state]+' · '+item.reason);
      const notes=cell(row,item.rows+' Zeilen · '+item.new_values+' neue Werte · '+item.unmapped_fields+' unzugeordnete Spalten · '+item.ambiguous_rows+' unklare Zielzeilen');if(item.conflicts.length){const list=document.createElement('ul');for(const conflict of item.conflicts){const li=document.createElement('li');li.textContent='Zeile '+(conflict.index+1)+': '+conflict.reason;list.append(li);}notes.append(list);if(item.conflicts_truncated)notes.append(document.createTextNode('Nur 20 von '+item.conflict_count+' Konflikten hier angezeigt; alle im Schemaeditor prüfen.'));}
      const action=cell(row,'');if(item.can_open_schema)action.append(button('Im Schemaeditor prüfen',()=>open(item,view,row)));else action.textContent='Kein automatischer Schemaeingriff';el('items').append(row);}
    el('counts').textContent=Object.entries(labels).map(([key,label])=>label+': '+data.counts[key]).join(' · ')+' · '+data.scoped_resources+' im gewählten Umfang von '+data.total_resources+' Stammdatensätzen.';
    el('page').textContent=(data.items.length?data.offset+1:0)+'–'+(data.offset+data.items.length)+' von '+data.filtered+' Treffern. Suche und Befundfilter gelten für den ganzen Umfang.';el('state').textContent='Schema-Arbeitsliste geprüft. Keine Revision oder NX-Datei geändert; keine fachliche Freigabe.';controls();}
  async function read(offset=0,paging=false){if(!enabled||owner||!el('release').value)return;const input=body(offset,paging),id=el('release').value,selected=selection(),current=generation,source=resources,own=NXRequests.begin('schema-impact.read',()=>current===generation&&selection()===selected),view=NXRequests.begin('details.read');owner=own;erase();controls();el('state').textContent='Prüfe verwaltete CAM-Schemata gegen den gewählten Datenstand …';
    try{const release=await own.wait(api('releases/'+id)),value=await own.wait(api('releases/'+id+'/catalog-schema-impact','POST',input));verify(value,input,release,source);data=value;render();if(view.current()&&workspace==='manage'&&el('box').open){shown=value;details(value);}}
    catch(error){if(own.current()&&!NXRequests.isStale(error)){erase();el('state').textContent='Schema-Arbeitsliste nicht bestätigt: '+error.message;}}
    finally{if(owner===own){owner=null;controls();}}}
  async function open(item,view,row){if(owner||!currentRow(row,view,item)||!item.can_open_schema)return;if(writingBlocked())throw new Error('Laufenden oder unbestätigten Speichervorgang/Upload zuerst klären.');
    if(!confirm('Diesen gespeicherten Datensatz im Schemaeditor öffnen? Aktuelle ungespeicherte Editorwerte werden ersetzt. Die Arbeitsliste speichert keine Änderung und übernimmt keine Werte automatisch.'))return;
    const report=view,id=el('release').value,current=generation,selected=selection(),editor=editorSnapshot(),own=NXRequests.begin('schema-impact.open',()=>current===generation&&selection()===selected&&currentRow(row,view,item)&&editorSnapshot()===editor&&!writingBlocked());owner=opening=own;controls();
    try{const resource=await own.wait(api('resources/'+item.resource_id)),release=await own.wait(api('releases/'+id));
      if(resource.id!==item.resource_id||resource.revision!==item.revision||hash(resource)!==item.resource_sha256||hash(target(release))!==report.target_sha256||!resources.some(value=>value.id===resource.id&&hash(value)===item.resource_sha256))throw new Error('Datensatz oder Zielschema geändert; Bestand und Arbeitsliste erneut prüfen.');
      const select=document.getElementById('schema-target-release');if(![...select.options].some(option=>option.value===id))throw new Error('Zieldatenstand zuerst im aktuellen Bestand laden.');openEditor(resource);select.value=id;select.dispatchEvent(new Event('change'));document.getElementById('schema-adaptation').open=true;document.getElementById('schema-preview').focus();message('Datensatz und Zielschema nur im Editor vorbereitet. Einzelvorschau prüfen, Entscheidungen bestätigen und erst danach bewusst als Entwurf speichern.');
    }catch(error){if(own.current()&&!NXRequests.isStale(error)){erase();el('state').textContent='Schemaeditor nicht vorbereitet: '+error.message;message(error.message,true);}}
    finally{if(opening===own)opening=null;if(owner===own){owner=null;controls();}}}
  ['release','library','kind','status','filter'].forEach(id=>el(id).onchange=()=>invalidate());el('query').oninput=()=>invalidate();el('query').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();read();}};
  el('box').querySelector('summary').addEventListener('click',()=>{if(el('box').open)abandonOpen();});
  ['input','change'].forEach(event=>document.getElementById('editor-section').addEventListener(event,()=>{editorVersion++;}));
  el('read').onclick=()=>read();el('prev').onclick=()=>read(Math.max(0,data.offset-50),true);el('next').onclick=()=>read(data.offset+50,true);
  return {configure,sync,clearConnection,workspaceChanged:abandonOpen};
})();

'use strict';
/* Metadata comparison only: no revision restore, qualification or file reads. */
globalThis.NXHistory=(()=>{
  const el=id=>document.getElementById(id),labels={identity:'Bezeichnung / Typ',cam:'Technische CAM-Daten',erp:'ERP-Felder',approval:'Exportstatus',geometry:'Geometrie / Prüfbelege',attachments:'Anhänge / Grafikpfade',model:'Modellpaket',package:'NX-Dateipaket',origin:'Herkunft / Besitz',other:'Weitere Felder'};
  let selected=null,head=null,index=null,result=null,busy=false,owner=null,generation=0,stale=false;const revisions=new Map();
  const hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const fields={kind:'identity',name:'identity',cam:'cam',erp:'erp',cam_status:'approval',geometry:'geometry',asset_ids:'attachments',file_bindings:'attachments',model_package:'model',file_package:'package',origin:'origin'};
  const natural=value=>Number.isSafeInteger(value)&&value>=0;
  function controls(){el('history-reload').disabled=busy||!selected;el('history-index-prev').disabled=busy||stale||!index||index.offset===0;
    el('history-index-next').disabled=busy||stale||!index||index.offset+index.items.length>=index.total;
    el('history-compare').disabled=busy||stale||!head||!el('history-before').value||!el('history-after').value;
    el('history-diff-prev').disabled=busy||stale||!result||result.offset===0;el('history-diff-next').disabled=busy||stale||!result||result.offset+result.items.length>=result.filtered;
  }
  function clearDiff(){NXRequests.invalidate('history.diff');result=null;if(owner?.historyAction==='diff'){busy=false;owner=null;}el('history-diff').replaceChildren();el('history-diff-page').textContent='Auswahl oder Filter geändert. Vergleich neu laden.';controls();}
  function reset(){generation++;NXRequests.invalidate('history.');selected=head=index=result=null;busy=stale=false;owner=null;revisions.clear();el('history-box').hidden=true;
    ['history-name','history-state','history-index-page','history-diff-page'].forEach(id=>el(id).textContent='');['history-index','history-diff','history-before','history-after'].forEach(id=>el(id).replaceChildren());el('history-query').value='';el('history-area').value='';controls();}
  function selectors(){const before=el('history-before').value,after=el('history-after').value,values=Object.create(null);NXData.set(values,'','Revision ausdrücklich wählen');
    for(const value of [...revisions.values()].sort((a,b)=>b.revision-a.revision))NXData.set(values,String(value.revision),'Rev '+value.revision+' · '+value.actor+' · '+value.created_at);
    options(el('history-before'),values);options(el('history-after'),values);if(before&&revisions.has(Number(before)))el('history-before').value=before;if(after&&revisions.has(Number(after)))el('history-after').value=after;
  }
  function validCommon(value){return value?.contract_version===1&&value.resource_id===selected?.id&&value.sqlite_read_transaction===true&&value.sqlite_read_only===true&&value.audit_verified===false&&value.files_verified===false&&value.automatic_restore===false&&value.nx_files_written===false;}
  async function run(name,guard,fn){if(busy)return;const own=NXRequests.begin('history.'+name,guard),current=generation;own.historyAction=name;owner=own;busy=true;controls();
    try{await fn(own);}catch(error){if(own.current()&&!NXRequests.isStale(error)){el('history-state').textContent='Historie/vergleich konnte nicht vollständig geladen werden. '+error.message;}}
    finally{if(current===generation&&owner===own){owner=null;busy=false;controls();}}}
  async function load(offset=0,fresh=false){if(!selected||busy)return;const id=selected.id,expected=fresh?null:head?.revision;
    return run('index',()=>selected?.id===id,async request=>{const query=new URLSearchParams({offset,limit:20});if(expected!==null&&expected!==undefined)query.set('expected_revision',expected);
      const value=await request.wait(api('resources/'+id+'/history-summary?'+query));
      if(!validCommon(value)||!natural(value.current_revision)||value.current_revision<1||value.total!==value.current_revision||value.offset!==offset||value.limit!==20||!hex(value.current_payload_sha256)||!Array.isArray(value.items)||value.items.length!==Math.min(20,Math.max(0,value.total-offset))||value.items.some((item,index)=>item.revision!==value.total-offset-index||!hex(item.payload_sha256)||!natural(item.payload_bytes)||typeof item.actor!=='string'||typeof item.created_at!=='string'))throw new Error('Kein unterstützter Revisionsbestand.');
      if(!fresh&&head&&(head.revision!==value.current_revision||head.sha!==value.current_payload_sha256))throw new Error('Historienkopf geändert. Historie ausdrücklich neu laden.');
      if(fresh){revisions.clear();result=null;el('history-diff').replaceChildren();el('history-diff-page').textContent='Revisionen wählen und Vergleich starten; keine Datenänderung.';}
      for(const item of value.items){const cached=revisions.get(item.revision);if(cached&&cached.payload_sha256!==item.payload_sha256)throw new Error('Historische Revisionsbytes geändert. Historie ausdrücklich neu laden.');}
      for(const item of value.items)revisions.set(item.revision,item);
      head={revision:value.current_revision,sha:value.current_payload_sha256};index=value;stale=false;selectors();
      if(fresh){el('history-after').value=String(head.revision);el('history-before').value=String(Math.max(1,head.revision-1));}
      el('history-index').replaceChildren();for(const item of value.items){const row=document.createElement('tr');cell(row,'Rev '+item.revision);cell(row,item.actor);cell(row,item.created_at);cell(row,item.payload_bytes+' Bytes');
        const actions=cell(row,'');actions.append(button('Als Vorher wählen',()=>{el('history-before').value=String(item.revision);clearDiff();}),button('Als Nachher wählen',()=>{el('history-after').value=String(item.revision);clearDiff();}));el('history-index').append(row);}
      el('history-index-page').textContent=(value.items.length?offset+1:0)+'–'+(offset+value.items.length)+' von '+value.total+' Revisionen. Weitere Seiten behalten bereits geladene Revisionsauswahlen.';
      el('history-state').textContent='Aktueller Historienkopf Rev '+head.revision+'. Byte-SHA bindet ausgewählte Metadaten; keine Audit-/Dateiprüfung oder NX-Freigabe.';
    });}
  function input(){const before=Number(el('history-before').value),after=Number(el('history-after').value);return {before,after,before_sha256:revisions.get(before)?.payload_sha256,after_sha256:revisions.get(after)?.payload_sha256,expected_revision:head?.revision,area:el('history-area').value,query:el('history-query').value};}
  function location(tokens){return tokens.map((part,index)=>typeof part==='number'?'Position '+(part+1):index===0?(labels[NXData.get(fields,part)]||part):part).join(' → ')||'(gesamter Datensatz)';}
  function validPreview(value){return NXData.record(value)&&typeof value.present==='boolean'&&typeof value.text==='string'&&[...value.text].length<=1200&&typeof value.truncated==='boolean'&&(value.present?['object','array','string','boolean','number','null'].includes(value.type)&&hex(value.sha256):value.type==='missing'&&value.text===''&&value.truncated===false&&value.sha256===null);}
  function validItem(item){if(!NXData.record(item)||!Object.hasOwn(labels,item.area)||!Array.isArray(item.tokens)||item.tokens.some(token=>typeof token!=='string'&&!natural(token))||typeof item.path!=='string'||!['added','removed','changed'].includes(item.change)||!validPreview(item.before)||!validPreview(item.after))return false;
    const pointer=item.tokens.map(token=>'/'+String(token).replaceAll('~','~0').replaceAll('/','~1')).join('');
    return pointer===item.path&&(item.change==='added'?!item.before.present&&item.after.present:item.change==='removed'?item.before.present&&!item.after.present:item.before.present&&item.after.present);}
  function renderValue(row,value){const box=cell(row,value.present?value.text:'Nicht vorhanden');if(value.truncated){const note=document.createElement('small');note.textContent='Anzeige gekürzt · SHA-256 '+value.sha256;box.append(document.createElement('br'),note);}box.title=value.type+' · '+(value.sha256||'kein Wert');}
  async function compare(offset=0){if(!selected||!head||stale||busy)return;const id=selected.id,body=input(),snapshot=JSON.stringify(body),pin=offset?result?.comparison_sha256:null;
    return run('diff',()=>selected?.id===id&&snapshot===JSON.stringify(input()),async request=>{
      if(!body.before_sha256||!body.after_sha256)throw new Error('Beide ausgewählten Revisionen zunächst in der Historie laden.');
      result=null;el('history-diff').replaceChildren();el('history-diff-page').textContent='Vergleich wird vollständig geprüft …';
      const query=new URLSearchParams({...body,offset,limit:50});if(pin)query.set('comparison_sha256',pin);
      const value=await request.wait(api('resources/'+id+'/history-diff?'+query));
      if(!validCommon(value)||value.complete!==true||value.arrays_positional!==true||value.current_revision!==head.revision||value.before_revision!==body.before||value.after_revision!==body.after||value.before_sha256!==body.before_sha256||value.after_sha256!==body.after_sha256||value.area!==body.area||value.query!==body.query||value.offset!==offset||value.limit!==50||!hex(value.comparison_sha256)||!natural(value.total)||!natural(value.filtered)||value.filtered>value.total||!NXData.record(value.counts)||Object.keys(value.counts).length!==Object.keys(labels).length||Object.keys(labels).some(area=>!natural(value.counts[area]))||Object.values(value.counts).reduce((sum,count)=>sum+count,0)!==value.total||!Array.isArray(value.items)||value.items.length!==Math.min(50,Math.max(0,value.filtered-offset))||value.items.some(item=>!validItem(item)))throw new Error('Kein vollständiger gebundener Revisionsvergleich.');
      if(pin&&pin!==value.comparison_sha256)throw new Error('Vergleichskontext geändert; neu vergleichen.');result=value;el('history-diff').replaceChildren();
      for(const item of value.items){
        const row=document.createElement('tr');cell(row,(labels[item.area]||'Weitere Felder')+' · '+{added:'Hinzugefügt',removed:'Entfernt',changed:'Geändert'}[item.change]);const path=cell(row,location(item.tokens));path.title=item.path;renderValue(row,item.before);renderValue(row,item.after);el('history-diff').append(row);}
      el('history-diff-page').textContent=(value.items.length?offset+1:0)+'–'+(offset+value.items.length)+' von '+value.filtered+' Treffern · '+value.total+' Änderungen insgesamt. Arrays positionsbezogen, keine automatische RTYPE-/SEQ-Zuordnung.';
      el('history-state').textContent='Rev '+body.before+' → Rev '+body.after+' · '+Object.entries(value.counts).filter(([,count])=>count).map(([area,count])=>(labels[area]||'Weitere Felder')+': '+count).join(' · ')+(value.total?'':'Keine Inhaltsunterschiede.')+' Keine Rücknahme, Dateiänderung oder fachliche Freigabe.';
    });}
  async function open(resource){reset();selected={id:resource.id,name:resource.name,catalogRevision:resource.revision};el('history-box').hidden=false;el('history-name').textContent=resource.name+' · '+resource.id;showWorkspace('manage','history-title');await load(0,true);}
  function catalogChanged(){if(!selected)return;const current=resources.find(item=>item.id===selected.id);if(!current||current.revision!==(head?.revision??selected.catalogRevision)){NXRequests.invalidate('history.');owner=null;busy=false;stale=true;el('history-state').textContent='Stammdatenbestand seit Historienabruf geändert. Historie ausdrücklich neu laden; alte Vergleichsauswahl nicht automatisch übernehmen.';controls();}}
  el('history-close').onclick=reset;el('history-reload').onclick=()=>load(0,true);el('history-compare').onclick=()=>compare(0);
  el('history-index-prev').onclick=()=>load(Math.max(0,index.offset-20));el('history-index-next').onclick=()=>load(index.offset+20);
  el('history-diff-prev').onclick=()=>compare(Math.max(0,result.offset-50));el('history-diff-next').onclick=()=>compare(result.offset+50);
  ['history-before','history-after','history-area'].forEach(id=>el(id).onchange=clearDiff);el('history-query').oninput=clearDiff;
  el('history-query').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();compare(0);}};
  return {open,reset,catalogChanged};
})();

'use strict';
let token = '', resources = [], releases = [], active = {}, editing = null;
let connectionGeneration = 0;
let noticeGeneration = 0;
let editorGeneration = 0, saveBusy = false, saveOwner = null;
let fileDownloadOwner=null;
const releaseActionOwners=new Map();
let resourcePage=0,resourceFilterSignature='';
const resourcePageSize=100;
globalThis.NXData=(()=>{
  const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&[null,Object.prototype].includes(Object.getPrototypeOf(value));
  const wireNumbers=new WeakMap();
  function set(parent,key,value){if(!record(parent)||typeof key!=='string')throw new Error('Dynamische Daten benötigen JSON-Objekte und Textschlüssel.');Object.defineProperty(parent,key,{value,enumerable:true,writable:true,configurable:true});wireNumbers.get(parent)?.delete(key);return value;}
  function get(parent,key){return record(parent)&&Object.hasOwn(parent,key)?parent[key]:undefined;}
  function branch(parent,key){if(!record(parent))throw new Error('Verschachtelte Daten müssen JSON-Objekte sein.');let value=get(parent,key);if(!Object.hasOwn(parent,key))value=set(parent,key,Object.create(null));if(!record(value))throw new Error('Verschachtelte Werte müssen JSON-Objekte sein; vorhandene Werte werden nicht ersetzt.');return value;}
  function remove(parent,key){if(!record(parent))throw new Error('Dynamische Daten müssen JSON-Objekte sein.');if(Object.hasOwn(parent,key))delete parent[key];wireNumbers.get(parent)?.delete(String(key));}
  function bindWire(value,raw){
    const fail=()=>{throw new Error('JSON-Zahlennachweis passt nicht zur gelesenen Antwort. Erneut lesen; keine Werte umdeuten.');};
    if(typeof raw!=='string')fail();let offset=0;const pending=new Map(),number=/-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/y;
    const space=()=>{while(offset<raw.length&&/[ \t\r\n]/.test(raw[offset]))offset++;};
    function string(){const start=offset;if(raw[offset++]!=='"')fail();for(;offset<raw.length;){const char=raw[offset++];if(char==='\\'){offset++;continue;}if(char==='"'){try{return JSON.parse(raw.slice(start,offset));}catch{fail();}}}fail();}
    const stack=[{type:'value',value,parent:null,key:null}];
    while(stack.length){const task=stack.pop();space();
      if(task.type==='object'){
        if(raw[offset]==='}'){offset++;if(task.seen.size!==Object.keys(task.value).length)fail();continue;}
        if(task.next){if(raw[offset++]!==',')fail();space();}
        const key=string();if(!Object.hasOwn(task.value,key)||task.seen.has(key))fail();task.seen.add(key);space();if(raw[offset++]!==':')fail();
        stack.push({...task,next:true},{type:'value',value:task.value[key],parent:task.value,key});continue;
      }
      if(task.type==='array'){
        if(raw[offset]===']'){offset++;if(task.index!==task.value.length)fail();continue;}
        if(task.next){if(raw[offset++]!==',')fail();space();}if(task.index>=task.value.length)fail();
        stack.push({...task,index:task.index+1,next:true},{type:'value',value:task.value[task.index],parent:task.value,key:String(task.index)});continue;
      }
      const item=task.value,char=raw[offset];
      if(char==='{'&&record(item)){if(pending.has(item))fail();pending.set(item,new Map());offset++;stack.push({type:'object',value:item,seen:new Set(),next:false});}
      else if(char==='['&&Array.isArray(item)){if(pending.has(item))fail();pending.set(item,new Map());offset++;stack.push({type:'array',value:item,index:0,next:false});}
      else if(char==='"'&&typeof item==='string'){if(string()!==item)fail();}
      else if(typeof item==='number'){
        number.lastIndex=offset;const match=number.exec(raw);if(!match||!Object.is(Number(match[0]),item))fail();offset=number.lastIndex;
        if(task.parent){let fields=pending.get(task.parent);if(!fields){fields=new Map();pending.set(task.parent,fields);}fields.set(task.key,{value:item,literal:match[0]});}
      }else{const literal=item===null?'null':item===true?'true':item===false?'false':null;if(!literal||raw.slice(offset,offset+literal.length)!==literal)fail();offset+=literal.length;}
    }
    space();if(offset!==raw.length)fail();for(const [parent,fields] of pending)wireNumbers.set(parent,fields);return value;
  }
  function parseWire(raw){try{return bindWire(JSON.parse(raw),raw);}catch{throw new Error('Kein vollständiger eindeutiger JSON-Text. Daten prüfen und erneut eingeben.');}}
  function numberLiteral(parent,key,value){if(!record(parent)&&!Array.isArray(parent))return null;const proof=wireNumbers.get(parent)?.get(String(key));return proof&&Object.is(proof.value,value)?proof.literal:null;}
  // JSON protocol copies must keep number tokens, not clone their rounded JS doubles.
  function stringifyWire(value,space=0){
    const gap=typeof space==='string'?space.slice(0,10):' '.repeat(Math.min(10,Math.max(0,Math.floor(Number(space)||0)))),ancestors=new Set();let nodes=0;
    const fail=()=>{throw new Error('JSON-Daten enthalten nicht sicher darstellbare Werte. Zahlen erneut als JSON-Text eingeben.');};
    function encode(item,parent,key,depth){
      if(++nodes>2000000||depth>512)throw new Error('JSON-Daten überschreiten die Strukturgrenze.');
      if(typeof item==='number'){
        const literal=numberLiteral(parent,key,item);
        if(literal!==null){
          if(/[.eE]/.test(literal)&&(!Number.isFinite(item)||item===0&&/[1-9]/.test(literal.split(/[eE]/)[0])))fail();
          return literal;
        }
        if(!Number.isFinite(item)||Number.isInteger(item)&&!Number.isSafeInteger(item))fail();
        return JSON.stringify(item);
      }
      if(item===null||typeof item==='string'||typeof item==='boolean')return JSON.stringify(item);
      if(['undefined','function','symbol'].includes(typeof item))return undefined;
      if(!record(item)&&!Array.isArray(item)||ancestors.has(item))fail();
      ancestors.add(item);const values=[],indent=gap.repeat(depth+1),separator=gap?',\n'+indent:',',colon=gap?': ':':';
      let open,close;
      if(Array.isArray(item)){open='[';close=']';for(let index=0;index<item.length;index++)values.push(encode(item[index],item,String(index),depth+1)??'null');}
      else{open='{';close='}';for(const name of Object.keys(item)){const result=encode(item[name],item,name,depth+1);if(result!==undefined)values.push(JSON.stringify(name)+colon+result);}}
      ancestors.delete(item);return values.length?open+(gap?'\n'+indent:'')+values.join(separator)+(gap?'\n'+gap.repeat(depth):'')+close:open+close;
    }
    const result=encode(value,null,null,0);if(result!==undefined&&new TextEncoder().encode(result).byteLength>64*1024*1024)throw new Error('JSON-Daten überschreiten die Byte-Grenze.');return result;
  }
  const cloneWire=value=>parseWire(stringifyWire(value));
  return {record,set,get,branch,remove,bindWire,parseWire,numberLiteral,stringifyWire,cloneWire};
})();
/* Search only the already-read catalog snapshot, never NX files or live ERP. */
globalThis.NXResourceSearch=(()=>{
  let cache=new WeakMap(),specs=new Map();
  const text=value=>typeof value==='string',record=value=>NXData.record(value);
  function configure(values){specs=new Map((Array.isArray(values)?values:[]).filter(value=>record(value)&&text(value.id)&&text(value.label)).map(value=>[value.id,value.label]));cache=new WeakMap();}
  function describe(resource){
    const previous=cache.get(resource);if(previous)return previous;
    const cam=record(resource.cam)?resource.cam:{},native=text(cam.library_id)&&cam.library_id.length>0,legacy=!native&&resource.kind==='tool'&&record(cam.values),keys=new Set(),classes=new Set(),terms=[];
    const add=value=>{if(text(value)&&value.length)terms.push(value);};
    [resource.id,resource.name,resource.erp?.number].forEach(add);
    if(native||legacy){add(cam.library_id);add(cam.nx_version);add(cam.class_name);if(text(cam.class_name)&&cam.class_name)classes.add(cam.class_name);
      const values=value=>{if(!record(value))return;const key=Object.hasOwn(value,'LIBRF')?value.LIBRF:value.NAME;if(text(key)&&key)keys.add(key);for(const [field,content] of Object.entries(value)){add(field);add(content);}};
      if(native&&Array.isArray(cam.records))for(const row of cam.records){if(!record(row))continue;if(text(row.class_name)&&row.class_name){classes.add(row.class_name);add(row.class_name);}add(row.block_id);values(row.values);}
      else if(legacy)values(cam.values);
    }
    const result=Object.freeze({library:native?cam.library_id:legacy?'__legacy_tool__':'__no_native__',
      keys:Object.freeze([...keys]),classes:Object.freeze([...classes]),rows:native&&Array.isArray(cam.records)?cam.records.length:legacy?1:0,
      search:terms.join('\n').toLocaleLowerCase('de')});cache.set(resource,result);return result;
  }
  function choices(values,selected=''){const libraries=new Set([...specs.keys(),...values.map(value=>describe(value).library)]);if(text(selected)&&selected)libraries.add(selected);return {'':'Alle Bibliothekszuordnungen',__no_native__:'Ohne native Bibliothekszuordnung',__legacy_tool__:'Älteres Werkzeugformat ohne Bibliotheks-ID',
    ...Object.fromEntries([...libraries].filter(value=>!['__no_native__','__legacy_tool__'].includes(value)).sort().map(value=>[value,(specs.get(value)||'Gespeicherte Bibliotheks-ID')+' · '+value]))};}
  function label(value){const data=describe(value);if(data.library==='__no_native__')return 'Keine native Bibliothekszuordnung';
    const library=data.library==='__legacy_tool__'?'Älteres Werkzeugformat (keine Bibliotheks-ID)':data.library;
    return library+'\nKennung: '+(data.keys.length?data.keys.slice(0,3).join(' · '):'nicht angegeben')+(data.keys.length>3?' · '+(data.keys.length-3)+' weitere Kennungen in Metadaten':'')+'\n'+data.rows+' gespeicherte Zeile(n)'+(data.classes.length?' · '+data.classes.slice(0,3).join(' · ')+(data.classes.length>3?' · weitere Klassen':''):'');}
  return {configure,describe,choices,label};
})();
globalThis.NXRequests=(()=>{
  let epoch=0;const slots=new Map();
  function stale(){const error=new Error('Veraltete Oberflächenantwort verworfen.');error.name='StaleUIReply';return error;}
  function begin(name,guard=()=>true){const serial=(slots.get(name)||0)+1,ownEpoch=epoch,session=connectionGeneration;slots.set(name,serial);
    const current=()=>{try{return ownEpoch===epoch&&session===connectionGeneration&&slots.get(name)===serial&&guard();}catch{return false;}};
    const check=()=>{if(!current())throw stale();};
    return {current,check,async wait(promise){try{const value=await promise;check();return value;}catch(error){check();throw error;}}};}
  return {begin,stale,isStale:error=>error?.name==='StaleUIReply',async settle(promise){try{return await promise;}catch(error){if(error?.name!=='StaleUIReply')throw error;}},invalidate:prefix=>{for(const [name,value] of slots)if(name.startsWith(prefix))slots.set(name,value+1);},reset:()=>{epoch++;slots.clear();}};
})();
globalThis.NXJobState=(()=>{
  function label(job){
    const value=job.execution,base=job.status+' · '+job.progress+' %';
    if(value?.contract_version!==1)return base;
    if(value.historical)return base+' · Historischer Auftrag (Wiederherstellung)';
    if(value.writer_may_be_active){
      const reason=value.child==='unknown'?'Unterprozess ungeklärt':value.child==='live'?'Unterprozess aktiv':value.future==='live'?'Ausführung aktiv':'Ausführung ungeklärt';
      return base+' · '+reason+(value.persisted_status!==job.status?' · Gespeicherter Status: '+value.persisted_status:'');
    }
    return base+(value.future==='failed'?' · Task unerwartet beendet':value.future==='cancelled'?' · Task abgebrochen':'');
  }
  return {label};
})();
const $ = id => document.getElementById(id);
const tests = {tools:'Kundenwerkzeuge geladen und Geometrie geprüft',machines:'Maschinen vollständig und ohne Ladefehler geprüft',posts:'Postprozessoren und NC-Ausgabe fachlich geprüft',simulation:'Maschinensimulation / Kollisionen geprüft',fixtures:'Spannmittel geprüft',inspection:'Prüfmittel / CMM geprüft oder Nichtanwendbarkeit begründet'};
let kinds = {tool:'Werkzeug',fixture:'Spannmittel',gauge:'Prüfmittel',machine:'Maschine'};
const workspaces = {manage:'Stammdaten verwalten',prepare:'Stammdaten anlegen & aufbereiten',update:'Versionsupdate'};
let workspace = 'manage';
function showWorkspace(name, focusId) {
  if (!Object.hasOwn(workspaces,name)) name='manage';
  if(workspace!==name){NXRequests.invalidate('editor.sample');NXRequests.invalidate('details.');globalThis.NXSchemaImpact?.workspaceChanged?.();globalThis.NXAudit?.workspaceChanged();clearReleaseValidation();clearHistoricalValidation();$('details-box').open=false;message('');}
  workspace=name;
  Object.keys(workspaces).forEach(key=>{
    $('panel-'+key).hidden=key!==name;
    $('tab-'+key).setAttribute('aria-selected',String(key===name));
    $('tab-'+key).tabIndex=key===name?0:-1;
  });
  if(location.hash!=='#'+name)history.replaceState(null,'','#'+name);
  if(focusId){const target=$(focusId);target.scrollIntoView({block:'start'});target.focus();}
}
document.querySelectorAll('[role="tab"]').forEach(tab=>{
  tab.onclick=()=>showWorkspace(tab.dataset.workspace);
  tab.onkeydown=event=>{
    const keys=Object.keys(workspaces),index=keys.indexOf(tab.dataset.workspace);
    let next;
    if(['ArrowRight','ArrowDown'].includes(event.key))next=(index+1)%keys.length;
    else if(['ArrowLeft','ArrowUp'].includes(event.key))next=(index+keys.length-1)%keys.length;
    else if(event.key==='Home')next=0;
    else if(event.key==='End')next=keys.length-1;
    else return;
    event.preventDefault();showWorkspace(keys[next]);$('tab-'+keys[next]).focus();
  };
});
window.addEventListener('hashchange',()=>showWorkspace(location.hash.slice(1)));
function message(text, error=false) { noticeGeneration++;$('message').textContent = text; $('message').className = error ? 'error' : 'success'; }
function details(value,validationRelease=null) { NXRequests.invalidate('details.');clearReleaseValidation();clearHistoricalValidation();$('details').textContent = NXData.stringifyWire(value,2);$('detail-context').textContent='Arbeitsbereich: '+workspaces[workspace];$('details-box').open=true;if(validationRelease)showReleaseValidation(validationRelease,value); }
async function loadDetails(path,releaseId=null){const request=NXRequests.begin('details.read'),value=await request.wait(api(path));details(value);if(releaseId&&value?.id===releaseId&&value.validation!==null)showReleaseValidation(releases.find(item=>item.id===releaseId),value.validation);}
$('details-box').querySelector('summary').addEventListener('click',()=>{if($('details-box').open)NXRequests.invalidate('details.');});
function releaseValidationModel(release,report){
  const record=NXData.record,text=value=>typeof value==='string'&&value.trim().length>0,nat=value=>Number.isSafeInteger(value)&&value>=0,
    strings=value=>Array.isArray(value)&&value.every(text),hex=value=>text(value)&&(/^[a-f0-9]{64}$/).test(value),fail=()=>{throw new Error('Kein vollständiger passender technischer Prüfbericht. Rohdaten und aktuellen Bestand prüfen.');};
  const timestamp=value=>{if(!text(value))return false;const m=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|([+-])(\d{2}):(\d{2}))$/.exec(value);if(!m)return false;
    const year=Number(m[1]),month=Number(m[2]),day=Number(m[3]),leap=year%4===0&&(year%100!==0||year%400===0),days=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31];
    return year>=1&&month>=1&&month<=12&&day>=1&&day<=days[month-1]&&Number(m[4])<=23&&Number(m[5])<=59&&Number(m[6])<=59&&Number(m[10]||0)<=23&&Number(m[11]||0)<=59&&Number.isFinite(Date.parse(value));};
  const same=(a,b,depth=0)=>{if(depth>12)return false;if(a===b)return true;if(Array.isArray(a)&&Array.isArray(b))return a.length===b.length&&a.every((value,index)=>same(value,b[index],depth+1));if(record(a)&&record(b)){const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(key=>Object.hasOwn(b,key)&&same(a[key],b[key],depth+1));}return false;};
  if(!record(release)||!text(release.id)||!(/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/).test(release.id)||!text(release.name)||!text(release.nx_version)||!(/^\d{4}$/).test(release.nx_version)||!text(release.folder)||release.host_folder!==null&&!text(release.host_folder)||!record(report)||!same(report,release.validation)||
      typeof report.integrity_passed!=='boolean'||!strings(report.errors)||report.integrity_passed!==(report.errors.length===0)||!nat(report.file_count)||report.tool_count!==null&&!nat(report.tool_count)||
      report.functional_validation_required!==true||!timestamp(report.checked_at)||typeof report.approval_retained!=='boolean'||typeof report.approval_requires_requalification!=='boolean'||
      report.approval_retained&&(report.approval_requires_requalification||!record(release.review))||!strings(report.review_invalidation_reasons)||!Array.isArray(report.libraries)||report.libraries.length>1000||!record(report.references))fail();
  const expected=report.integrity_passed?(report.approval_retained?'approved':'validated'):'blocked';if(release.status!==expected)fail();
  const refs=report.references;if(!Array.isArray(refs.errors)||refs.errors.length>report.errors.length||!Array.isArray(refs.warnings)||!nat(refs.warning_rows)||refs.automatic_geometry_validation!==false)fail();
  const ids=new Set(),paths=new Set(),rows=[];
  for(const library of report.libraries){
    if(!record(library)||!text(library.id)||ids.has(library.id)||!text(library.path)||paths.has(library.path.toLowerCase())||!text(library.label)||!strings(library.errors)||!Array.isArray(library.schemas)||
       !library.errors.length&&(!nat(library.row_count)||!nat(library.object_count)||library.object_count>library.row_count||!library.schemas.length)||library.errors.length&&report.integrity_passed)fail();
    ids.add(library.id);paths.add(library.path.toLowerCase());
    const blocks=new Set();for(const block of library.schemas){if(!record(block)||!text(block.id)||blocks.has(block.id)||!text(block.class_name)||!strings(block.fields)||!block.fields.length||new Set(block.fields).size!==block.fields.length||!hex(block.schema_hash))fail();blocks.add(block.id);}
    rows.push({kind:'library',label:'DAT-Bibliothek',target:library.label+' · '+library.id,path:library.path,detail:library.errors.length?'Nicht lesbar: '+library.errors.join(' · '):library.object_count+' Objekte · '+library.row_count+' DATA-Zeilen · '+library.schemas.length+' FORMAT-Blöcke'});
  }
  for(const error of refs.errors)if(!record(error)||!text(error.library)||!text(error.reason))fail();
  for(const error of report.errors){let parsed=null;try{parsed=JSON.parse(error);}catch{}const relational=record(parsed)&&text(parsed.library)&&text(parsed.reason);rows.push({kind:'error',label:'Blockierender Befund',target:relational?parsed.library:'Datenstand',path:relational&&typeof parsed.field==='string'?parsed.field:'',detail:relational?[parsed.reason,parsed.key,parsed.value,parsed.target].filter(value=>typeof value==='string'&&value.length).join(' · '):error});}
  let warningRows=0;
  for(const warning of refs.warnings){if(!record(warning)||!text(warning.library)||!text(warning.field)||!text(warning.value)||!text(warning.target)||!text(warning.reason)||warning.standard_object!==true||!nat(warning.affected_rows)||warning.affected_rows<1||!strings(warning.example_keys))fail();warningRows+=warning.affected_rows;if(!Number.isSafeInteger(warningRows))fail();rows.push({kind:'warning',label:'Standard-Verweishinweis',target:warning.library,path:warning.field+' → '+warning.target,detail:warning.value+' · '+warning.reason+' · '+warning.affected_rows+' betroffene Zeilen; Beispielkennungen: '+warning.example_keys.join(', ')});}
  if(warningRows!==refs.warning_rows)fail();
  for(const reason of report.review_invalidation_reasons)rows.push({kind:'review',label:'Freigabebindung prüfen',target:'Bestehender Abnahmebeleg',path:'',detail:reason});
  return {release_id:release.id,name:release.name,nx_version:release.nx_version,folder:release.host_folder||release.folder,checked_at:report.checked_at,status:release.status,
    passed:report.integrity_passed,files:report.file_count,tools:report.tool_count,libraries:report.libraries.length,errors:report.errors.length,warnings:refs.warnings.length,warning_rows:refs.warning_rows,
    approval_retained:report.approval_retained,requires_requalification:report.approval_requires_requalification,rows};
}
let releaseValidationView=null,releaseValidationReport=null,releaseValidationPage=0;
function clearReleaseValidation(){releaseValidationView=null;releaseValidationReport=null;releaseValidationPage=0;$('release-validation-box').hidden=true;$('release-validation-items').replaceChildren();$('release-validation-state').textContent='';$('release-validation-context').textContent='';$('release-validation-query').value='';$('release-validation-kind').value='';$('release-validation-page').textContent='';$('release-validation-prev').disabled=true;$('release-validation-next').disabled=true;}
function syncReleaseValidation(){if(!releaseValidationView)return;try{const current=releases.find(item=>item.id===releaseValidationView.release_id),next=releaseValidationModel(current,releaseValidationReport);
  for(const key of ['name','nx_version','folder','status'])if(next[key]!==releaseValidationView[key])throw new Error('Geänderter Datenstand.');
  }catch{clearReleaseValidation();$('release-validation-box').hidden=false;$('release-validation-state').textContent='Datenstand oder Prüfbericht geändert. Diese Übersicht ist nicht mehr aktuell; Ergebnis und Rohdaten erneut prüfen.';}}
function renderReleaseValidation(){if(!releaseValidationView)return;const query=$('release-validation-query').value.toLocaleLowerCase('de'),kind=$('release-validation-kind').value;
  const items=releaseValidationView.rows.filter(row=>(!kind||row.kind===kind)&&(!query||[row.label,row.target,row.path,row.detail].join(' ').toLocaleLowerCase('de').includes(query)));
  releaseValidationPage=Math.min(releaseValidationPage,Math.max(0,Math.ceil(items.length/50)-1));const offset=releaseValidationPage*50;
  $('release-validation-items').replaceChildren(...items.slice(offset,offset+50).map(item=>{const row=document.createElement('tr');cell(row,item.label);cell(row,item.target);cell(row,item.path);cell(row,item.detail);return row;}));
  $('release-validation-page').textContent=(items.length?offset+1:0)+'–'+Math.min(offset+50,items.length)+' von '+items.length+' passenden Einträgen · '+releaseValidationView.rows.length+' insgesamt';
  $('release-validation-prev').disabled=releaseValidationPage===0;$('release-validation-next').disabled=offset+50>=items.length;
}
function showReleaseValidation(release,report){$('release-validation-box').hidden=false;try{releaseValidationView=releaseValidationModel(release,report);releaseValidationReport=structuredClone(report);
  const value=releaseValidationView;$('release-validation-state').textContent=(value.passed?'Technisch ohne blockierende Befunde.':value.errors+' blockierende Befunde.')+' '+value.files+' Dateien · '+value.libraries+' gefundene bekannte DAT-Bibliotheken · Metric-Werkzeugleser: '+(value.tools===null?'nicht bestimmt':value.tools)+' · '+value.warnings+' Standard-Verweisgruppen / '+value.warning_rows+' betroffene Zeilen.';
  $('release-validation-context').textContent=value.name+' · NX'+value.nx_version+' · '+value.release_id+' · '+value.folder+' · Server-Prüfzeit '+value.checked_at+' · Status '+value.status+'. '+(value.approval_retained?'Bestehende Freigabebindung laut Server beibehalten.':value.requires_requalification?'Bestehende Freigabebindung muss neu geprüft werden.':'Keine bestehende Freigabebindung beibehalten.');renderReleaseValidation();
  }catch(error){clearReleaseValidation();$('release-validation-box').hidden=false;$('release-validation-state').textContent='Lesbare Übersicht nicht bestätigt: '+error.message+' Die Serveraktion wird nicht wiederholt.';}}
for(const id of ['release-validation-kind','release-validation-query'])$(id).oninput=()=>{releaseValidationPage=0;renderReleaseValidation();};
$('release-validation-prev').onclick=()=>{releaseValidationPage=Math.max(0,releaseValidationPage-1);renderReleaseValidation();};
$('release-validation-next').onclick=()=>{releaseValidationPage++;renderReleaseValidation();};
clearReleaseValidation();
function historicalValidationCandidate(report){return NXData.record(report)&&['approval_retained','approval_requires_requalification','review_invalidation_reasons'].every(key=>!Object.hasOwn(report,key));}
function historicalValidationModel(release){
  const report=release?.validation,record=NXData.record,text=value=>typeof value==='string'&&value.trim().length>0,nat=value=>Number.isSafeInteger(value)&&value>=0,fail=()=>{throw new Error('Kein vollständig lesbarer historischer Prüfstand. Originaldaten bleiben erhalten; keine neue Prüfung ausgelöst.');};
  if(!record(release)||!text(release.id)||!(/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/).test(release.id)||!text(release.name)||typeof release.nx_version!=='string'||!(/^\d{4}$/).test(release.nx_version)||!text(release.folder)||release.host_folder!==null&&!text(release.host_folder)||!['draft','validated','approved','blocked'].includes(release.status)||!historicalValidationCandidate(report)||typeof report.integrity_passed!=='boolean'||!Array.isArray(report.errors)||report.errors.length>100000||!report.errors.every(text)||report.integrity_passed!==(report.errors.length===0)||!nat(report.file_count)||report.tool_count!==null&&!nat(report.tool_count)||report.functional_validation_required!==true||typeof report.checked_at!=='string')fail();
  const stamp=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|([+-])(\d{2}):(\d{2}))$/.exec(report.checked_at);if(!stamp)fail();
  const year=Number(stamp[1]),month=Number(stamp[2]),day=Number(stamp[3]),leap=year%4===0&&(year%100!==0||year%400===0),days=[31,leap?29:28,31,30,31,30,31,31,30,31,30,31];
  if(year<1||month<1||month>12||day<1||day>days[month-1]||Number(stamp[4])>23||Number(stamp[5])>59||Number(stamp[6])>59||Number(stamp[10]||0)>23||Number(stamp[11]||0)>59||!Number.isFinite(Date.parse(report.checked_at)))fail();
  const rows=report.errors.map(error=>{let value=null;try{value=JSON.parse(error);}catch{}
    if(record(value)&&['library','field','key','target','value','reason'].every(key=>text(value[key])))return {library:value.library,field:value.field,detail:value.reason+' · damaliges Objekt: '+value.key+' · damaliger Wert: '+value.value+' · damalige Zielbibliothek: '+value.target};
    return {library:'Gespeicherter Altbericht',field:'',detail:error};});
  return {release_id:release.id,name:release.name,nx_version:release.nx_version,folder:release.host_folder||release.folder,status:release.status,checked_at:report.checked_at,files:report.file_count,tools:report.tool_count,rows,
    read_only:true,historical:true,current_validation_confirmed:false,files_verified:false,approval_confirmed:false};
}
let historicalValidationView=null,historicalValidationPage=0;
function clearHistoricalValidation(){historicalValidationView=null;historicalValidationPage=0;$('historical-validation-box').hidden=true;$('historical-validation-items').replaceChildren();$('historical-validation-state').textContent='';$('historical-validation-context').textContent='';$('historical-validation-query').value='';$('historical-validation-page').textContent='';$('historical-validation-prev').disabled=true;$('historical-validation-next').disabled=true;}
function renderHistoricalValidation(){if(!historicalValidationView)return;const query=$('historical-validation-query').value.toLocaleLowerCase('de'),rows=historicalValidationView.rows.filter(row=>[row.library,row.field,row.detail].join(' ').toLocaleLowerCase('de').includes(query));
  historicalValidationPage=Math.min(historicalValidationPage,Math.max(0,Math.ceil(rows.length/50)-1));const offset=historicalValidationPage*50;
  $('historical-validation-items').replaceChildren(...rows.slice(offset,offset+50).map(item=>{const row=document.createElement('tr');cell(row,item.library);cell(row,item.field);cell(row,item.detail);return row;}));
  $('historical-validation-page').textContent=(rows.length?offset+1:0)+'–'+Math.min(offset+50,rows.length)+' von '+rows.length+' passenden damaligen Befunden';$('historical-validation-prev').disabled=historicalValidationPage===0;$('historical-validation-next').disabled=offset+50>=rows.length;
}
function showHistoricalValidation(release){details(release.validation);$('historical-validation-box').hidden=false;try{const view=historicalValidationModel(release),hash=NXUpgradePlan.hash(release.validation);historicalValidationView={...view,report_hash:hash};historicalValidationPage=0;
  $('historical-validation-state').textContent=view.rows.length+' damals gemeldete Befunde · historischer gespeicherter Prüfstand, keine aktuelle Datei- oder NX-Prüfung.';
  $('historical-validation-context').textContent=view.name+' · NX'+view.nx_version+' · '+view.release_id+' · '+view.folder+' · damalige Prüfzeit '+view.checked_at+' · '+view.files+' damals gezählte Dateien · gespeicherter Status '+view.status+'. Keine Freigabe oder Aktivierung durch diese Ansicht.';renderHistoricalValidation();}
  catch(error){clearHistoricalValidation();$('historical-validation-box').hidden=false;$('historical-validation-state').textContent='Historische Übersicht nicht bestätigt: '+error.message+' Original-JSON darunter lesen; keine neue Prüfung oder Freigabe.';}}
function syncHistoricalValidation(){if(!historicalValidationView)return;try{const source=releases.find(release=>release.id===historicalValidationView.release_id),value=historicalValidationModel(source);if(NXUpgradePlan.hash(source.validation)!==historicalValidationView.report_hash||['name','nx_version','folder','status'].some(key=>value[key]!==historicalValidationView[key]))throw new Error('Geänderter Altbericht.');}
  catch{clearHistoricalValidation();$('historical-validation-box').hidden=false;$('historical-validation-state').textContent='Datenstand oder gespeicherter Altbericht geändert. Historische Befunde erneut bewusst öffnen; keine automatische Wiederholung.';}}
$('historical-validation-query').oninput=()=>{historicalValidationPage=0;renderHistoricalValidation();};$('historical-validation-prev').onclick=()=>{historicalValidationPage=Math.max(0,historicalValidationPage-1);renderHistoricalValidation();};$('historical-validation-next').onclick=()=>{historicalValidationPage++;renderHistoricalValidation();};
clearHistoricalValidation();
async function readNumberedResponse(response,alive){
  const fail=()=>new Error('Kein vollständiger JSON-Zahlennachweis. Antwort erneut lesen; gespeicherte Daten bleiben unverändert.');
  let reader=null;const check=()=>{if(!alive())throw NXRequests.stale();};
  try{
    reader=response.clone().body.getReader();const decoder=new TextDecoder('utf-8',{fatal:true}),parts=[];let size=0;
    check();for(;;){const chunk=await reader.read();check();if(chunk.done)break;if(!(chunk.value instanceof Uint8Array))throw fail();size+=chunk.value.byteLength;if(size>64*1024*1024)throw fail();parts.push(decoder.decode(chunk.value,{stream:true}));}
    parts.push(decoder.decode());check();const value=await response.json();check();return NXData.bindWire(value,parts.join(''));
  }catch(error){const cleanup=[];try{if(reader)cleanup.push(reader.cancel());}catch{}try{if(response.body)cleanup.push(response.body.cancel());}catch{}await Promise.allSettled(cleanup);if(NXRequests.isStale(error))throw error;throw fail();}
  finally{reader?.releaseLock?.();}
}
async function readWorkspaceResponse(response,alive){
  const fail=()=>new Error('Kein vollständiger bytegeprüfter Workspace-Stand. Server/Verbindung prüfen und Bestand erneut laden.'),length=response.headers.get('X-NX-Workspace-Bytes'),expected=response.headers.get('X-NX-Workspace-SHA256');
  if(response.headers.get('X-NX-Workspace-Contract')!=='1'||!length||!(/^[1-9][0-9]*$/).test(length)||!Number.isSafeInteger(Number(length))||!expected||!(/^[a-f0-9]{64}$/).test(expected)||response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase()!=='application/json'||!response.body?.getReader){await response.body?.cancel().catch(()=>{});throw fail();}
  const reader=response.body.getReader(),hash=new jsSHA('SHA-256','UINT8ARRAY'),decoder=new TextDecoder('utf-8',{fatal:true}),parts=[];let size=0;
  const check=()=>{if(!alive())throw NXRequests.stale();};
  try{check();for(;;){const chunk=await reader.read();check();if(chunk.done)break;if(!(chunk.value instanceof Uint8Array))throw fail();size+=chunk.value.byteLength;if(!Number.isSafeInteger(size)||size>Number(length))throw fail();hash.update(chunk.value);parts.push(decoder.decode(chunk.value,{stream:true}));}
    if(size!==Number(length)||hash.getHash('HEX')!==expected)throw fail();parts.push(decoder.decode());check();return NXData.parseWire(parts.join(''));
  }catch(error){await reader.cancel().catch(()=>{});if(NXRequests.isStale(error))throw error;throw fail();}finally{reader.releaseLock?.();}
}
async function api(path, method='GET', body,extraHeaders={},scopeCurrent=()=>true) {
  const generation=connectionGeneration, sessionToken=token;
  const response = await fetch('/api/v1/' + path, {method, headers:{...extraHeaders,Authorization:'Bearer ' + token,...(body === undefined ? {} : {'Content-Type':'application/json'})},...(body === undefined ? {} : {body:NXData.stringifyWire(body)})});
  const alive=()=>generation===connectionGeneration&&sessionToken===token&&scopeCurrent();
  const result = path==='workspace'&&method==='GET'&&response.ok?await readWorkspaceResponse(response,alive):await readNumberedResponse(response,alive);
  if(generation!==connectionGeneration || sessionToken!==token||!scopeCurrent())throw NXRequests.stale();
  if (!response.ok) throw Object.assign(new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail)),{httpStatus:response.status});
  return result;
}
function action(fn) { return async () => { const generation=connectionGeneration;try { await fn(); } catch(e) { if(generation===connectionGeneration&&!NXRequests.isStale(e))message(e.message, true); } }; }
function button(text, fn) { const b=document.createElement('button');b.textContent=text;b.onclick=action(fn);return b; }
function cell(row, text) { const td=document.createElement('td');td.textContent=text;row.append(td);return td; }
function cancelFileDownload(quiet=false,reason='Download abgebrochen. Keine Datei an den Browser übergeben; Serverdaten bleiben unverändert.'){
  const own=fileDownloadOwner;fileDownloadOwner=null;own?.controller.abort();$('file-download-cancel').disabled=true;
  if(quiet){$('file-download-box').hidden=true;$('file-download-state').textContent='';}
  else if(own){$('file-download-state').textContent=reason;$('file-download-state').className='';}
}
function checkFileDownloadContext(){if(fileDownloadOwner&&!fileDownloadOwner.current())cancelFileDownload(false,'Download verworfen: Datenstand oder Auswahl geändert. Keine Datei übergeben.');}
async function downloadFile(path,filename,expected={}){
  if(fileDownloadOwner)throw new Error('Ein Dateidownload läuft bereits. Erst abschließen oder den laufenden Download abbrechen.');
  const state=$('file-download-state');$('file-download-box').hidden=false;state.className='';state.textContent='Downloadbeleg wird geprüft …';$('file-download-cancel').disabled=true;
  try{
    if(typeof expected.sha256!=='string'||!(/^[a-f0-9]{64}$/).test(expected.sha256))throw new Error('Download benötigt einen gespeicherten SHA-256-Beleg. Bestand neu laden.');
    if(expected.size!==undefined)NXDownload.metadata(expected);
    if(expected.size>NXDownload.memoryLimit)throw new Error('Datei überschreitet die Grenze für diesen Browserdownload.');
  }catch(error){state.className='error';state.textContent='Datei nicht übergeben: '+error.message;throw error;}
  const session=connectionGeneration,sessionToken=token,sha256=expected.sha256,size=expected.size;
  const own={controller:new AbortController(),current:()=>{try{return fileDownloadOwner===own&&session===connectionGeneration&&sessionToken===token&&!own.controller.signal.aborted&&(!expected.current||expected.current());}catch{return false;}}};
  fileDownloadOwner=own;$('file-download-box').hidden=false;$('file-download-cancel').disabled=false;
  state.textContent='Datei wird empfangen und laufend per SHA-256 geprüft …';
  try{
    if(!own.current())throw NXRequests.stale();
    if(expected.prepare){await expected.prepare();if(!own.current())throw NXRequests.stale();}
    const response=await fetch('/api/v1/'+path,{headers:{Authorization:'Bearer '+sessionToken},signal:own.controller.signal});
    if(!own.current())throw NXRequests.stale();
    if(!response.ok)throw new Error('Dateidownload abgelehnt (HTTP '+response.status+'). Bestand und Dateiintegrität prüfen.');
    // Launcher receipts may omit size. Content-Length is only a bounded
    // transport hint; the pinned SHA authenticates bytes before publication.
    const header=response.headers.get('content-length');
    if(header!==null&&!(/^[1-9][0-9]*$/).test(header))throw new Error('Ungültige Downloadgröße im HTTP-Beleg.');
    const receivedSize=header===null?undefined:Number(header),bound=size??receivedSize;
    NXDownload.metadata({size:bound,sha256});
    if(bound>NXDownload.memoryLimit||receivedSize!==undefined&&(!Number.isSafeInteger(receivedSize)||receivedSize!==bound))throw new Error('Downloadgröße weicht vom Beleg ab oder überschreitet die Browsergrenze.');
    const verified=await NXDownload.receive(response,{size:bound,sha256},{alive:own.current,progress:(bytes,total)=>{if(own.current())state.textContent='Empfangen: '+bytes+' / '+total+' Bytes · SHA wird geprüft, noch nicht veröffentlicht.';}});
    if(!own.current())throw NXRequests.stale();
    if(expected.verify){state.textContent='Empfang vollständig; aktuellen Paketstand vor Übergabe erneut prüfen …';await expected.verify();}
    if(!own.current())throw NXRequests.stale();
    const blob=new Blob([verified.blob],{type:response.headers.get('content-type')||'application/octet-stream'});
    const url=URL.createObjectURL(blob),anchor=document.createElement('a');
    try{anchor.href=url;anchor.download=filename;anchor.click();}finally{setTimeout(()=>URL.revokeObjectURL(url),1000);}
    state.textContent='Prüfsumme und Größe bestätigt. Datei an den Browser übergeben; dessen Speicher-/Abbruchdialog beachten.';
    return {size:verified.size,sha256:verified.sha256};
  }catch(error){const stale=!own.current();
    if(fileDownloadOwner===own){state.className=stale?'':'error';state.textContent=stale?'Download verworfen: Auswahl, Bestand oder Sitzung geändert. Keine Datei übergeben.':'Datei nicht übergeben: '+error.message;}
    own.controller.abort();if(stale)throw NXRequests.stale();throw error;
  }finally{if(fileDownloadOwner===own){fileDownloadOwner=null;$('file-download-cancel').disabled=true;}}
}
function launcherDownloadPin(release){return release&&!['building','failed'].includes(release.status)&&release.nx_version==='2512'&&release.test_launcher?JSON.stringify([release.id,release.nx_version,release.folder,release.host_folder,release.test_launcher.package_revision,release.test_launcher.package_sha256,release.test_launcher.manifest_sha256]):null;}
async function downloadTestLauncherPackage(release,filename,current=()=>true){
  const pin=launcherDownloadPin(release);if(!pin)throw new Error('Kein vollständiger Test-Starter ausgewählt. Bestand neu laden.');
  const id=release.id;let manifest=null;
  const read=async()=>{const fresh=await api('releases/'+id);if(launcherDownloadPin(fresh)!==pin||!Array.isArray(fresh.manifest))throw new Error('Testpaketstand hat sich geändert. Bestand neu laden und den aktuellen Test-Starter ausdrücklich herunterladen.');return JSON.stringify(fresh.manifest);};
  return downloadFile('releases/'+id+'/test-launcher/package',filename,{sha256:release.test_launcher.package_sha256,
    current:()=>current()&&launcherDownloadPin(releases.find(value=>value.id===id))===pin,
    prepare:async()=>{manifest=await read();},
    verify:async()=>{if(await read()!==manifest)throw new Error('Testpaket-Quellmanifest hat sich geändert. Bestand neu laden; keine Datei übergeben.');}});
}
async function downloadTestLauncher(release) {
  const notice=noticeGeneration;await downloadTestLauncherPackage(release,'NX2512_Test_'+release.id.slice(0,8)+'.zip');
  if(notice===noticeGeneration)message('Testpaket vollständig in einen neuen lokalen Ordner entpacken und Start-NX2512-Test.cmd ausführen. Der NX-Datenordner bleibt unverändert.');
}
function options(select, values,showKeys=true) { const previous=select.value;select.replaceChildren();Object.entries(values).forEach(([key,value])=>{const o=document.createElement('option');o.value=key;o.textContent=showKeys&&key?key+' — '+value:value;select.append(o);});if([...select.options].some(o=>o.value===previous))select.value=previous; }
function reset() { editorGeneration++;checkFileDownloadContext();editing=null;$('kind').disabled=false;$('kind').value='tool';$('name').value='';$('camstatus').value='draft';$('cam').value='{}';$('erp').value='null';$('assetids').value='[]';$('assetfile').value='';$('editing').textContent='Neuer Datensatz · noch nicht gespeichert';document.querySelector('#editor-section details.technical').open=false;NXNative.reset();NXAssets.reset();NXSchemas.reset();globalThis.NXFilePackages?.reset();NXGraphicsBindings.reset();globalThis.NXSaves?.render(); }
function openEditor(resource) {
  editorGeneration++;
  checkFileDownloadContext();
  if(resource){editing=resource;$('kind').value=resource.kind;$('name').value=resource.name;$('camstatus').value=resource.cam_status;$('cam').value=NXData.stringifyWire(resource.cam,2);$('erp').value=JSON.stringify(resource.erp,null,2);$('assetids').value=JSON.stringify(resource.asset_ids);$('assetfile').value='';$('editing').textContent='Bearbeite '+resource.name+' · Revision '+resource.revision;NXNative.edit(resource);NXFilePackages.edit(resource);}
  else reset();
  $('kind').disabled=Boolean(editing);
  NXSchemas.reset();
  if(resource)NXAssets.open();
  NXGraphicsBindings.reset();
  showWorkspace('prepare','name');
}
const exportEvidenceLabels={unselected:'Kein Vergleich gewählt',current:'Aktuelle Katalogrevision registriert',older:'Ältere Katalogrevision registriert',review:'Teilbeleg / Klärungsbedarf',origin:'Erfassungsquelle, kein eigener Exportbeleg',no_record:'Kein eigener Exportbeleg'};
function resourceExportEvidence(resource,release){
  const record=NXData.record,own=(value,key)=>record(value)&&Object.hasOwn(value,key),positive=value=>Number.isSafeInteger(value)&&value>0,hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value),text=value=>typeof value==='string'&&value.trim().length>0;
  const parts=[],notes=[],revisions=[];let invalid=false,needsReview=false,hasDat=false,hasPackage=false;
  const result=state=>({state,text:[exportEvidenceLabels[state],...parts,...notes].join('\n')});
  if(!release)return result('unselected');
  if(!['draft','validated','approved'].includes(release.status)){notes.push('Datenstand nicht als vollständiger nutzbarer Stand registriert. Keine Bereitstellungsbestätigung.');return result('review');}
  if(release.catalog!==null&&release.catalog!==undefined&&!record(release.catalog)){notes.push('Exportmetadaten nicht auswertbar.');return result('review');}
  const catalog=release.catalog||{},cam=resource.cam||{},needsDat=Boolean(cam.library_id||resource.kind==='tool'&&record(cam.values)),needsPackage=Boolean(resource.file_package);
  for(const family of ['exported_native','exported_tools','exported_file_packages']){
    const map=NXData.get(catalog,family);if(map!==undefined&&!record(map)){invalid=true;continue;}if(!own(map,resource.id))continue;
    const item=NXData.get(map,resource.id);if(!record(item)||!positive(item.revision)||item.revision>resource.revision){invalid=true;continue;}
    if(family==='exported_file_packages'){
      if(!/^\d{4}$/.test(item.nx_version||'')||item.nx_version!==release.nx_version||!Array.isArray(item.entries)||item.entries.some(entry=>!record(entry)||!text(entry.path)||/[\\:\x00-\x1f]/.test(entry.path)||entry.path.startsWith('/')||entry.path.split('/').some(part=>!part||part==='.'||part==='..')||!Number.isSafeInteger(entry.size)||entry.size<0||!hex(entry.sha256))||new Set(item.entries.map(entry=>entry.path.toLowerCase())).size!==item.entries.length||own(item,'exclusions')&&(!Array.isArray(item.exclusions)||item.exclusions.some(value=>!record(value)||!text(value.path)||!text(value.reason)))||own(item,'qualification_retained')&&typeof item.qualification_retained!=='boolean'){invalid=true;continue;}
      hasPackage=true;parts.push('Dateipaket: Revision '+item.revision+' · '+item.entries.length+' Dateien'+(!item.entries.length?' · kein Paketinhalt registriert':''));
      if(item.qualification_retained===false){needsReview=true;notes.push('Dateipaket nach Versionswechsel erneut fachlich qualifizieren.');}
      if(item.exclusions?.length){needsReview=true;notes.push(item.exclusions.length+' Paketpfade beim Versionswechsel ausgelassen/ersetzt.');}
      if(item.revision===resource.revision&&resource.cam_status!=='retired'){
        const expected=resource.file_package?.entries;
        if(!Array.isArray(expected)||expected.length!==item.entries.length||!item.entries.length||JSON.stringify(expected.map(entry=>entry.path).sort())!==JSON.stringify(item.entries.map(entry=>entry.path).sort())){needsReview=true;notes.push('Registrierter Paketumfang entspricht nicht dem aktuellen Stammdatenpaket.');}
      }
    }else{
      const legacy=family==='exported_tools';
      const malformed=!hex(item.records_hash)||(legacy?(resource.kind!=='tool'||item.id!==resource.id||item.kind!=='tool'||!record(item.cam)||!record(item.cam.values)||!text(item.cam.values.LIBRF)):(!text(item.library_id)||item.library_id!==cam.library_id||!text(item.key)));
      if(malformed){invalid=true;continue;}
      hasDat=true;parts.push((legacy?'DAT (Legacy)':'DAT')+': Revision '+item.revision+(legacy?'':' · '+item.library_id+' / '+item.key));
    }
    revisions.push(item.revision);
  }
  if(invalid){notes.push('Mindestens ein gespeicherter Exportbeleg ist unvollständig oder widersprüchlich.');return result('review');}
  if(!revisions.length){if(resource.origin?.release_id===release.id){notes.push('Dieser Datenstand ist die dokumentierte Erfassungsquelle; kein eigener Export wird daraus abgeleitet.');return result('origin');}notes.push('Kein eigener revisionsgebundener Exportbeleg für diesen Datensatz. Das beweist nicht, dass ein Standard- oder gleichnamiges Objekt in NX fehlt.');return result('no_record');}
  if(resource.cam_status!=='retired'&&needsDat&&!hasDat||needsPackage&&!hasPackage){needsReview=true;notes.push('Für DAT und Dateipaket liegen nicht alle benötigten Teilbelege vor.');}
  if(needsReview)return result('review');
  if(revisions.every(value=>value===resource.revision))return result('current');
  if(revisions.every(value=>value<resource.revision)){notes.push('Neuere Stammdaten sind gespeichert, aber hier nicht als aktuelle Revision registriert.');return result('older');}
  notes.push('DAT und Dateipaket beziehen sich auf unterschiedliche Katalogrevisionen.');return result('review');
}
function syncResourceExportSelection(){options($('resource-release'),{'':'Kein Datenstandvergleich',...Object.fromEntries(releases.map(release=>[release.id,release.name+' · NX'+release.nx_version+' · '+release.status]))});}
function renderResources() {
  options($('resource-library'),NXResourceSearch.choices(resources,$('resource-library').value),false);
  const release=releases.find(item=>item.id===$('resource-release').value)||null;
  $('resource-export-status').disabled=!release;if(!release)$('resource-export-status').value='';
  $('resource-export-context').textContent=release?'Exportmetadaten für '+release.name+' · NX'+release.nx_version+' · '+release.status+' · '+(release.host_folder||release.folder)+'. Kein Datei-/NX-Nachweis und keine Umschaltung des aktiven Ordners.':'Optional einen NX-Datenstand für den revisionsbezogenen Metadatenvergleich wählen.';
  const query=$('resource-query').value.trim().toLocaleLowerCase('de');
  const signature=JSON.stringify([query,$('resource-kind').value,$('resource-status').value,$('resource-library').value,release?.id,$('resource-export-status').value]);
  if(signature!==resourceFilterSignature){resourcePage=0;resourceFilterSignature=signature;}
  const evidence=new Map(resources.map(resource=>[resource.id,resourceExportEvidence(resource,release)]));
  const items=resources.filter(r=>(!$('resource-kind').value||r.kind===$('resource-kind').value)&&(!$('resource-status').value||r.cam_status===$('resource-status').value)&&(!$('resource-library').value||NXResourceSearch.describe(r).library===$('resource-library').value)&&(!$('resource-export-status').value||evidence.get(r.id).state===$('resource-export-status').value)&&(!query||NXResourceSearch.describe(r).search.includes(query)));
  resourcePage=Math.min(resourcePage,Math.max(0,Math.ceil(items.length/resourcePageSize)-1));const offset=resourcePage*resourcePageSize;
  $('resources').replaceChildren();
  items.slice(offset,offset+resourcePageSize).forEach(r=>{const tr=document.createElement('tr');cell(tr,kinds[r.kind]);cell(tr,r.name);cell(tr,r.revision+' / '+r.cam_status);cell(tr,r.erp?.number||'—');const proof=cell(tr,evidence.get(r.id).text);proof.style.whiteSpace='pre-line';const technical=cell(tr,NXResourceSearch.label(r));technical.style.whiteSpace='pre-line';const td=cell(tr,'');
    const rowAction=(label,fn)=>button(label,()=>{if(workspace!=='manage'||!tr.isConnected||!resources.includes(r))return;return fn(r);});
    td.append(rowAction('Bearbeiten',openEditor),rowAction('Historie',resource=>NXHistory.open(resource)));if(!['template','ui_standard'].includes(r.kind))td.append(rowAction('ERP-Bezug prüfen',resource=>NXERPVariants.inspectResource(resource)));$('resources').append(tr);});
  $('resource-count').textContent=`${items.length} von ${resources.length} Stammdatensätzen · Anzeige ${items.length?offset+1:0}–${Math.min(offset+resourcePageSize,items.length)}. Suche gilt für den gesamten Workspace-Stand.`;
  $('resource-prev').disabled=resourcePage===0;$('resource-next').disabled=offset+resourcePageSize>=items.length;
  $('resource-empty').hidden=items.length!==0;
  $('resource-empty').textContent=resources.length?'Keine Stammdaten passen zu den gewählten Filtern.':'Noch keine verwalteten Stammdaten. Vorhandene Kundenobjekte aus einer NX-Bibliothek übernehmen oder einen neuen Entwurf anlegen.';
}
function checkWorkspace(value){
  const record=NXData.record,text=value=>typeof value==='string'&&value.length>0,nat=value=>Number.isSafeInteger(value)&&value>=0,hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value),uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value),date=value=>text(value)&&Number.isFinite(Date.parse(value)),nullable=value=>value===null||record(value),fail=()=>{throw new Error('Kein vollständiger konsistenter Workspace-Vertrag. Bestand erneut laden; keine Teilübernahme.');};
  if(!record(value)||!Array.isArray(value.resources)||!Array.isArray(value.releases)||!record(value.active)||!record(value.snapshot))fail();
  const snapshot=value.snapshot,head=snapshot.audit_head;
  if(snapshot.schema_version!==1||snapshot.consistency!=='sqlite_read_transaction'||snapshot.files_verified!==false||snapshot.audit_verified!==false||snapshot.signed!==false||!date(snapshot.captured_at)||!hex(snapshot.state_sha256)||!record(head)||!nat(head.sequence)||!hex(head.hash)||head.sequence===0&&head.hash!=='0'.repeat(64))fail();
  const resourceIds=new Set(),releaseIds=new Map(),statuses=new Set(['building','draft','validated','approved','blocked','failed']);
  for(const item of value.resources){
    if(!record(item)||!uuid(item.id)||resourceIds.has(item.id)||!nat(item.revision)||item.revision<1||!text(item.name)||typeof item.kind!=='string'||!Object.hasOwn(kinds,item.kind)||!['draft','approved','retired'].includes(item.cam_status)||!date(item.updated_at)||!record(item.cam)||!nullable(item.erp)||!Array.isArray(item.asset_ids)||item.asset_ids.some(id=>!text(id)))fail();resourceIds.add(item.id);
    for(const key of ['geometry'])if(Object.hasOwn(item,key)&&!record(item[key]))fail();
    for(const key of ['origin','model_package','file_package'])if(Object.hasOwn(item,key)&&!nullable(item[key]))fail();
    if(Object.hasOwn(item,'file_bindings')&&(!Array.isArray(item.file_bindings)||item.file_bindings.some(binding=>!record(binding)||Object.values(binding).some(value=>typeof value!=='string'))))fail();
    if(item.file_package&&(!Array.isArray(item.file_package.entries)||item.file_package.entries.some(entry=>!record(entry)||typeof entry.path!=='string'||typeof entry.asset_id!=='string')))fail();
  }
  for(const item of value.releases){
    if(!record(item)||!uuid(item.id)||releaseIds.has(item.id)||!text(item.name)||typeof item.nx_version!=='string'||!/^\d{4}$/.test(item.nx_version)||typeof item.source!=='string'||!text(item.folder)||item.host_folder!==null&&typeof item.host_folder!=='string'||!statuses.has(item.status)||item.parent_id!==null&&!uuid(item.parent_id)||!date(item.created_at)||!nullable(item.validation)||!nullable(item.review)||!nullable(item.catalog)||!nullable(item.test_launcher))fail();releaseIds.set(item.id,item);
    if(item.validation&&Object.hasOwn(item.validation,'file_count')&&!nat(item.validation.file_count))fail();
    for(const key of ['exported_native','exported_tools','exported_file_packages'])if(item.catalog&&Object.hasOwn(item.catalog,key)&&!record(item.catalog[key]))fail();
    const launcher=item.test_launcher;if(launcher&&(!['ready','needs_windows_path'].includes(launcher.status)||launcher.release_id!==item.id||!hex(launcher.package_sha256)||launcher.ui_profile!=null&&(!record(launcher.ui_profile)||!text(launcher.ui_profile.path))))fail();
  }
  const active=value.active,source=releaseIds.get(active.release_id);
  if(!nat(active.generation)||typeof active.usable!=='boolean'||!text(active.usability_reason)||active.release_id!==null&&!uuid(active.release_id))fail();
  if(active.release_id===null){if(active.usable||active.status!=='none'||active.folder!==null||active.host_folder!==null)fail();}
  else if(!source||active.status!==source.status||active.usable&&(source.status!=='approved'||active.folder!==source.folder||active.host_folder!==source.host_folder)||!active.usable&&(active.folder!==null||active.host_folder!==null))fail();
}
async function refresh() {
  const request=NXRequests.begin('core.refresh');$('refresh-state').textContent='Bestand wird geladen …';
  ['resources','releases'].forEach(id=>$(id).setAttribute('aria-busy','true'));
  try{
    const snapshot=await request.wait(api('workspace','GET',undefined,{},request.current));
    checkWorkspace(snapshot);
    ({resources,releases,active}=snapshot);
    $('summary').textContent=`${resources.length} Stammdatensätze · ${releases.length} Ordner-Versionen · Umschaltgeneration ${active.generation} · Metadatenstand ${snapshot.snapshot.audit_head.sequence}`;
    $('summary').title='Ein SQLite-Lesestand; keine Datei-/Auditprüfung oder NX-Freigabe. SHA '+snapshot.snapshot.state_sha256;
    $('active').textContent=active.folder ? 'Lokal aktiver NX-Ordner: '+(active.host_folder || active.folder) : active.release_id?'Aktivzeiger vorhanden, aber nicht nutzbar: Datenstand erneut nach aktueller NX-Abnahme prüfen.':'Noch kein freigegebener Datenstand lokal aktiviert.';
    syncResourceExportSelection();renderResources();renderReleases();
    options($('library-release'),Object.fromEntries(releases.filter(r=>!['failed','building'].includes(r.status)).map(r=>[r.id,r.name])));
    options($('create-release'),Object.fromEntries(releases.filter(r=>!['failed','building'].includes(r.status)).map(r=>[r.id,r.name])));
    const subscribers=[['Exportkontext',()=>NXExports.syncCatalog(resources)],['Native Bibliotheken',()=>NXNative.syncReleaseSelections()],['Schemaeditor',()=>NXSchemas.sync()],['Katalog-Schemafolgen',()=>NXSchemaImpact.sync()],['Editorverweise',()=>NXEditorReferences.sync()],['Dateipakete',()=>NXFilePackages.sync()],['Historie',()=>NXHistory.catalogChanged()],['ERP-Prüfansicht',()=>NXERP.catalogChanged()],['ERP-Varianten',()=>NXERPVariants.catalogChanged(snapshot.snapshot)],['Werkzeugergebnisse',()=>NXResults.catalogChanged()],['NX-Startprofile',()=>NXLaunchProfiles.sync()],['Graphics-Zuordnung',()=>NXGraphicsBindings.sourceChanged()],['Abnahmeansicht',()=>NXReviews.logsChanged()]];
    const run=async([name,load])=>{try{await load();return null;}catch(error){return NXRequests.isStale(error)?null:name+': '+error.message;}};
    const errors=await request.wait(Promise.all(subscribers.map(run)));
    const modules=[['Versionsupdate',()=>NXUpgrade.refresh(request.current)],['3D-Aufbereitung',()=>NXGeometry.refresh(null,request.current)],
      ['Protokolle',()=>refreshLogs(request.current)],['Integrität',()=>NXIntegrity.refresh(request.current)],['Sicherungen',()=>NXBackups.refresh(request.current)],['Ordneraufträge',()=>NXReleaseJobs.refresh(request.current)],['ERP-Modell',()=>NXERPVariants.refreshStatus(request.current)]];
    errors.push(...await request.wait(Promise.all(modules.map(run))));
    $('refresh-state').textContent=errors.some(Boolean)?'Bestand geladen; einzelne Module nicht aktualisiert: '+errors.filter(Boolean).join(' · '):'Bestand aktualisiert. Ungespeicherte Editorwerte bleiben erhalten.';
  }catch(error){if(request.current())$('refresh-state').textContent='Bestand konnte nicht vollständig geladen werden; letzte Anzeige bleibt erhalten. '+error.message;throw error;}
  finally{if(request.current())['resources','releases'].forEach(id=>$(id).setAttribute('aria-busy','false'));}
}
function syncReleaseActionControls(){document.querySelectorAll('[data-release-action]').forEach(control=>{const busy=releaseActionOwners.has(control.dataset.releaseAction);control.disabled=busy;control.setAttribute('aria-busy',String(busy));});}
function releaseActionButton(label,release,endpoint,success=''){
  const control=button(label,async()=>{
    if(!control.isConnected||!token||releaseActionOwners.has(release.id))return;
    const own={session:connectionGeneration},view=NXRequests.begin('details.read'),notice=noticeGeneration;releaseActionOwners.set(release.id,own);syncReleaseActionControls();
    try{
      let value;
      try{value=await api('releases/'+release.id+'/'+endpoint,'POST',endpoint==='test-launcher'?{}:undefined);}
      catch(error){if(view.current()&&notice===noticeGeneration)throw error;return;}
      // A changed display intent is not cancellation of the accepted server
      // action. Refresh its result without reopening an obsolete detail view.
      let refreshError=null;
      try{await NXRequests.settle(refresh());}catch(error){refreshError=error;}
      if(view.current()){
        details(value,endpoint==='validate'?releases.find(item=>item.id===release.id):null);
        if(refreshError&&notice===noticeGeneration)message('Serveraktion beantwortet; Bestand konnte nicht vollständig aktualisiert werden: '+refreshError.message+'. Bestand erneut laden, keinen zweiten Schreibversuch automatisch starten.',true);
        else if(success&&notice===noticeGeneration)message(success);
      }
    }finally{if(releaseActionOwners.get(release.id)===own)releaseActionOwners.delete(release.id);syncReleaseActionControls();}
  });
  control.dataset.releaseAction=release.id;return control;
}
function renderReleases(){
  checkFileDownloadContext();
  syncReleaseValidation();
  syncHistoricalValidation();
  $('releases').replaceChildren();
  releases.forEach(r=>{const tr=document.createElement('tr');cell(tr,r.name+'\n'+r.id);cell(tr,r.status);cell(tr,r.nx_version+' / '+(r.validation?.file_count ?? 'noch ungeprüft'));const td=cell(tr,'');
    if(historicalValidationCandidate(r.validation))td.append(button('Historische Befunde',()=>{if(workspace!=='manage'||!tr.isConnected||!token||!releases.includes(r))return;showHistoricalValidation(r);}));
    if(r.test_launcher){td.append(button('Test-Starter herunterladen',()=>downloadTestLauncher(r)));const info=document.createElement('small');info.textContent=(r.test_launcher.ui_profile?'Gewählte UI-Rolle: '+r.test_launcher.ui_profile.path+' · ': '')+(r.test_launcher.status==='ready'?'Testpaket erstellt.':'Testpaket erstellt; lokaler Windows-Datenpfad wird beim Start abgefragt.');td.append(info);}
    if(r.test_launcher&&r.nx_version==='2512'&&!['failed','building'].includes(r.status))td.append(releaseActionButton('Test-Starter aktualisieren',r,'test-launcher','Test-Starter geprüft/aktualisiert. Den aktuellen Paketstand erneut herunterladen; alte Pakete bleiben erhalten.'));
    else if(r.nx_version==='2512' && !['failed','building'].includes(r.status))td.append(releaseActionButton('Test-Starter vorbereiten',r,'test-launcher','Testpaket für diesen vorhandenen Datenstand erstellt.'));
    const activate=button('Lokal aktivieren',()=>NXReviews.activate(r));activate.dataset.releaseActivate='true';activate.disabled=true;
    const sample=button('NX-Werkzeugschema',()=>NXNative.loadToolExample(r));sample.disabled=['failed','building'].includes(r.status);
    td.append(button('Details',()=>loadDetails('releases/'+r.id,r.id)),releaseActionButton('Prüfen',r,'validate'),sample,button('Neuen Export ableiten',()=>NXExports.open(r)),button('Abnehmen',()=>NXReviews.open(r)),activate);$('releases').append(tr);});
  syncReleaseActionControls();
}
async function refreshLogs(alive=()=>true) { const request=NXRequests.begin('logs.list',alive),logs=await request.wait(api('logs'));options($('syslog'),{'':'Protokoll ausdrücklich wählen',...Object.fromEntries(logs.map(log=>[log.id,log.filename+(log.source_type==='uploaded'?' · hochgeladener Beleg':' · konfiguriert')]))});globalThis.NXReviews?.logsChanged(); }
async function fileBase64(file) { return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('Datei konnte nicht gelesen werden.'));reader.readAsDataURL(file);}); }
function clearSessionView(){cancelFileDownload(true);NXRequests.reset();globalThis.NXAudit?.reset();releaseActionOwners.clear();saveOwner=null;saveBusy=false;resourcePage=0;resourceFilterSignature='';$('save').disabled=false;NXSaves.connectionChanged();NXAssets.connectionChanged();NXGeometry.connectionChanged();
  $('work').style.display='none';resources=[];releases=[];active={};reset();NXNative.clearConnection();NXClone.clearConnection();NXGeometry.reset();NXFolders.reset();NXExports.reset();NXExportData.clearConnection();NXExportFiles.clearConnection();NXUpgrade.reset();NXReleaseJobs.reset();NXSchemas.clearConnection();NXEditorReferences.clearConnection();NXIntegrity.reset();NXBackups.reset();NXReviews.reset();NXFilePackages.clearConnection();NXHistory.reset();NXERP.clearConnection();NXERPVariants.clearConnection();NXLaunchProfiles.clearConnection();
  ['resources','releases'].forEach(id=>{$(id).replaceChildren();$(id).setAttribute('aria-busy','false');});
  ['summary','active','refresh-state','resource-export-context'].forEach(id=>$(id).textContent='');['resource-query','resource-kind','resource-status','resource-library','resource-export-status'].forEach(id=>$(id).value='');NXResourceSearch.configure([]);options($('resource-library'),NXResourceSearch.choices([]),false);options($('resource-release'),{'':'Kein Datenstandvergleich'});$('resource-export-status').disabled=true;
  NXGraphicsBindings.clearConnection();NXSchemaImpact.clearConnection();$('syslog').replaceChildren();clearReleaseValidation();clearHistoricalValidation();$('details').textContent='Noch keine Prüfung ausgewählt.';$('detail-context').textContent='';$('details-box').open=false;}
globalThis.NXConnection=(()=>{
  let owner=null;
  const notice='Oberfläche konnte nicht sicher initialisiert werden. Browser neu laden und Serverdateien prüfen. Angenommene Aufträge bleiben erhalten.';
  function invalidate(clearField=true){connectionGeneration++;token='';cancelFileDownload(true);NXRequests.reset();owner=null;$('work').style.display='none';if(clearField)$('token').value='';}
  globalThis.NXClientBoot?.onFault(()=>{invalidate();message(notice,true);});
  async function connect(){const boot=globalThis.NXClientBoot;if(!boot?.canConnect()||owner){if(!boot)message(notice,true);return;}
    if(!boot.verify())return;const candidate=$('token').value;invalidate(false);const own={generation:connectionGeneration};owner=own;boot.connecting();message('Verbindung wird geprüft …');let stage='reset';
    try{clearSessionView();token=candidate;const request=NXRequests.begin('connection',()=>owner===own);
      stage='capabilities';const capabilities=await request.wait(api('capabilities'));
      stage='configure';kinds=capabilities.kind_labels;options($('kind'),kinds);options($('resource-kind'),{'':'Alle Typen',...kinds});NXResourceSearch.configure(capabilities.native_library_specs);NXNative.configure(capabilities);NXClone.configure(capabilities);NXEditorReferences.configure(capabilities);NXSaves.configure(capabilities);NXUpgrade.configure(capabilities);NXReleaseJobs.configure(capabilities);NXAssets.configure(capabilities);NXBackups.configure(capabilities);NXReviews.configure(capabilities);NXERP.configure(capabilities);NXLaunchProfiles.configure(capabilities);NXExportData.configure(capabilities);
      NXGraphicsBindings.configure(capabilities);NXExportFiles.configure(capabilities);NXERPVariants.configure(capabilities);NXSchemaImpact.configure(capabilities);NXFilePackages.configure(capabilities);
      stage='sources';const sources=await request.wait(api('sources'));stage='configure';NXFolders.configure(sources);options($('syslog'),{'':'Protokoll ausdrücklich wählen',...sources.logs});NXGeometry.configure(capabilities);
      stage='workspace';await request.wait(refresh());$('work').style.display='block';boot.connected();message('Verbunden. Keine Produktivanbindung.');
    }catch(error){if(owner!==own||own.generation!==connectionGeneration)return;
      token='';NXRequests.reset();$('work').style.display='none';
      if(stage==='reset'||stage==='configure'){boot.fail();message(notice,true);}
      else{boot.disconnected();message([401,403].includes(error.httpStatus)?'Verbindung abgelehnt. Lokalen Administrationsschlüssel prüfen; keine Sitzung freigegeben.':'Verbindung konnte nicht vollständig geprüft werden. Server/Verbindung prüfen und ausdrücklich erneut verbinden; keine Sitzung freigegeben.',true);}
    }finally{if(owner===own)owner=null;}}
  function disconnect(){invalidate();try{clearSessionView();showWorkspace('manage');globalThis.NXClientBoot?.disconnected();message('Abgemeldet.');}catch{globalThis.NXClientBoot?.fail();message('Abgemeldet. '+notice,true);}}
  return {connect,disconnect};
})();
$('connect').onclick=NXConnection.connect;$('disconnect').onclick=NXConnection.disconnect;
$('file-download-cancel').onclick=()=>cancelFileDownload();
if(!globalThis.NXClientBoot){$('connect').disabled=true;$('connection-state').textContent='Startprüfung der Oberfläche fehlt. Browser neu laden und Serverdateien prüfen; kein Login freigegeben.';}
$('refresh').onclick=action(refresh);
$('audit').onclick=action(()=>loadDetails('audit'));
$('library-read').onclick=action(NXNative.loadNative);
$('openapi').onclick=action(()=>loadDetails('openapi.json'));
$('new').onclick=()=>openEditor();
$('editor-new').onclick=()=>openEditor();
$('back-to-manage').onclick=()=>showWorkspace('manage','resource-query');
$('update-to-releases').onclick=()=>showWorkspace('manage','release-title');
['resource-query','resource-kind','resource-status','resource-library','resource-release','resource-export-status'].forEach(id=>$(id).oninput=renderResources);
$('resource-prev').onclick=()=>{resourcePage=Math.max(0,resourcePage-1);renderResources();};
$('resource-next').onclick=()=>{resourcePage++;renderResources();};
function editorBody(){return {kind:$('kind').value,name:$('name').value,cam_status:$('camstatus').value,cam:NXData.parseWire($('cam').value),erp:JSON.parse($('erp').value),asset_ids:JSON.parse($('assetids').value),...NXNative.resourceFields(),...NXFilePackages.fields()};}
function editorSignature(){const file=$('assetfile').files[0];return NXData.stringifyWire([editorBody(),file?[file.name,file.size,file.lastModified]:null]);}
$('save').onclick=()=>NXSaves.save();
$('upload').onclick=NXAssets.upload;
$('import').onclick=action(NXNative.importFolder);
Object.entries(tests).forEach(([key,text])=>{const label=document.createElement('label');const input=document.createElement('input');input.type='checkbox';input.id=key;label.append(input,document.createTextNode(text));$('checklist').append(label);});
reset();
NXUpgrade.reset();
NXIntegrity.reset();
showWorkspace(location.hash.slice(1));

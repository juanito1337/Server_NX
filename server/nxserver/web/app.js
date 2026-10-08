'use strict';
let token = '', resources = [], releases = [], active = {}, editing = null, reviewing = null;
const $ = id => document.getElementById(id);
const tests = {tools:'Kundenwerkzeuge geladen und Geometrie geprüft',machines:'Maschinen vollständig und ohne Ladefehler geprüft',posts:'Postprozessoren und NC-Ausgabe fachlich geprüft',simulation:'Maschinensimulation / Kollisionen geprüft',fixtures:'Spannmittel geprüft',inspection:'Prüfmittel / CMM geprüft oder Nichtanwendbarkeit begründet'};
let kinds = {tool:'Werkzeug',fixture:'Spannmittel',gauge:'Prüfmittel',machine:'Maschine'};
function message(text, error=false) { $('message').textContent = text; $('message').className = error ? 'error' : 'success'; }
function details(value) { $('details').textContent = JSON.stringify(value, null, 2); }
async function api(path, method='GET', body) {
  const response = await fetch('/api/v1/' + path, {method, headers:{Authorization:'Bearer ' + token,...(body === undefined ? {} : {'Content-Type':'application/json'})},...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
  return result;
}
function action(fn) { return async () => { try { await fn(); } catch(e) { message(e.message, true); } }; }
function button(text, fn) { const b=document.createElement('button');b.textContent=text;b.onclick=action(fn);return b; }
function cell(row, text) { const td=document.createElement('td');td.textContent=text;row.append(td);return td; }
function options(select, values) { const previous=select.value;select.replaceChildren();Object.entries(values).forEach(([key,value])=>{const o=document.createElement('option');o.value=key;o.textContent=key+' — '+value;select.append(o);});if([...select.options].some(o=>o.value===previous))select.value=previous; }
function reset() { editing=null;$('kind').value='tool';$('name').value='';$('camstatus').value='draft';$('cam').value='{}';$('erp').value='null';$('assetids').value='[]';$('editing').textContent='Neuer Datensatz';NXNative.reset(); }
async function refresh() {
  [resources,releases,active] = await Promise.all([api('resources'),api('releases'),api('active')]);
  $('summary').textContent=`${resources.length} Stammdatensätze · ${releases.length} Ordner-Versionen · Umschaltgeneration ${active.generation}`;
  $('active').textContent=active.folder ? 'Lokal aktiver NX-Ordner: '+(active.host_folder || active.folder) : 'Noch kein freigegebener Datenstand lokal aktiviert.';
  await NXGeometry.refresh();
  $('resources').replaceChildren();
  resources.forEach(r=>{const tr=document.createElement('tr');cell(tr,kinds[r.kind]);cell(tr,r.name);cell(tr,r.revision+' / '+r.cam_status);cell(tr,r.erp?.number || '—');const td=cell(tr,'');td.append(button('Bearbeiten',()=>{editing=r;$('kind').value=r.kind;$('name').value=r.name;$('camstatus').value=r.cam_status;$('cam').value=JSON.stringify(r.cam,null,2);$('erp').value=JSON.stringify(r.erp,null,2);$('assetids').value=JSON.stringify(r.asset_ids);$('editing').textContent='Bearbeite '+r.id+' · Revision '+r.revision;NXNative.edit(r);}),button('Historie',async()=>details(await api('resources/'+r.id+'/history'))));$('resources').append(tr);});
  $('releases').replaceChildren();
  options($('library-release'),Object.fromEntries(releases.filter(r=>r.status!=='failed' && r.status!=='building').map(r=>[r.id,r.name])));
  releases.forEach(r=>{const tr=document.createElement('tr');cell(tr,r.name+'\n'+r.id);cell(tr,r.status);cell(tr,r.nx_version+' / '+(r.validation?.file_count ?? 'noch ungeprüft'));const td=cell(tr,'');td.append(button('Details',async()=>details(await api('releases/'+r.id))),button('Prüfen',async()=>{details(await api('releases/'+r.id+'/validate','POST'));await refresh();}),button('NX-Werkzeugschema',async()=>{const data=await api('releases/'+r.id+'/tool-schemas');details(data);if(data.examples.length){const sample=structuredClone(data.examples[0]);sample.values.LIBRF='CUSTOM_'+Date.now();$('cam').value=JSON.stringify(sample,null,2);message('Werkzeugbeispiel geladen. Alle Maße und Referenzen vor einer Freigabe fachlich prüfen.');}}),button('Neuen Export ableiten',async()=>{const name=prompt('Bezeichnung für neuen Datenstand',r.name+' + Kundenwerkzeuge');if(name){details(await api('releases/'+r.id+'/derive','POST',{name}));await refresh();}}),button('Abnehmen',()=>{reviewing=r;$('review-box').hidden=false;$('review-name').textContent=r.name;Object.keys(tests).forEach(key=>$(key).checked=false);$('evidence').value='';}),button('Lokal aktivieren',async()=>{if(confirm('Nur den lokalen Verwaltungszeiger auf diesen freigegebenen Datenstand umschalten?')){details(await api('active','POST',{release_id:r.id,expected_generation:active.generation}));await refresh();}}));$('releases').append(tr);});
}
$('connect').onclick=action(async()=>{token=$('token').value;const capabilities=await api('capabilities');kinds=capabilities.kind_labels;options($('kind'),kinds);const sources=await api('sources');options($('source'),sources.folders);options($('syslog'),sources.logs);$('work').style.display='block';await refresh();await NXGeometry.refresh(capabilities);message('Verbunden. Keine Produktivanbindung.');});
$('disconnect').onclick=()=>{token='';$('token').value='';$('work').style.display='none';resources=[];releases=[];reset();NXGeometry.reset();details({});message('Abgemeldet.');};
$('refresh').onclick=action(refresh);
$('audit').onclick=action(async()=>details(await api('audit')));
$('library-read').onclick=action(NXNative.loadNative);
$('openapi').onclick=action(async()=>details(await api('openapi.json')));
$('new').onclick=reset;
$('save').onclick=action(async()=>{const body={kind:$('kind').value,name:$('name').value,cam_status:$('camstatus').value,cam:JSON.parse($('cam').value),erp:JSON.parse($('erp').value),asset_ids:JSON.parse($('assetids').value),...NXNative.resourceFields()};if(editing)body.expected_revision=editing.revision;details(await api(editing?'resources/'+editing.id:'resources',editing?'PUT':'POST',body));reset();await refresh();message('Neue Stammdatenrevision gespeichert. NX-Dateien wurden nicht verändert.');});
$('upload').onclick=action(async()=>{const file=$('assetfile').files[0];if(!file || file.size>20*1024*1024)throw new Error('Bitte eine Datei mit maximal 20 MB wählen.');const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});const asset=await api('assets','POST',{filename:file.name,content_base64:encoded});const ids=JSON.parse($('assetids').value);ids.push(asset.id);$('assetids').value=JSON.stringify(ids);NXNative.assetAdded();details(asset);message('Datei gespeichert. Stammdatensatz noch speichern und Geometrie erneut prüfen.');});
$('import').onclick=action(NXNative.importFolder);
Object.entries(tests).forEach(([key,text])=>{const label=document.createElement('label');const input=document.createElement('input');input.type='checkbox';input.id=key;label.append(input,document.createTextNode(text));$('checklist').append(label);});
$('scan').onclick=action(async()=>details(await api('logs/'+encodeURIComponent($('syslog').value)+'/scan','POST')));
$('approve').onclick=action(async()=>{if(!reviewing)throw new Error('Zuerst einen Datenstand wählen.');const confirmations=Object.fromEntries(Object.keys(tests).map(key=>[key,$(key).checked]));details(await api('releases/'+reviewing.id+'/review','POST',{tests:confirmations,evidence:$('evidence').value,syslog_source:$('syslog').value}));await refresh();message('Fachliche Abnahme dokumentiert. Noch keine Produktivumschaltung.');});
reset();

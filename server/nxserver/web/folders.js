'use strict';
/* Browses authenticated, administrator-approved SERVER paths, not browser uploads. */
globalThis.NXFolders = (() => {
  const el = id => document.getElementById(id);
  let roots = {}, purpose = 'source', current = null, leaf = '', loading = false;
  const join = (parent, name) => parent.replace(/[\\/]+$/, '') + (parent.includes('\\') ? '\\' : '/') + name;
  const basename = path => path.split(/[\\/]/).filter(Boolean).pop() || '';
  const dirname = path => path.replace(/[\\/][^\\/]+[\\/]*$/, '');
  const invalidate = () => NXNative.invalidateImport();
  const run = fn => () => action(fn)();
  function close(){NXRequests.invalidate('folders.');loading=false;current=null;el('folder-dialog').close();}
  function suggestedName() { return 'nx' + el('nxversion').value + '_' + crypto.randomUUID().replaceAll('-', '').slice(0, 8); }
  function resetPath(kind) {
    const name = kind === 'target' ? (basename(el('target-path').value) || suggestedName()) : '';
    const root = roots[kind]?.[el(kind).value] || '';
    el(kind + '-path').value = name ? join(root, name) : root;
    invalidate();
  }
  function configure(settings) {
    roots = {source: settings.source_roots, target: settings.target_roots};
    options(el('source'), roots.source);options(el('target'), roots.target);
    resetPath('source');resetPath('target');
  }
  async function browse(path) {
    if(loading)return;
    const kind=purpose,root=el(kind).value,request=NXRequests.begin('folders.browse',()=>purpose===kind&&root===el(kind).value&&el('folder-dialog').open);
    loading=true;current=null;el('folder-select').disabled=true;el('folder-up').disabled=true;el('folder-home').disabled=true;
    el('folder-entries').replaceChildren();el('folder-state').textContent='Ordner werden gelesen …';
    try {
      const params=new URLSearchParams({purpose:kind,root});if(path)params.set('path',path);
      current=await request.wait(api('folders?' + params));
      el('folder-path').textContent=current.path;
      current.children.forEach(child=>{const li=document.createElement('li');li.append(button(child.name,()=>browse(child.path)));el('folder-entries').append(li);});
      el('folder-select').disabled=!current.can_select;
      el('folder-up').disabled=!current.parent;el('folder-home').disabled=false;
      el('folder-state').textContent=current.reason || (current.truncated?'Die ersten 500 Ordner werden angezeigt. Den gewünschten Pfad können Sie auch direkt eingeben.':current.children.length?'Unterordner öffnen oder diesen Ordner wählen.':'Keine auswählbaren Unterordner vorhanden.');
    } catch(e) { if(request.current()){el('folder-state').textContent=e.message;el('folder-home').disabled=false;} }
    finally { if(request.current())loading=false; }
  }
  async function open(kind) {
    close();
    purpose=kind;leaf=basename(el('target-path').value)||suggestedName();
    el('folder-title').textContent=kind==='source'?'Quellordner auswählen':'Übergeordneten Zielordner auswählen';
    el('folder-select').textContent=kind==='source'?'Diesen Quellordner wählen':'Als übergeordneten Zielordner wählen';
    el('folder-dialog').showModal();
    await browse(kind==='source'?el('source-path').value:dirname(el('target-path').value));
  }
  ['source','target'].forEach(kind=>{
    el(kind).addEventListener('change',()=>resetPath(kind));
    el(kind+'-path').addEventListener('input',invalidate);
    el(kind+'-browse').onclick=run(()=>open(kind));
  });
  el('folder-up').onclick=run(()=>browse(current?.parent));
  el('folder-home').onclick=run(()=>browse());
  el('folder-close').onclick=close;
  el('folder-dialog').addEventListener('close',()=>{if(!el('folder-dialog').open){NXRequests.invalidate('folders.');loading=false;current=null;}});
  el('folder-select').onclick=()=>{if(!current?.can_select)return;el(purpose+'-path').value=purpose==='source'?current.path:join(current.path,leaf);invalidate();close();};
  return {configure,fields:()=>({source_path:el('source-path').value,target:el('target').value,target_path:el('target-path').value}),
          reset:()=>{close();roots={};['source','target'].forEach(id=>{el(id).replaceChildren();el(id+'-path').value='';});invalidate();}};
})();

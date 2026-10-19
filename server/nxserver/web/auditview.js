'use strict';
globalThis.NXAudit=(()=>{
  const el=id=>document.getElementById('auditview-'+id),size=20;
  const nat=value=>Number.isSafeInteger(value)&&value>=0,hex=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  let generation=0,report=null,busy=false,detailOwner=null;
  const fail=()=>{throw new Error('Änderungsprotokoll passt nicht zur angefragten Ansicht. Übersicht neu laden.');};
  const same=(left,right)=>left?.sequence===right?.sequence&&left?.hash===right?.hash;
  function head(value){if(!NXData.record(value)||!nat(value.sequence)||!hex(value.hash)||value.sequence===0&&value.hash!=='0'.repeat(64))fail();}
  function flags(value){if(value.contract_version!==1||value.sqlite_read_only!==true||value.sqlite_read_transaction!==true||value.audit_chain_verified!==false||value.files_verified!==false||value.signed!==false)fail();head(value.head);}
  function item(value){if(!NXData.record(value)||!nat(value.sequence)||value.sequence<1||!nat(value.payload_bytes)||!hex(value.hash)||!hex(value.previous_hash)||!['actor','action','created_at'].every(key=>typeof value[key]==='string'&&value[key].length>0))fail();}
  function controls(){el('read').disabled=busy;el('prev').disabled=busy||!report||report.offset===0;el('next').disabled=busy||!report||report.offset+report.items.length>=report.filtered;}
  function erase(){report=null;detailOwner=null;el('items').replaceChildren();el('page').textContent='';el('detail').hidden=true;el('payload').textContent='';el('detail-state').textContent='';controls();}
  function invalidate(){generation++;NXRequests.invalidate('auditview.');busy=false;erase();}
  function reset(){invalidate();el('box').hidden=true;el('query').value='';el('state').textContent='';}
  function row(value,owner){const tr=document.createElement('tr');for(const text of [String(value.sequence),value.created_at,value.actor,value.action,String(value.payload_bytes)+' Bytes']){const td=document.createElement('td');td.textContent=text;tr.append(td);}
    const td=document.createElement('td'),button=document.createElement('button');button.className='secondary';button.textContent='Eintrag lesen';button.onclick=()=>action(()=>readEntry(value,owner,button))();td.append(button);tr.append(td);return tr;}
  function params(value){return {head_sequence:String(value.sequence),head_hash:value.hash};}
  async function read(offset=0,keepHead=false){
    const previous=report,query=el('query').value,current=++generation;
    NXRequests.invalidate('auditview.');busy=true;erase();controls();
    const selected=keepHead?previous?.head:null,own=NXRequests.begin('auditview.list',()=>generation===current&&!el('box').hidden&&el('query').value===query);
    el('state').textContent='Änderungsprotokoll wird gelesen …';
    try{
      if((offset||keepHead)&&!selected)fail();
      const input={offset:String(offset),limit:String(size),query,...(selected?params(selected):{})};
      const value=await own.wait(api('audit-summary?'+new URLSearchParams(input)));
      if(!NXData.record(value))fail();flags(value);
      if(value.offset!==offset||value.limit!==size||value.query!==query||!nat(value.total)||!nat(value.filtered)||value.filtered>value.total||!Array.isArray(value.items)||value.items.length!==Math.min(size,Math.max(0,value.filtered-offset))||selected&&!same(selected,value.head))fail();
      let last=value.head.sequence+1;for(const valueItem of value.items){item(valueItem);if(valueItem.sequence>=last||valueItem.sequence>value.head.sequence)fail();last=valueItem.sequence;}
      const fragment=document.createDocumentFragment();for(const valueItem of value.items)fragment.append(row(valueItem,value));
      report=value;el('items').replaceChildren(fragment);
      el('state').textContent=value.filtered+' passende von '+value.total+' Einträgen · Lesestand '+value.head.sequence+' · keine vollständige Integritätsprüfung.';
      el('page').textContent=value.items.length?(offset+1)+'–'+(offset+value.items.length)+' von '+value.filtered:'Keine passenden Einträge.';
    }catch(error){if(own.current()&&!NXRequests.isStale(error)){erase();el('state').textContent=error.message;}}
    finally{if(own.current()){busy=false;controls();}}
  }
  async function readEntry(value,owner,button){
    if(report!==owner||busy||!button.isConnected)return;
    const own=NXRequests.begin('auditview.detail',()=>report===owner&&!el('box').hidden),selected={};detailOwner=selected;
    el('detail').hidden=true;el('payload').textContent='';el('detail-state').textContent='Eintrag '+value.sequence+' wird gelesen …';
    try{
      const response=await own.wait(api('audit-entries/'+value.sequence+'?'+new URLSearchParams({expected_hash:value.hash,...params(owner.head)})));
      if(!NXData.record(response))fail();flags(response);item(response.entry);
      if(!same(response.head,owner.head)||response.entry_hash_verified!==true||Object.keys(value).some(key=>response.entry[key]!==value[key])||typeof response.payload!=='string'||new TextEncoder().encode(response.payload).byteLength!==value.payload_bytes)fail();
      const hash=new jsSHA('SHA-256','TEXT');hash.update(JSON.stringify([value.previous_hash,value.actor,value.action,response.payload,value.created_at]));if(hash.getHash('HEX')!==value.hash)fail();
      el('payload').textContent=response.payload;el('detail').hidden=false;
      el('detail-state').textContent='Eintrag '+value.sequence+' · Einzelhash bestätigt: '+value.hash+' · keine Ketten-, Datei- oder Signaturprüfung.';
    }catch(error){if(own.current()&&detailOwner===selected&&!NXRequests.isStale(error)){el('payload').textContent='';el('detail').hidden=true;el('detail-state').textContent=error.message;}}
  }
  function open(){showWorkspace('manage');reset();el('box').hidden=false;el('title').focus();return read();}
  el('open').onclick=action(open);el('read').onclick=action(()=>read());el('close').onclick=reset;
  el('prev').onclick=action(()=>read(Math.max(0,report.offset-size),true));el('next').onclick=action(()=>read(report.offset+size,true));
  el('query').oninput=()=>{invalidate();el('state').textContent='Suchtext geändert. Übersicht ausdrücklich neu laden.';};
  return {reset,workspaceChanged:reset};
})();

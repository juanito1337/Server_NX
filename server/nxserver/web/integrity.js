'use strict';
globalThis.NXIntegrity=(()=>{
  const el=id=>document.getElementById(id);
  const labels={passed:'Nachweise konsistent',partial:'Nur teilweise nachweisbar',failed:'Abweichungen gefunden',stale:'Während Prüfung geändert'};
  let generation=0,timer=null,busy=false,report=null;
  function reset(){generation++;clearTimeout(timer);timer=null;busy=false;report=null;el('integrity-start').disabled=false;el('integrity-checkpoint').disabled=true;
    el('integrity-anchor').value='';el('integrity-issues').replaceChildren();el('integrity-jobs').replaceChildren();el('integrity-trust').textContent='';el('integrity-state').textContent='Noch keine Integritätsprüfung in dieser Sitzung.';}
  function render(value){report=value;el('integrity-checkpoint').disabled=!value.checkpoint_available;
    el('integrity-state').textContent=`${labels[value.status]||value.status} · Prüfstand ${new Date(value.finished_at).toLocaleString('de')} · ${value.audit.entries} Audit-Einträge · ${value.metadata.bound_rows} gebundene Datenzeilen · ${value.files.files} geprüfte Dateien · ${value.error_count} Fehler / ${value.warning_count} Hinweise.`;
    el('integrity-trust').textContent=(value.trust.external_anchor_checked?'Externer Vergleichsanker geprüft.':'Nur interne Nachweise geprüft; kein externer Vergleichsanker.')+' Hash-Konsistenz ist keine Signatur oder historische Echtheitsgarantie.'+(value.files.included?'':' Dateiprüfung war nicht ausgewählt.');
    el('integrity-issues').replaceChildren();[...value.errors,...value.warnings].forEach(item=>{const row=document.createElement('tr');cell(row,item.code);cell(row,item.entity);cell(row,item.detail);el('integrity-issues').append(row);});if(value.issues_truncated){const row=document.createElement('tr');cell(row,'Weitere Befunde');cell(row,'');cell(row,'Detailanzeige begrenzt; Gesamtzähler enthält alle gefundenen Befunde.');el('integrity-issues').append(row);}details(value);
  }
  async function refresh(alive=()=>true){const current=generation,request=NXRequests.begin('integrity.list',()=>current===generation&&alive()),jobs=await request.wait(api('integrity'));el('integrity-jobs').replaceChildren();
    jobs.forEach(job=>{const row=document.createElement('tr');cell(row,job.id.slice(0,8));cell(row,job.status+' · '+job.message);cell(row,'').append(button(job.status==='running'?'Weiter beobachten':'Ergebnis ansehen',()=>{generation++;clearTimeout(timer);return watch(job.id,generation);}));el('integrity-jobs').append(row);});}
  async function watch(identifier,current,request=null){request??=NXRequests.begin('integrity.watch',()=>current===generation);if(!request.current())return;clearTimeout(timer);
    try{const job=await request.wait(api('integrity/'+identifier));
      if(job.status==='running'){busy=true;report=null;el('integrity-checkpoint').disabled=true;el('integrity-start').disabled=true;el('integrity-state').textContent=job.phase+' · '+job.message;timer=setTimeout(()=>watch(identifier,current,request),1000);return;}
      busy=false;el('integrity-start').disabled=false;if(job.report)render(job.report);else{report=null;el('integrity-checkpoint').disabled=true;el('integrity-state').textContent=job.message;}await request.wait(NXRequests.settle(refresh()));
    }catch(error){if(!request.current()||NXRequests.isStale(error))return;el('integrity-state').textContent='Abfrage fehlgeschlagen: '+error.message+' Kein automatischer Neustart der Prüfung.';busy=false;el('integrity-start').disabled=false;report=null;el('integrity-checkpoint').disabled=true;}
  }
  el('integrity-open').onclick=()=>showWorkspace('manage','integrity-title');
  el('integrity-start').onclick=()=>action(async()=>{if(busy)return;const current=generation;busy=true;report=null;el('integrity-start').disabled=true;el('integrity-checkpoint').disabled=true;el('integrity-state').textContent='Prüfung wird vorbereitet …';
    try{const file=el('integrity-anchor').files[0];if(file&&file.size>8*1024*1024)throw new Error('Vergleichsanker ist größer als 8 MiB.');const anchor=file?JSON.parse((await file.text()).replace(/^\uFEFF/,'')):null;if(current!==generation)return;
      const job=await api('integrity','POST',{include_files:el('integrity-files').checked,anchor});if(current===generation)await watch(job.id,current);
    }finally{if(current===generation&&!timer){busy=false;el('integrity-start').disabled=false;}}
  })();
  el('integrity-jobs-refresh').onclick=()=>action(refresh)();
  el('integrity-checkpoint').onclick=()=>{if(!report?.checkpoint_available)return;if(!confirm('Dieses Prüfergebnis bewusst als Ausgangsstand sichern? Das beweist keine frühere Echtheit. Datei separat und zugriffsgeschützt aufbewahren; kein Datenbackup und keine NX-Freigabe.'))return;
    const blob=new Blob([JSON.stringify(report.checkpoint,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='NX-Pruefstand-'+report.audit.head.sequence+'.json';link.click();URL.revokeObjectURL(url);};
  return {reset,refresh};
})();

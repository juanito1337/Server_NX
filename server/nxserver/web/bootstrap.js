'use strict';
/* Load before modules. Diagnostics contain no exceptions, tokens or CAM data. */
globalThis.NXClientBoot=(()=>{
  const contracts={NXData:['record','bindWire','parseWire','numberLiteral','stringifyWire','cloneWire'],NXResourceSearch:['configure','describe','choices','label'],NXRequests:['begin','reset'],NXDownload:['receive'],
    NXNative:['reset','configure','hasModelPackage'],NXClone:['configure','clearConnection'],NXEditorReferences:['configure','clearConnection'],
    NXFolders:['reset','configure'],NXGeometry:['configure','reset','connectionChanged'],NXResults:['reset','catalogChanged'],
    NXExports:['reset','syncCatalog','context'],NXExportData:['configure','open','reset','clearConnection'],NXExportFiles:['configure','open','reset','clearConnection'],NXAssets:['reset','upload','readMetadata','identifiers'],NXUpgrade:['reset','configure'],NXUpgradePlan:['verify','hash'],
    NXSchemas:['clearConnection'],NXSchemaImpact:['configure','sync','clearConnection','workspaceChanged'],NXIntegrity:['refresh','reset'],NXBackups:['reset','configure'],
    NXFilePackages:['configure','clearConnection'],NXGraphicsBindings:['configure','reset','sourceChanged','controls','clearConnection'],NXLaunchProfiles:['configure','sync','clearConnection'],NXSaves:['save','connectionChanged'],NXReleaseJobs:['reset','configure'],
    NXReviews:['reset','configure'],NXHistory:['reset','open','catalogChanged'],NXAudit:['reset','workspaceChanged'],NXERP:['reset','configure','catalogChanged','setModel','clearConnection'],NXERPVariants:['reset','configure','catalogChanged','refreshStatus','inspectResource','clearConnection'],NXConnection:['connect','disconnect']};
  const notice='Oberfläche konnte nicht sicher initialisiert werden. Browser neu laden und Serverdateien prüfen. Angenommene Aufträge werden nicht gestoppt oder neu gestartet; ungespeicherte Eingaben vor Neuladen beachten.';
  let ready=false,failed=false,phase='loading',faultHandler=null;
  function render(){const state=document.getElementById('connection-state'),connect=document.getElementById('connect');
    if(connect){connect.disabled=!ready||failed||phase==='connecting';connect.setAttribute('aria-busy',String(phase==='connecting'));}
    if(state){state.textContent=failed?notice:phase==='loading'?'Oberfläche wird geladen; noch keine Serververbindung.':phase==='connecting'?'Verbindung wird geprüft; konsistenter Bestand wird geladen …':phase==='connected'?'Verbunden · lokaler Pilot, keine Produktivanbindung.':'Oberfläche bereit. Sitzungsschlüssel eingeben und ausdrücklich verbinden.';state.className=failed?'error':'muted';}}
  function fail(){if(!failed){failed=true;ready=false;phase='failed';try{faultHandler?.();}catch{/* No raw failure diagnostics. */}}render();}
  function verify(){const valid=typeof globalThis.jsSHA==='function'&&Object.entries(contracts).every(([name,methods])=>methods.every(method=>typeof globalThis[name]?.[method]==='function'));
    if(!valid){fail();return false;}return true;}
  window.addEventListener('error',event=>{if(event.target?.tagName==='SCRIPT'||event instanceof ErrorEvent)fail();},true);
  window.addEventListener('unhandledrejection',()=>fail());
  window.addEventListener('DOMContentLoaded',()=>{if(!failed&&verify()){ready=true;phase='idle';}render();
    const logout=document.getElementById('disconnect');if(logout&&typeof logout.onclick!=='function')logout.onclick=()=>{const field=document.getElementById('token');if(field)field.value='';render();};});
  return {notice,canConnect:()=>ready&&!failed&&phase!=='connecting',verify,
    onFault:handler=>{faultHandler=handler;if(failed){try{handler();}catch{}}},fail,
    connecting:()=>{if(!failed)phase='connecting';render();},connected:()=>{if(!failed)phase='connected';render();},disconnected:()=>{if(!failed)phase='idle';render();},
    status:()=>({contract_version:1,ready,failed,phase})};
})();

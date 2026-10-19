'use strict';
/* SHA the received bytes before publishing a download; never buffer a large ZIP. */
globalThis.NXDownload=(()=>{
  const memoryLimit=64*1024*1024;
  function metadata(expected){
    if(!expected||!Number.isSafeInteger(expected.size)||expected.size<1||typeof expected.sha256!=='string'||!(/^[a-f0-9]{64}$/).test(expected.sha256))throw new Error('Ungültiger Größen-/SHA-Beleg für den Download.');
    return expected;
  }
  async function receive(response,expected,{writer=null,alive=()=>true,progress=()=>{}}={}){
    metadata(expected);if(!writer&&expected.size>memoryLimit)throw new Error('Großes Archiv benötigt die Dateispeicher-API; kein unbeschränkter Speicherdownload.');
    if(typeof jsSHA!=='function')throw new Error('Lokale Streaming-SHA-Komponente fehlt; Download wird nicht gespeichert.');
    if(!response.body||typeof response.body.getReader!=='function')throw new Error('Downloadantwort enthält keinen lesbaren Datenstrom.');
    const reader=response.body.getReader(),hash=new jsSHA('SHA-256','UINT8ARRAY'),chunks=[];let size=0,lastProgress=0;
    const check=()=>{if(!alive())throw new Error('Sitzung oder Download geändert; nicht als geprüft speichern.');};
    try{
      check();
      for(;;){const {done,value}=await reader.read();check();if(done)break;
        if(!(value instanceof Uint8Array))throw new Error('Download enthält keinen unterstützten Binärblock.');
        size+=value.byteLength;if(size>expected.size||!Number.isSafeInteger(size)||(!writer&&size>memoryLimit))throw new Error('Download überschreitet die erwartete Größe.');
        hash.update(value);if(writer){await writer.write(value);check();}else chunks.push(value);
        if(size-lastProgress>=4*1024*1024){progress(size,expected.size);lastProgress=size;}
      }
      if(size!==expected.size)throw new Error('Archivdownload ist unvollständig.');
      const actual=hash.getHash('HEX');if(actual!==expected.sha256)throw new Error('Empfangene Download-SHA entspricht nicht dem Prüfsummenbeleg. Nicht gespeichert.');
      check();progress(size,expected.size);
      return {size,sha256:actual,blob:writer?null:new Blob(chunks,{type:'application/zip'}),verified:true};
    }catch(error){await reader.cancel().catch(()=>{});throw error;}
    finally{reader.releaseLock?.();}
  }
  return {receive,metadata,memoryLimit};
})();

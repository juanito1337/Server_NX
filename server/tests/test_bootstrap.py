"""Public startup assets and actual no-secret bootstrap lifecycle."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nxserver.api import create_app
from nxserver.store import Store

WEB=Path(__file__).parents[1]/'nxserver/web'


def test_bootstrap_is_local_served_exactly_and_login_is_initially_disabled(tmp_path):
    with TestClient(create_app(Store(tmp_path/'managed'),'isolated-bootstrap-token-'+'b'*40)) as client:
        html=client.get('/').text;response=client.get('/bootstrap.js')
        assert response.status_code==200 and response.content==(WEB/'bootstrap.js').read_bytes()
        assert response.headers['cache-control']=='no-store' and "script-src 'self'" in response.headers['content-security-policy']
        assert html.index('src="/bootstrap.js"')<html.index('src="/native.js"')
        assert '<button id="connect" disabled>' in html and 'id="connection-state" role="status"' in html
        assert 'JavaScript aktivieren' in html
        assert client.get('/api/v1/workspace').status_code==401
    assert 'COPY nxserver ./nxserver' in (WEB.parents[1]/'Dockerfile').read_text(encoding='utf-8')


def test_actual_bootstrap_contract_and_faults_do_not_inspect_private_error_data():
    candidates=[shutil.which('node'),r'C:\Program Files\nodejs\node.exe','/mnt/c/Program Files/nodejs/node.exe']
    node=next((value for value in candidates if value and Path(value).is_file()),None)
    if not node:pytest.skip('JavaScript runtime unavailable.')
    source=(WEB/'bootstrap.js').read_text(encoding='utf-8')
    script=r'''
    const assert=require('node:assert/strict');
    const names=['NXData','NXResourceSearch','NXRequests','NXDownload','NXNative','NXClone','NXEditorReferences','NXFolders','NXGeometry','NXResults','NXExports','NXExportData','NXExportFiles','NXAssets','NXGraphicsBindings','NXUpgrade','NXUpgradePlan','NXSchemas','NXSchemaImpact','NXIntegrity','NXBackups','NXFilePackages','NXLaunchProfiles','NXSaves','NXReleaseJobs','NXReviews','NXHistory','NXAudit','NXERP','NXERPVariants','NXConnection'];
    const methods=['record','bindWire','parseWire','numberLiteral','stringifyWire','cloneWire','describe','choices','label','workspaceChanged','begin','reset','receive','configure','clearConnection','connectionChanged','syncCatalog','sync','context','upload','readMetadata','identifiers','hasModelPackage','sourceChanged','controls','refresh','save','connect','disconnect','open','catalogChanged','refreshStatus','inspectResource','setModel','verify','hash'];
    const elements={connect:{disabled:true,setAttribute(key,value){this[key]=value;}},'connection-state':{textContent:'',className:''},disconnect:{onclick:null},token:{value:'synthetic-private-key'}};
    function initialize(){
      globalThis.document={getElementById(id){return elements[id]||null;}};
      const listeners=new Map();globalThis.window={addEventListener(name,handler){listeners.set(name,handler);}};
      globalThis.ErrorEvent=class ErrorEvent {};
      for(const name of names)globalThis[name]=Object.fromEntries(methods.map(method=>[method,()=>{}]));
      globalThis.jsSHA=()=>{};
      new Function(SOURCE)();return listeners;
    }
    let listeners=initialize();assert.equal(NXClientBoot.canConnect(),false);
    listeners.get('DOMContentLoaded')();assert.equal(NXClientBoot.canConnect(),true);assert.equal(elements.connect.disabled,false);
    NXClientBoot.connecting();assert.equal(NXClientBoot.canConnect(),false);assert.equal(elements.connect['aria-busy'],'true');
    NXClientBoot.connected();assert.equal(NXClientBoot.status().phase,'connected');
    NXClientBoot.disconnected();assert.equal(NXClientBoot.status().phase,'idle');
    let fault=0;NXClientBoot.onFault(()=>{fault++;});
    const error=new ErrorEvent();Object.defineProperty(error,'message',{get(){throw new Error('Must not inspect private exception text');}});
    listeners.get('error')(error);assert.equal(fault,1);assert.equal(NXClientBoot.status().failed,true);assert.equal(elements.connect.disabled,true);
    assert.equal(JSON.stringify(NXClientBoot.status()).includes('private'),false);
    assert.equal(elements['connection-state'].textContent.includes('synthetic-private-key'),false);
    NXClientBoot.fail();assert.equal(fault,1);
    NXClientBoot.connected();assert.equal(NXClientBoot.status().phase,'failed');assert.equal(NXClientBoot.canConnect(),false);
    listeners=initialize();delete NXNative.reset;listeners.get('DOMContentLoaded')();assert.equal(NXClientBoot.canConnect(),false);
    for(const method of ['readMetadata','identifiers']){listeners=initialize();delete NXAssets[method];listeners.get('DOMContentLoaded')();assert.equal(NXClientBoot.canConnect(),false);}
    for(const method of ['bindWire','parseWire','numberLiteral','stringifyWire','cloneWire']){listeners=initialize();delete NXData[method];listeners.get('DOMContentLoaded')();assert.equal(NXClientBoot.canConnect(),false);}
    listeners=initialize();delete NXERPVariants.inspectResource;listeners.get('DOMContentLoaded')();assert.equal(NXClientBoot.canConnect(),false);
    for(const method of ['describe','choices','label']){listeners=initialize();delete NXResourceSearch[method];listeners.get('DOMContentLoaded')();assert.equal(NXClientBoot.canConnect(),false);}
    listeners=initialize();listeners.get('DOMContentLoaded')();
    const rejection={};Object.defineProperty(rejection,'reason',{get(){throw new Error('Must not read rejection payload');}});
    listeners.get('unhandledrejection')(rejection);assert.equal(NXClientBoot.status().failed,true);
    listeners=initialize();listeners.get('error')({target:{tagName:'SCRIPT'}});listeners.get('DOMContentLoaded')();assert.equal(NXClientBoot.canConnect(),false);
    console.log('Actual bootstrap contract, redaction and lifecycle assertions passed.');
    '''.replace('SOURCE',json.dumps(source))
    result=subprocess.run([node,'-e',script],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Actual bootstrap contract' in result.stdout

"""Start a separate loopback development instance, never a test-run database."""
import argparse
import json
import os
from pathlib import Path
import sys

SERVER=Path(__file__).absolute().parents[1]
sys.path.insert(0,str(SERVER))
from tools.nxlab.guard import local,writable_lab_root


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default=str(SERVER/'data/development'))
    parser.add_argument('--port',type=int,default=8765)
    options=parser.parse_args()
    if not 1<=options.port<=65535:parser.error('Port must be 1-65535.')
    root=writable_lab_root(options.root,SERVER,development=True);root.mkdir(parents=True,exist_ok=True)
    for name in ('inputs','exports','temp'):
        local(root/name).mkdir(exist_ok=True)
    settings=local(root/'settings.local.json')
    expected={'data_dir':str(root/'managed'),'sources':{},'logs':{},'standards':{},
              'source_roots':{'local-inputs':str(root/'inputs')},
              'target_roots':{'local-exports':str(root/'exports')}}
    if settings.exists():
        if json.loads(settings.read_text(encoding='utf-8'))!=expected:
            raise RuntimeError('Development settings differ from the isolated profile; review instead of overwriting.')
    else:settings.write_text(json.dumps(expected,indent=2),encoding='utf-8')
    # os.execve replaces only this launcher; there is no background service and
    # the session key is shown solely in the user's interactive server console.
    environment={key:value for key,value in os.environ.items()
        if key.upper() in ('SYSTEMROOT','WINDIR','COMSPEC')}
    environment.update(TEMP=str(root/'temp'),TMP=str(root/'temp'),
        PATH=str(local(sys.executable).parent)+os.pathsep+r'C:\Windows\System32',PYTHONIOENCODING='utf-8')
    os.chdir(SERVER)
    os.execve(str(local(sys.executable)),[sys.executable,'-m','nxserver','--settings',str(settings),
              '--port',str(options.port),'--host','127.0.0.1'],environment)


if __name__=='__main__':main()

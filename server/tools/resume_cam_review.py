"""Resume the local HMI for a qualified lab case; never starts or approves NX."""
import argparse
import json
from pathlib import Path
import sys
import time
import uuid

SERVER=Path(__file__).absolute().parents[1]
sys.path.insert(0,str(SERVER))
from tools.run_local_pipeline import evidence,source_state
from tools.nxlab.cam_review import load,check_server
from tools.nxlab.guard import Blocked,local,writable_lab_root
from tools.nxlab.runtime import Server


def main(arguments=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--handoff',required=True)
    parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--root',default=str(SERVER/'test-results/local'))
    options=parser.parse_args(arguments)
    root=writable_lab_root(options.root,SERVER);root.mkdir(parents=True,exist_ok=True)
    directory=local(root/('cam-review-'+uuid.uuid4().hex[:12]));directory.mkdir(exist_ok=False)
    result={'schema_version':1,'stage':'cam-review','status':'incomplete','cases':[],
        'complete_product_acceptance':False,'automatic_approval':False,'production_access':False}
    started=time.monotonic()
    try:
        result['source']=source_state()
        if not options.check_only and not sys.stdin.isatty():raise Blocked('Manual HMI review requires an interactive console; use --check-only for context verification.')
        value,case=load(options.handoff,result['source'])
        # Only this generated case database is reopened, with an empty source
        # registry. Original settings and external aliases are never imported.
        with Server(directory/'server',data=local(case/'server/data')) as server:
            result['cases'].append(check_server(server,value))
            if not options.check_only:
                print('Lokale HMI: '+server.base,flush=True)
                print('Nur fuer diese interaktive Sitzung: '+server.token,flush=True)
                print('NX wird nicht gestartet. Native CAM-Pruefung und frisches Wiederladen bleiben erforderlich.',flush=True)
                input('Review beenden und eigenen Server stoppen: Eingabetaste druecken. ')
        if source_state()!=result['source']:raise RuntimeError('Sources changed during manual review.')
        result['status']='awaiting_cam'
        result['cases'].append({'id':'native-cam-acceptance','status':'awaiting_cam',
            'message':'A valid review context does not confirm the accompanied native CAM/export/reload workflow.'})
    except Blocked as error:
        result['status']='blocked';result['cases'].append({'id':'review-prerequisites','status':'blocked','message':str(error)})
    except Exception as error:
        result['status']='failed';result['cases'].append({'id':'review-context','status':'failed','message':str(error)})
    finally:
        result['seconds']=round(time.monotonic()-started,3);evidence(directory,result)
        print(json.dumps({'status':result['status'],'report':str(directory),'complete_product_acceptance':False}))
    return 2 if result['status'] in ('awaiting_cam','blocked') else 1


if __name__=='__main__':sys.exit(main())

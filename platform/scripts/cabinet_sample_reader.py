"""Use per-call RCAR sessions for traced previews and the upstream CLI for other QA calls."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from cabinet_sample_session import run
from cabinet_trustee_audit import session_event


def read(config, args):
    if len(args) != 7 or args[4:6] != ['get-resource','--path']:
        return 2
    preview = os.environ.get('CABINET_PREVIEW_ID')
    if not preview:
        return subprocess.run(config['reader']+['get-resource','--path',args[-1]],stderr=subprocess.DEVNULL).returncode
    call = os.environ['CABINET_KEY_CALL_ID']
    operation = os.environ['CABINET_KEY_OPERATION']
    if not all(re.fullmatch('[0-9a-f]{32}',v) for v in (preview,call)):
        return 2
    key, receipt = run(config,preview,call,operation,args[-1])
    archive = Path(config['archive'])
    event = receipt
    if receipt['decision'] != 'unavailable':
        for attempt in range(10):
            proc = subprocess.run(config['docker']+['logs','--timestamps','--since','2m',config['kbs']],
                                  stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=True,timeout=5)
            lines = [line for line in proc.stdout.decode().splitlines() if ' INFO actix_web::middleware::logger:' in line
                     and f'?preview={preview}&call={call}&' in line]
            if len(lines)>=3:
                break
            time.sleep(.05)
        evidence = dict(receipt,source_container=config['source'],path=args[-1],operation=operation,
                        preview_id=preview,call_id=call,records=lines)
        event, raw = session_event(evidence)
        with (archive/event['raw_file']).open('xb') as out:
            out.write(raw)
    entry = dict(preview_id=preview,call_id=call,event=event,operation=operation)
    with (archive/(preview+'.jsonl')).open('a') as out:
        out.write(json.dumps(entry)+'\n')
    if key is None:
        return 1
    sys.stdout.buffer.write(base64.b64encode(key))
    return 0


if __name__ == '__main__':
    sys.exit(read(json.loads(Path(sys.argv[1]).read_text()),sys.argv[2:]))

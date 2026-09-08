"""Content-addressed phone attachments retained beside the authoritative ledger."""
import base64
import hashlib
import json
import mimetypes
import urllib.parse
from pathlib import Path
import re

import private_state
import run_board

MAX_BYTES=5*1024*1024


def root():
    return run_board.LEDGER.parent/'office-uploads'


def upload(body):
    name=body.get('name','')
    if not isinstance(name,str) or not name or len(name)>240 or Path(name).name!=name or '\\' in name:
        raise ValueError('Choose a file with a simple filename')
    encoded=body.get('base64')
    if not isinstance(encoded,str) or len(encoded)>4*((MAX_BYTES+2)//3):raise ValueError('Each attachment is limited to 5 MiB')
    try:raw=base64.b64decode(encoded,validate=True)
    except ValueError:raise ValueError('Attachment encoding is invalid') from None
    if len(raw)>MAX_BYTES:raise ValueError('Each attachment is limited to 5 MiB')
    revision=hashlib.sha256(raw).hexdigest()
    identifier=hashlib.sha256(name.encode()+b'\0'+raw).hexdigest()
    record={'id':'upload:'+identifier,'revision':revision,'name':name,'bytes':len(raw),'base64':encoded}
    directory=private_state.ensure_dir(root(),anchor=run_board.LEDGER.parent)
    private_state.atomic_write_text(directory/(identifier+'.json'),json.dumps(record))
    return {key:value for key,value in record.items() if key!='base64'}


def resolve(reference):
    if not isinstance(reference,dict) or set(reference)!={'id','revision'}:raise ValueError('Attachment needs an exact receipt')
    identifier=reference['id']
    if not isinstance(identifier,str) or not re.fullmatch(r'upload:[0-9a-f]{64}',identifier):raise ValueError('Invalid attachment receipt')
    path=root()/(identifier[7:]+'.json')
    if any(part.is_symlink() for part in (path,*path.parents)):raise PermissionError('Linked attachment storage is unavailable')
    record=json.loads(path.read_text())
    if record['revision']!=reference['revision']:raise FileExistsError('Attachment revision does not match its receipt')
    return {'name':identifier[7:23]+'-'+record['name'],'source':record['name'],
            'revision':record['revision'],'upload_id':identifier,'snapshot_path':str(path)}


def attachments(references):
    if references is None:return []
    if not isinstance(references,list) or len(references)>8:raise ValueError('Attach at most eight files per message')
    return [resolve(reference) for reference in references]


def content(handler,reference):
    item=resolve(reference)
    record=json.loads(Path(item['snapshot_path']).read_text())
    raw=base64.b64decode(record['base64'],validate=True)
    if hashlib.sha256(raw).hexdigest()!=item['revision']:raise ValueError('Stored attachment failed integrity verification')
    handler.send_response(200)
    handler.send_header('Content-Type',mimetypes.guess_type(record['name'])[0] or 'application/octet-stream')
    handler.send_header('Content-Length',str(len(raw)))
    handler.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+urllib.parse.quote(record['name'],safe=''))
    handler.send_header('X-Content-Type-Options','nosniff')
    handler.send_header('Content-Security-Policy',"sandbox; default-src 'none'")
    handler.end_headers()
    if handler.command!='HEAD':handler.wfile.write(raw)

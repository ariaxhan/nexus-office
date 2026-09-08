"""Shared local object identity, file policy and bounded reads for the phone."""
from __future__ import annotations

import base64
import codecs
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import stat
import threading

import context
import sessions
import office_workspaces

MAX_TEXT = 1024 * 1024
SKIP = (context.SKIP_DIRS - {'.agents'}) | {'.codex', '.claude', '.ssh', '.aws', '.gnupg'}
PRIVATE_NAMES = {'credentials.json', 'auth.json', 'secrets.yaml', 'secrets.json'}
LOCK = threading.RLock()


def vault() -> Path:
    value = os.environ.get('OFFICE_RUNTIME_ROOT', '')
    if not value:
        raise ValueError('Office runtime root is not configured')
    return Path(value).expanduser().resolve()


def roots() -> dict:
    base = vault()
    items = list(office_workspaces.discover(base))
    items.extend((name, base / name) for name in ('CollabVault', '_meta'))
    items.extend(task_workspaces())
    output = {}
    for name, path in items:
        if not path.is_dir():
            continue
        key = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]
        output[key] = {'id': key, 'name': name, 'path': str(path.resolve()),'label':name+' · '+os.path.relpath(path,base)}
    return output


def encode(root_id: str, relative: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([root_id, relative]).encode()).decode().rstrip('=')


def decode(value: str) -> tuple:
    try:
        parts = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
    except (ValueError, TypeError):
        raise ValueError('Invalid object identifier') from None
    if not isinstance(parts, list) or len(parts) != 2:
        raise ValueError('Invalid object identifier')
    if not all(isinstance(p, str) for p in parts):
        raise ValueError('Invalid object identifier')
    return tuple(parts)


def private_part(part):
    visible={'.github','.agents','.gitignore','.gitattributes','.editorconfig','.prettierrc','.eslintrc'}
    return part in ('.','..') or part in SKIP or (part.startswith('.') and part not in visible)


def permitted(relative: str) -> bool:
    parts = Path(relative).parts
    if not relative or not parts or Path(relative).is_absolute() or '\\' in relative or '\x00' in relative:
        return False
    if any(private_part(p) for p in parts):
        return False
    name = parts[-1].lower()
    if name in PRIVATE_NAMES or name in {'token', 'secrets', 'credentials'}:
        return False
    return Path(name).suffix not in {'.key', '.pem', '.p12', '.pickle', '.sqlite', '.sqlite3', '.db'}


def resolve(identifier: str, directory=False) -> tuple:
    root_id, relative = decode(identifier)
    root = roots().get(root_id)
    if not root:
        raise FileNotFoundError('Workspace is no longer available')
    base = Path(root['path'])
    if relative == '' and directory:
        return root, base
    if not permitted(relative):
        raise PermissionError('This path is not a work object')
    candidate = base
    for part in Path(relative).parts:
        candidate /= part
        if candidate.is_symlink():
            raise PermissionError('Linked paths cannot be opened')
    candidate.resolve(strict=True).relative_to(base)
    return root, candidate


def entry(root: dict, path: Path) -> dict:
    info = path.stat()
    relative = path.relative_to(root['path']).as_posix()
    return {'id': encode(root['id'], relative), 'name': path.name, 'path': relative,
            'root_id': root['id'], 'project': root['name'],
            'kind': 'folder' if path.is_dir() else 'file', 'bytes': info.st_size,
            'modified': info.st_mtime, 'mime': mimetypes.guess_type(path.name)[0] or 'application/octet-stream'}


def browse(identifier='', cursor=0, limit=100) -> dict:
    if not identifier:
        items = [dict(r, id=encode(r['id'], ''), kind='folder') for r in roots().values()]
        return {'items': items, 'next_cursor': None, 'source': 'Mac workspaces'}
    root, path = resolve(identifier, directory=True)
    if not path.is_dir():
        raise ValueError('This object is not a folder')
    children = []
    for child in path.iterdir():
        if child.is_symlink() or not permitted(child.relative_to(root['path']).as_posix()):
            continue
        try:
            children.append(entry(root, child))
        except OSError:
            continue
    children.sort(key=lambda x: (x['kind'] != 'folder', x['name'].lower()))
    start = max(0, int(cursor)); end = start + min(200, max(1, int(limit)))
    parent = path.parent if path != Path(root['path']) else None
    return {'items': children[start:end], 'total': len(children),
            'next_cursor': end if end < len(children) else None,
            'parent': encode(root['id'], ('' if parent == Path(root['path']) else parent.relative_to(root['path']).as_posix())) if parent else '',
            'path': str(path), 'source': 'Local checkout', 'root_id': root['id']}


def text_bytes(raw, mime):
    if b'\x00' in raw or mime.startswith(('audio/', 'image/', 'video/', 'application/pdf')):
        return False
    try:
        codecs.getincrementaldecoder('utf-8')().decode(raw,final=False)
    except UnicodeDecodeError:
        return False
    return True


def read(identifier: str, offset=0) -> dict:
    root, path = resolve(identifier)
    if not path.is_file():
        raise ValueError('This object is not a regular file')
    data = entry(root, path)
    start = max(0, int(offset))
    signature=path.stat()
    start,raw=read_chunk(path,start)
    is_text=text_bytes(raw,data['mime'])
    text=''
    consumed=len(raw)
    if is_text:
        decoder=codecs.getincrementaldecoder('utf-8')()
        text=decoder.decode(raw,final=False)
        consumed-=len(decoder.getstate()[0])
        if decoder.getstate()[0] and start+len(raw)>=data['bytes']:
            is_text=False;text='';consumed=len(raw)
    revision=file_revision(path)
    first_line=line_number(path,start) if is_text else None
    after=path.stat()
    if (signature.st_mtime_ns,signature.st_size,signature.st_ino)!=(after.st_mtime_ns,after.st_size,after.st_ino):
        raise FileExistsError('File changed while reading; reload the current version')
    data.update(text=text,is_text=is_text,offset=start,line_start=first_line,
                line_end=first_line+text.count("\n")-(1 if text.endswith("\n") else 0) if is_text else None,
                next_offset=start+consumed if start+consumed<data['bytes'] else None,
                content_url='/api/objects/content?id='+identifier,revision=revision,
                editable=is_text and start==0 and data['bytes']<=MAX_TEXT)
    return data


def save(body: dict) -> dict:
    identifier = body.get('id', '')
    text = body.get('text')
    if not isinstance(text, str) or len(text.encode()) > MAX_TEXT:
        raise ValueError('File must be text smaller than 1 MiB')
    with LOCK:
        current = read(identifier)
        if not current['editable']:
            raise PermissionError('This file cannot be edited here')
        if current['revision'] != body.get('revision'):
            raise FileExistsError('File changed. Reopen and compare before saving.')
        root, path = resolve(identifier)
        return atomic_save(path, text, current, root)


def atomic_save(path: Path, text: str, current: dict, root: dict) -> dict:
    import tempfile
    fd, name = tempfile.mkstemp(prefix='.office-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        os.chmod(name, stat.S_IMODE(path.stat().st_mode))
        if hashlib.sha256(path.read_bytes()).hexdigest() != current['revision']:
            raise FileExistsError('File changed before saving. Your draft was kept.')
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    return read(current['id'])


def diff(body):
    import difflib
    text=body.get('text')
    if not isinstance(text,str) or len(text.encode())>MAX_TEXT:raise ValueError('Draft exceeds the edit size limit')
    current=read(body.get('id',''))
    if not current['editable']:raise PermissionError('This file is not editable')
    if current['revision']!=body.get('revision'):raise FileExistsError('File changed; compare with the current Mac version')
    patch=''.join(difflib.unified_diff(current['text'].splitlines(keepends=True),text.splitlines(keepends=True),
                                      fromfile=current['path'],tofile=current['path']+' (draft)'))
    return {'diff':patch,'revision':current['revision']}


def task_workspaces():
    from contextlib import closing
    import office_system
    try:
        with closing(office_system.connect()) as db:
            rows=db.execute("SELECT subject FROM events WHERE kind='office.task'").fetchall()
    except (OSError,ValueError,office_system.sqlite3.Error):return []
    result=[]
    for row in rows:
        task=row['subject']
        if not __import__('re').fullmatch(r'task_[A-Za-z0-9]+',task):continue
        path=office_system.run_board.LEDGER.parent/'office-tasks'/task/'checkout'
        if path.is_dir() and not any(part.is_symlink() for part in (path,*path.parents)):
            result.append(('Conversation '+task,path))
    return result


def read_chunk(path,start):
    with path.open('rb') as stream:
        stream.seek(start)
        first=stream.read(1)
        # A caller can bookmark a byte position; normalize UTF-8 continuation
        # bytes to their leading byte rather than manufacture replacement text.
        for _ in range(3):
            if not start or not first or first[0]&0xc0!=0x80:break
            start-=1;stream.seek(start);first=stream.read(1)
        stream.seek(start)
        return start,stream.read(MAX_TEXT)


def file_revision(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024):digest.update(block)
    return digest.hexdigest()


def line_number(path,offset):
    lines=1
    with path.open('rb') as stream:
        remaining=offset
        while remaining:
            block=stream.read(min(65536,remaining))
            if not block:break
            lines+=block.count(b'\n');remaining-=len(block)
    return lines


def selection(reference):
    start,end=reference.get('start_line'),reference.get('end_line')
    if type(start) is not int or type(end) is not int or start<1 or end<start or end-start>10000:
        raise ValueError('Choose an ordered range of at most10,001 lines')
    root,path=resolve(reference['id'])
    if file_revision(path)!=reference['revision']:raise FileExistsError('The selected file changed; reopen it')
    pieces=[];size=0;last=0
    with path.open(encoding='utf-8') as stream:
        for number,line in enumerate(stream,1):
            if number<start:continue
            size+=len(line.encode())
            if size>MAX_TEXT:raise ValueError('Selected context exceeds1 MiB')
            pieces.append(line);last=number
            if number==end:break
    if last!=end:raise ValueError('Selected lines are outside this file')
    if file_revision(path)!=reference['revision']:raise FileExistsError('The file changed while selecting context')
    return ''.join(pieces)

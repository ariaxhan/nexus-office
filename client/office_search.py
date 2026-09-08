"""Rebuildable search over the same objects the phone can open.

The index is a disposable projection. Building never holds an HTTP request;
readers see a complete previous generation until its replacement is committed.
"""
from contextlib import closing
import json
import io
import hashlib
import os
import re
from pathlib import Path
import sqlite3
import shutil
import threading
import time

import office_projection as projection
import office_bot_history as bot_history
import office_archives as archives
import office_media as media
import office_objects as objects
import office_preferences as preferences

LOCK = threading.Lock()
STATUS = {'state': 'unbuilt', 'started_at': None, 'finished_at': None, 'errors': []}
TTL = 120
STORAGE_CHECK_AT = 0


def path():
    return preferences.path().parent / 'search.sqlite'


def connect():
    preferences.private_state.ensure_dir(path().parent)
    db = sqlite3.connect(path(), timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS objects (id TEXT PRIMARY KEY, title TEXT, path TEXT, project TEXT, kind TEXT, body TEXT, modified REAL, coverage TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS search_lookup ON objects(modified DESC,id,title,path,kind,project)')
    db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS words USING fts5(id UNINDEXED, title, path, body, tokenize="unicode61")')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS signatures (id TEXT PRIMARY KEY, value TEXT)')
    db.execute('CREATE TEMP TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)')
    prepare_word_owners(db)
    return db


def walk(root, errors, boundaries=()):
    base = Path(root['path'])
    nested=set(boundaries)-{str(base)}
    def error(exc):
        errors.append({'source': root['name'], 'error': str(exc)[:200]})
    for directory, dirs, files in os.walk(base, followlinks=False, onerror=error):
        parent = Path(directory)
        dirs[:] = [d for d in dirs if str(parent/d) not in nested and allowed(base, parent / d)]
        for name in files:
            candidate = parent / name
            if allowed(base, candidate):
                yield candidate


def allowed(base, candidate):
    return not candidate.is_symlink() and objects.permitted(candidate.relative_to(base).as_posix())


def file_record(root, candidate):
    entry = objects.entry(root, candidate)
    # Never decode binary documents as prose; the filename still remains findable.
    with candidate.open('rb') as stream:
        first = stream.read(65536)
    textual = objects.text_bytes(first, entry['mime'])
    body = first.decode('utf-8',errors='replace') if textual else ''
    return {'id': entry['id'], 'title': entry['name'], 'path': str(candidate),
            'project': root['name'], 'kind': 'file', 'body': body,
            'modified': entry['modified'], 'coverage': 'full text' if textual else 'name only: binary'}


def rebuild(extra=()):
    global STATUS
    STATUS = {'state': 'indexing', 'started_at': time.time(), 'finished_at': None, 'errors': []}
    count = 0
    try:
        with closing(connect()) as db, db:
            db.execute('DELETE FROM seen')
            roots=list(objects.roots().values())
            boundaries={root['path'] for root in roots}
            for root in roots:
                for candidate in walk(root, STATUS['errors'],boundaries):
                    try:
                        index_file(db, root, candidate); count += 1
                        STATUS.update(count=count,current_source=root['name'])
                    except (OSError, ValueError) as exc:
                        STATUS['errors'].append({'source': root['name'], 'path': str(candidate), 'error': str(exc)[:200]})
            from itertools import chain
            for record in chain(archives.records(STATUS['errors']),bot_history.records(STATUS['errors'])):
                count+=index_history(db,record,STATUS['errors'])
                STATUS.update(count=count,current_source=record['project'])
            for record in media_records(STATUS['errors']):
                insert(db, record); count += 1
            for record in projection.records(STATUS['errors']):
                count+=index_projection(db,record,STATUS['errors'])
                STATUS.update(count=count,current_source=record['project'])
            for record in extra:
                insert(db, record); count += 1
            db.execute('DELETE FROM words WHERE rowid IN (SELECT word_rowid FROM word_owners WHERE object_id NOT IN (SELECT id FROM seen))')
            db.execute('DELETE FROM word_owners WHERE object_id NOT IN (SELECT id FROM seen)')
            for table in ('objects','signatures'):
                db.execute(f'DELETE FROM {table} WHERE id NOT IN (SELECT id FROM seen)')
            completed=dict(STATUS,state='partial' if STATUS['errors'] else 'ready',finished_at=time.time(),count=count)
            sources=source_coverage(db,completed)
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('source_coverage',json.dumps({'generation':completed['finished_at'],'sources':sources})))
            db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('coverage', json.dumps(completed)))
        STATUS.update(completed)
    except Exception as exc:
        STATUS.update(state='error', error=str(exc)[:200])
    finally:
        LOCK.release()


def index_file(db, root, candidate):
    entry=objects.entry(root,candidate)
    signature=source_signature(candidate,root['name'])
    if unchanged(db,entry['id'],signature):return
    row = file_record(root, candidate)
    insert(db, row)
    if row['coverage'] == 'full text':
        index_text(db, row, candidate)
    db.execute('INSERT OR REPLACE INTO signatures VALUES (?,?)',(row['id'],signature))


def index_text(db, row, candidate):
    with candidate.open(encoding='utf-8', errors='replace') as stream:
        previous = ''
        for chunk in searchable_chunks(stream):
            insert_words(db,row,previous+chunk)
            previous = chunk[-256:]


def insert(db, row):
    row=dict(row,body=''.join(searchable_chunks(io.StringIO(row['body']))))
    signature='record:'+hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()
    if unchanged(db,row['id'],signature):return
    delete_words(db,row['id'])
    db.execute('INSERT OR REPLACE INTO objects VALUES (:id,:title,:path,:project,:kind,:body,:modified,:coverage)', row)
    insert_words(db,row,row['body'])
    db.execute('INSERT OR REPLACE INTO signatures VALUES (?,?)',(row['id'],signature))


def refresh(extra=()):
    projection.github_cache.refresh(projection.WORLD)
    if not LOCK.acquire(blocking=False):
        return
    threading.Thread(target=rebuild, args=(extra,), daemon=True, name='office-search').start()


def coverage(db):
    row = db.execute("SELECT value FROM meta WHERE key='coverage'").fetchone()
    result = json.loads(row[0]) if row else {'state': 'unbuilt', 'finished_at': None, 'count': 0}
    result['refresh'] = dict(STATUS)
    result['sources'] = source_coverage(db,result)
    result['github'] = projection.github_cache.coverage(projection.github_cache.known(projection.WORLD),result['sources'])
    if any(repo['state']!='indexed' for repo in result['github']):result['state']='partial'
    if any(source['state']=='partial' for source in result['sources']):result['state']='partial'
    return result


def terms(query):
    return ' AND '.join('"' + word.replace('"','""') + '"*' for word in query.split())


def search(query='', cursor=0, kind='', project=''):
    query = str(query).strip()
    if len(query) > 200:
        raise ValueError('Search is at most 200 characters')
    with closing(connect()) as db:
        db.execute('BEGIN')
        status = coverage(db)
        if os.environ.get('OFFICE_SEARCH_AUTOBUILD','1')=='0':
            status['refresh']={'state':'paused','detail':'Index maintenance in progress; source readers remain available'}
        elif time.time() - (status.get('finished_at') or 0) > TTL:
            refresh()
        if not query:
            return {'items': [], 'coverage': status, 'next_cursor': None}
        return results(db, query, cursor, kind, project, status)


def results(db, query, cursor, kind, project, status):
    pattern = '%' + query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
    sql = r"""FROM objects o INDEXED BY search_lookup WHERE (title LIKE ? ESCAPE '\' OR path LIKE ? ESCAPE '\'
             OR id IN (SELECT id FROM words WHERE words MATCH ?))
             AND (?='' OR kind=?) AND (?='' OR project=?)"""
    params = (pattern, pattern, terms(query), kind, kind, project, project)
    total = db.execute('SELECT count(*) ' + sql, params).fetchone()[0]
    generation=str(int((status.get('finished_at') or 0)*1000000))
    signature=hashlib.sha256(json.dumps([query,kind,project]).encode()).hexdigest()[:16]
    start=cursor_offset(cursor,generation,signature)
    rows = db.execute('SELECT * ' + sql + ' ORDER BY modified DESC, id LIMIT 40 OFFSET ?', (*params,start)).fetchall()
    return {'items': [result(db, row, query) for row in rows], 'total': total, 'coverage':status,
            'next_cursor': f'{generation}:{start+40}:{signature}' if start + 40 < total else None}


def result(db, row, query):
    data = dict(row)
    body = data.pop('body')
    index = body.lower().find(query.lower())
    start = max(0,index-60)
    match = db.execute("SELECT snippet(words,3,'','','…',35) FROM words WHERE rowid IN (SELECT word_rowid FROM word_owners WHERE object_id=?) AND words MATCH ? LIMIT 1", (row['id'],terms(query))).fetchone()
    data['excerpt'] = match[0] if match else body[start:start+240]
    return data


def media_records(errors):
    for loader in (media.podcasts, media.substrate):
        try:
            rows=loader()
        except (OSError, ValueError) as exc:
            errors.append({'source':'media','error':str(exc)[:200]});continue
        for item in rows:
            try:
                detail=media.detail(item['id'],include_provenance=False)
                yield {'id':item['id'],'title':item['title'],'path':item.get('date',''),
                       'project':'Library','kind':item['kind'],'body':detail.get('text',''),
                       'modified':0,'coverage':'published full text'}
            except (OSError,ValueError) as exc:
                errors.append({'source':item['id'],'error':str(exc)[:200]})


def object_detail(identifier,offset=0,revision=""):
    with closing(connect()) as db:
        row=db.execute('SELECT * FROM objects WHERE id=?',(identifier,)).fetchone()
    if row is None:raise FileNotFoundError('This indexed object is no longer available')
    data=projection.current(dict(row)) if identifier.startswith('projection:') else dict(row)
    if revision and revision!=data.get('revision'):raise FileExistsError('Source changed; reopen this result')
    body=data.pop('body');start=max(0,int(offset));end=start+65536
    data.update(text=body[start:end],next_offset=end if end<len(body) else None)
    return data


def cursor_offset(cursor,generation,signature):
    if str(cursor)=='0':return 0
    parts=str(cursor).split(':')
    if len(parts)!=3 or parts[0]!=generation or parts[2]!=signature:
        raise FileExistsError('Search results changed. Refresh from the first page.')
    offset=int(parts[1])
    if offset<0:raise ValueError('Invalid search cursor')
    return offset


def index_history(db,record,errors):
    source=Path(record.pop('source_path'))
    try:
        if not source.is_file():raise FileNotFoundError('Retained source disappeared during indexing')
        signature=source_signature(source,record['project'])
        if unchanged(db,record['id'],signature):return 1
        insert(db,record);index_text(db,record,source)
        db.execute('INSERT OR REPLACE INTO signatures VALUES (?,?)',(record['id'],signature))
        return 1
    except (OSError,ValueError) as exc:
        errors.append({'source':str(source),'error':str(exc)[:200]})
        delete_words(db,record['id'])
        db.execute('DELETE FROM objects WHERE id=?',(record['id'],))
        return 0


def source_coverage(db,status):
    cached=db.execute("SELECT value FROM meta WHERE key='source_coverage'").fetchone()
    value=json.loads(cached[0]) if cached else {}
    if value.get('generation')==status.get('finished_at') and 'sources' in value:return value['sources']
    rows=db.execute('SELECT project,kind,coverage,count(*) indexed_count FROM objects GROUP BY project,kind,coverage ORDER BY project,kind').fetchall()
    result=[]
    for row in rows:
        partial='snapshot' in row['coverage'].lower()
        result.append({'source':row['project'],'kind':row['kind'],'indexed':row['indexed_count'],'total':None,
                       'coverage':row['coverage'],'state':'partial' if partial else 'indexed',
                       'observed_at':status.get('finished_at')})
    return result


def source_signature(source,project):
    stat=source.stat()
    return json.dumps(['readable-v3',str(source),project,stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns])


def unchanged(db,identifier,signature):
    db.execute('INSERT OR IGNORE INTO seen VALUES (?)',(identifier,))
    row=db.execute('SELECT value FROM signatures WHERE id=?',(identifier,)).fetchone()
    return bool(row and row[0]==signature and db.execute('SELECT 1 FROM objects WHERE id=?',(identifier,)).fetchone())


def prepare_word_owners(db):
    db.execute('CREATE TABLE IF NOT EXISTS word_owners (word_rowid INTEGER PRIMARY KEY, object_id TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS word_owners_object ON word_owners(object_id)')
    if not db.execute("SELECT 1 FROM meta WHERE key='word_owners_v1'").fetchone():
        db.execute('INSERT OR REPLACE INTO word_owners SELECT rowid,id FROM words')
        db.execute("INSERT INTO meta VALUES ('word_owners_v1','1')")
        db.commit()


def insert_words(db,row,body):
    storage_guard()
    result=db.execute('INSERT INTO words VALUES (?,?,?,?)',(row['id'],row['title'],row['path'],body))
    db.execute('INSERT INTO word_owners VALUES (?,?)',(result.lastrowid,row['id']))


def delete_words(db,identifier):
    db.execute('DELETE FROM words WHERE rowid IN (SELECT word_rowid FROM word_owners WHERE object_id=?)',(identifier,))
    db.execute('DELETE FROM word_owners WHERE object_id=?',(identifier,))


def index_projection(db,record,errors):
    if 'source_path' in record:return index_history(db,record,errors)
    insert(db,record)
    return 1


BINARY_PREFIX=re.compile(r'data:[A-Za-z0-9.+/-]+(?:;[^,\s]{1,80})*;base64,|"(?:data_base64|base64)"\s*:\s*"')
BINARY_END=re.compile(r'[^A-Za-z0-9+/=]')
BINARY_QUOTED_END=re.compile(r'[^A-Za-z0-9+/=\\\r\n]')


def searchable_chunks(stream):
    """Keep readable source text; encoded media remains in the original file."""
    carry='';encoded=False;ending=BINARY_END
    while chunk:=stream.read(65536):
        carry+=chunk
        while carry:
            if encoded:
                end=ending.search(carry)
                if end is None:
                    carry='';break
                carry=carry[end.start():];encoded=False
            marker=BINARY_PREFIX.search(carry)
            if marker:
                ending=binary_ending(carry,marker)
                yield carry[:marker.end()]
                carry=carry[marker.end():];encoded=True
            else:
                if len(carry)>256:
                    yield carry[:-256];carry=carry[-256:]
                break
    if carry and not encoded:yield carry


def storage_guard():
    global STORAGE_CHECK_AT
    now=time.monotonic()
    if now-STORAGE_CHECK_AT<1:return
    STORAGE_CHECK_AT=now
    if shutil.disk_usage(path().parent).free<5*1024**3:
        raise RuntimeError('Search build stopped below the 5 GB free-storage threshold; previous committed index retained')


def binary_ending(text,marker):
    quoted=marker.group().startswith('"') or (marker.start()>0 and text[marker.start()-1] in ('"',"'"))
    return BINARY_QUOTED_END if quoted else BINARY_END

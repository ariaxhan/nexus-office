"""Private transactional collection journal; published snapshots stay atomic."""
from contextlib import closing
import json
import os
import sqlite3
import shutil
from pathlib import Path
import tempfile
import time

import office_github_batches as batches
import private_state


def connect(target):
    path=target.with_suffix('.stage.sqlite')
    private_state._assert_bounded(path,target.parent)
    descriptor=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:os.fchmod(descriptor,0o600)
    finally:os.close(descriptor)
    db=sqlite3.connect(path,timeout=5)
    db.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
    return db,path


def advance(access,known,repo,target,limit=8):
    db,path=connect(target)
    with closing(db):
        row=db.execute('SELECT body FROM checkpoint WHERE id=1').fetchone()
        state=json.loads(row[0]) if row else batches.initial()
        for _ in range(limit):
            if state['stage']=='done':break
            capacity(target,0)
            rows,state=batches.next_batch(access,known,repo,state)
            commit(db,rows,state)
        count=db.execute('SELECT count(*) FROM records').fetchone()[0]
        result={'state':'fetching','records':count,'stage':state['stage'],'repo':repo}
        if state['stage']=='done':result=publish(db,target,repo,count,state['publication'])
    if result['state']=='ready':path.unlink()
    return result


def commit(db,rows,state):
    if state['stage']=='done' and 'publication' not in state:
        state['publication']={'generation':str(time.time_ns()),'finished_at':time.time()}
    encoded=[(row['id'],json.dumps(row,ensure_ascii=False)) for row in rows]
    location=Path(db.execute('PRAGMA database_list').fetchone()[2])
    capacity(location,2*sum(len(body.encode('utf-8')) for _,body in encoded))
    with db:
        db.executemany('INSERT OR REPLACE INTO records VALUES (?,?)',encoded)
        db.execute('INSERT OR REPLACE INTO checkpoint VALUES (1,?)',(json.dumps(state),))


def publish(db,target,repo,count,publication):
    info={'repo':repo,**publication,'count':count}
    size=db.execute('SELECT coalesce(sum(length(cast(body AS BLOB))),0) FROM records').fetchone()[0]
    capacity(target,size+count+1024)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=target.parent,delete=False) as stream:
            temporary=stream.name;stream.write(json.dumps(info)+'\n')
            for row in db.execute('SELECT body FROM records ORDER BY id'):stream.write(row[0]+'\n')
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,target);temporary=None
    finally:
        if temporary is not None:os.unlink(temporary)
    return dict(info,state='ready',records=count)


def capacity(path,additional):
    if shutil.disk_usage(path.parent).free<5*1024**3+additional:
        raise OSError('GitHub collection paused: keep at least 5 GiB free; committed progress is retained')

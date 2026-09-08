"""Cursor history from the existing Nexus ledger, with bounded log reads."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import time

import run_board


def connect():
    if not run_board.LEDGER.is_file():
        raise FileNotFoundError('Nexus ledger is not installed')
    db=sqlite3.connect(f'file:{run_board.LEDGER}?mode=ro',uri=True,timeout=3)
    db.row_factory=sqlite3.Row
    return db


def plans():
    with closing(connect()) as db:
        rows=db.execute('SELECT id,name,kind,schedule,enabled,quarantined_at,objective FROM plans ORDER BY name').fetchall()
    return {'items':[dict(row) for row in rows], 'observed_at':time.time(), 'source':'Nexus ledger'}


def runs(cursor=0,plan='',state=''):
    start=max(0,int(cursor))
    query="""FROM flights f LEFT JOIN tasks t ON t.id=f.task_id JOIN plans p ON p.id=f.plan_id
             WHERE (?='' OR p.id=? OR p.name=?) AND (?='' OR f.state=?)"""
    params=(plan,plan,plan,state,state)
    with closing(connect()) as db:
        total=db.execute('SELECT count(*) '+query,params).fetchone()[0]
        rows=db.execute('SELECT f.id,f.task_id,f.state,f.created_at,f.started_at,f.ended_at,f.attempt,p.name plan,t.title '+query+' ORDER BY f.created_at DESC,f.id LIMIT 40 OFFSET ?',(*params,start)).fetchall()
    return {'items':[dict(row) for row in rows],'total':total,'next_cursor':start+40 if start+40<total else None,'observed_at':time.time(),'source':'Nexus ledger: all retained history'}


def validate_flight(identifier):
    if not re.fullmatch(r'flt_[A-Za-z0-9]+',identifier):
        raise ValueError('Invalid flight identity')


def validate_lane(lane):
    if lane and not re.fullmatch(r'[A-Za-z0-9_.-]+\.out',lane):
        raise ValueError('Invalid lane log')


def flight(identifier):
    validate_flight(identifier)
    with closing(connect()) as db:
        row=db.execute('SELECT f.*,t.title,t.objective,p.name plan FROM flights f LEFT JOIN tasks t ON t.id=f.task_id JOIN plans p ON p.id=f.plan_id WHERE f.id=?',(identifier,)).fetchone()
        if row is None:
            raise FileNotFoundError('Flight is not in the ledger')
        data=dict(row)
        data['artifacts']=[dict(r) for r in db.execute('SELECT * FROM artifacts WHERE flight_id=? ORDER BY created_at',(identifier,))]
    return data


def events(identifier,cursor=0):
    flight(identifier)
    with closing(connect()) as db:
        rows=db.execute('SELECT * FROM events WHERE subject=? AND id>? ORDER BY id LIMIT 100',(identifier,max(0,int(cursor)))).fetchall()
    items=[dict(row,payload=json.loads(row['payload'])) for row in rows]
    return {'items':items,'next_cursor':items[-1]['id'] if len(items)==100 else None,'source':'Nexus append-only events'}


def log(identifier,offset=0,lane=''):
    return read_flight_log(identifier,offset,lane,'')


def read_flight_log(identifier,offset,lane,version):
    flight(identifier)
    base=run_board.FLIGHTS/identifier
    validate_lane(lane)
    path=base/'lanes'/lane if lane else base/'log'
    if not lane and not path.exists():
        path=run_board.LEDGER.parent/'logs'/f'{identifier}.log'
    from office_jobs import read_log
    return read_log(path,offset,version)


def machine():
    import os
    import shutil
    import subprocess
    disk=shutil.disk_usage(Path.home())
    pressure=subprocess.run(['memory_pressure','-Q'],capture_output=True,text=True,timeout=5)
    uptime=subprocess.run(['uptime'],capture_output=True,text=True,timeout=5)
    return {'observed_at':time.time(),'source':'Mac operating system','load':os.getloadavg(),
            'disk':{'total':disk.total,'used':disk.used,'free':disk.free},
            'memory':pressure.stdout.strip(),'uptime':uptime.stdout.strip()}


def command(body):
    from nexus.ledger import Ledger
    from nexus import cli
    import office_tasks
    action=body.get('action');identifier=body.get('id','');key=office_tasks.request_id(body)
    if action not in ('pause','resume','run','retry','cancel'):raise ValueError('Unknown system action')
    with closing(Ledger(str(run_board.LEDGER))) as ledger:
        return apply_command(ledger,action,identifier,key)


def apply_command(ledger,action,identifier,key):
    from nexus import cli
    prior=ledger.events(kind='office.system_receipt',subject=key)
    if prior:
        receipt=json.loads(prior[0]['payload'])
        if (receipt['action'],receipt['id'])!=(action,identifier):raise FileExistsError('Request ID already belongs to another action')
        return receipt
    pending=reserve_command(ledger,action,identifier,key)
    if pending:return reconcile_command(ledger,action,identifier,key,pending)
    try:
        result=plan_command(ledger,action,identifier,key) if action in ('pause','resume','run') else flight_request(ledger,action,identifier,'phone-request:'+key)
    except (ValueError,FileNotFoundError,FileExistsError) as exc:
        result={'state':'rejected','detail':str(exc)}
    except Exception as exc:
        return {'action':action,'id':identifier,'result':{'state':'unconfirmed','detail':str(exc)}}
    return record_command(ledger,action,identifier,key,result)


def record_command(ledger,action,identifier,key,result):
    receipt={'action':action,'id':identifier,'result':result,'observed_at':time.time()}
    ledger.event('office.system_receipt',key,receipt,'phone')
    return receipt


def reconcile_command(ledger,action,identifier,key,pending):
    result=observe_command(ledger,action,identifier,key)
    if result is None:return pending
    return record_command(ledger,action,identifier,key,dict(result,evidence='Current Nexus owner state; no command replayed'))


def observe_command(ledger,action,identifier,key):
    if action in ('pause','resume'):
        plan=ledger.plan(identifier)
        if plan and bool(plan['enabled'])==(action=='resume'):return {'enabled':bool(plan['enabled'])}
        return None
    if action=='run':
        task=ledger.conn.execute('SELECT id,state FROM tasks WHERE dedupe_key=? ORDER BY created_at LIMIT 1',('phone-run:'+key,)).fetchone()
        return {'task_id':task['id'],'state':task['state']} if task else None
    return observe_flight_request(ledger,action,identifier,key)


def observe_flight_request(ledger,action,identifier,key):
    from nexus import flights
    row=ledger.flight(identifier)
    if row is None:return None
    if action=='cancel':
        if row['state'] in ('cancelled','failed','landed','produced') and not flights.alive(row['pid']):return {'state':row['state']}
        return None
    newer=ledger.conn.execute("SELECT f.id,f.state FROM flights f JOIN events e ON e.subject=f.id WHERE e.kind='flight.state' AND e.source=? ORDER BY e.id LIMIT 1",('phone-request:'+key,)).fetchone()
    return {'flight_id':newer['id'],'state':newer['state']} if newer else None


def plan_command(ledger,action,identifier,key):
    plan=ledger.plan(identifier)
    if plan is None:raise FileNotFoundError('Plan does not exist')
    if action!='run':
        ledger.set_plan_enabled(identifier,action=='resume');return {'enabled':action=='resume'}
    task=ledger.live_task_with_key('phone-run:'+key)
    if task:return {'task_id':task['id'],'state':'queued'}
    task_id=ledger.add_task(title='Run '+plan['name'],origin='phone',plan_id=identifier,
                            reason='Requested from Office',risk='low',dedupe_key='phone-run:'+key)
    return {'task_id':task_id,'state':'queued'}


def flight_command(ledger,action,identifier):
    return flight_request(ledger,action,identifier,'phone')


def flight_request(ledger,action,identifier,source):
    from nexus import cli
    row=ledger.flight(identifier)
    if row is None:raise FileNotFoundError('Flight does not exist')
    if action=='cancel':
        if row['state'] in ('cancelled','failed','landed'):return {'state':row['state']}
        if cli._cancel(ledger,row,identifier):raise RuntimeError('Process teardown is not confirmed; see run state')
        return {'state':'cancelled'}
    if row['state'] not in ('failed','cancelled'):raise FileExistsError('Retry requires a failed or cancelled attempt')
    task=ledger.task(row['task_id']) if row['task_id'] else None
    if task and task['state']=='abandoned':raise FileExistsError('This task was abandoned; create a new task')
    created=ledger.create_flight(row['plan_id'],task_id=row['task_id'],attempt=row['attempt']+1,source=source,unique_for_task=True)
    return {'flight_id':created,'state':'queued' if created else 'already-active'}


def reserve_command(ledger,action,identifier,key):
    with ledger.tx():
        row=ledger.conn.execute("SELECT payload FROM events WHERE kind='office.system_request' AND subject=?",(key,)).fetchone()
        if row:
            prior=json.loads(row['payload'])
            if (prior['action'],prior['id'])!=(action,identifier):raise FileExistsError('Request ID already has another action')
            return dict(prior,result={'state':'unconfirmed','detail':'Inspect current state before issuing another command.'})
        ledger._event('office.system_request',key,{'action':action,'id':identifier},'phone')
    return None


def artifact(identifier):
    import office_objects
    with closing(connect()) as db:
        row=db.execute('SELECT * FROM artifacts WHERE id=?',(identifier,)).fetchone()
    if row is None:raise FileNotFoundError('Artifact does not exist')
    path=Path(row['ref'])
    root=run_board.LEDGER.parent.resolve()
    relative=path.relative_to(root)
    if relative.parts[0] not in ('flights','logs','office-tasks') or not office_objects.permitted(str(relative)):
        raise PermissionError('Artifact is outside the owned output folders')
    if any(p.is_symlink() for p in (path,*path.parents)):raise PermissionError('Linked artifacts cannot be opened')
    if not path.is_file():raise FileNotFoundError('Recorded artifact bytes are unavailable')
    return path


def log_lanes(identifier):
    flight(identifier)
    folder=run_board.FLIGHTS/identifier/'lanes'
    if any(path.is_symlink() for path in (folder,*folder.parents)):raise PermissionError('Linked flight logs are unavailable')
    return {'items':[path.name for path in sorted(folder.glob('*.out')) if path.is_file() and not path.is_symlink()]}


def notifications(cursor=0):
    with closing(connect()) as db:
        rows=db.execute("SELECT id,ts,payload FROM events WHERE kind='office.podcast_ready' AND id>? ORDER BY id LIMIT 40",(max(0,int(cursor)),)).fetchall()
    return {'items':[dict(row,payload=json.loads(row['payload'])) for row in rows],
            'next_cursor':rows[-1]['id'] if len(rows)==40 else None}

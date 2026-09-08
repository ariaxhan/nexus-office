"""Revisioned user objects; independent of task execution and preferences."""
import json
import math
import threading
import time

import office_preferences as preferences
import private_state

LOCK=threading.RLock()
KINDS={'saved','recent','listening','reading','seen'}


def path():
    return preferences.path().with_name('user-objects.json')


def read():
    try:data=json.loads(path().read_text())
    except FileNotFoundError:data={'revision':0,'items':{}}
    return dict(data,server_time=time.time()*1000)


def validate(body):
    kind=body.get('kind');identifier=body.get('id');value=body.get('value');stamp=body.get('recorded_at')
    if kind not in KINDS or not isinstance(identifier,str) or not 1<=len(identifier)<=8192 or identifier in ('__proto__','constructor','prototype'):raise ValueError('Invalid user object identity')
    if type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp<0 or stamp>time.time()*1000+60000:raise ValueError('Invalid user object timestamp')
    validate_value(kind,value)
    return kind,identifier,value,stamp


def validate_value(kind,value):
    if value is not None and len(json.dumps(value).encode())>32768:raise ValueError('User object is too large')
    if kind in ('listening','reading','seen') and value is not None:
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('Position must be a nonnegative number')
    if kind in ('saved','recent') and value is not None:
        if not isinstance(value,dict) or not isinstance(value.get('kind'),str) or not isinstance(value.get('id'),str):raise ValueError('Saved objects need a kind and ID')



def save(body):
    kind,identifier,value,stamp=validate(body)
    with LOCK:
        if kind in ('recent','listening','reading') and not preferences.read()['preferences']['remember']:
            raise PermissionError('Remembering activity is disabled')
        data=read();group=data['items'].setdefault(kind,{})
        old=group.get(identifier)
        if old and old['recorded_at']>=stamp:return {'item':old,'revision':data['revision'],'applied':False}
        data['revision']+=1
        item={'value':value,'recorded_at':stamp,'revision':data['revision']};group[identifier]=item
        data.pop('server_time',None)
        private_state.ensure_dir(path().parent)
        private_state.atomic_write_text(path(),json.dumps(data))
        return {'item':item,'revision':data['revision'],'applied':True}


def forget_activity():
    with LOCK:
        data=read();stamp=time.time()*1000
        data['revision']+=1
        for kind in ('recent','listening','reading'):
            for identifier in data['items'].get(kind,{}):
                data['items'][kind][identifier]={'value':None,'recorded_at':stamp,'revision':data['revision']}
        data.pop('server_time',None)
        private_state.ensure_dir(path().parent)
        private_state.atomic_write_text(path(),json.dumps(data))

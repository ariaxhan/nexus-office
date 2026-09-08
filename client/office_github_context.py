"""Pin GitHub file/change context into the existing immutable upload store."""
import base64
import json
import re

import office_github as github
import office_uploads as uploads


def snapshot(access,known,body):
    repo=body.get('repo','')
    who,token=github.read_identity(access,repo,known)
    if body.get('kind')=='file':
        text,source=file_context(access,known,body)
    elif body.get('kind')=='change':
        text,source=change_context(repo,who,token,body)
    else:raise ValueError('Choose a file or change to discuss')
    raw=(json.dumps(source,ensure_ascii=False,indent=2)+'\n\n'+text).encode('utf-8')
    receipt=uploads.upload({'name':'github-context.txt','base64':base64.b64encode(raw).decode('ascii')})
    return {'attachment':receipt,'source':source}


def commit(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{40}',value):
        raise ValueError('Reopen the GitHub source to select an exact commit')
    return value


def file_context(access,known,body):
    ref=commit(body.get('ref'));repo=body['repo'];path=body.get('path','')
    data=github.tree(access,known,{'repo':repo,'path':path,'ref':ref})
    item=data.get('object',{})
    if not item.get('readable'):raise ValueError('Choose a readable text file')
    if item.get('sha')!=body.get('blob'):raise FileExistsError('The selected GitHub blob no longer matches')
    text=item['text'];source={'kind':'github-file','repo':repo,'path':path,'commit':ref,'blob':item['sha']}
    if 'start_line' in body or 'end_line' in body:
        start=body.get('start_line');end=body.get('end_line');lines=text.splitlines(keepends=True)
        if type(start) is not int or type(end) is not int or not 1<=start<=end<=len(lines):raise ValueError('Choose a valid line range')
        text=''.join(lines[start-1:end]);source.update(start_line=start,end_line=end)
    return text,source


def change_context(repo,who,token,body):
    number=github.number(body.get('number'));head=commit(body.get('head'));base=commit(body.get('base'))
    endpoint=f'repos/{repo}/pulls/{number}'
    require_change(endpoint,who,token,head,base)
    diff,_=github.fetch(endpoint,who+':'+head+':'+base,token,'application/vnd.github.v3.diff')
    require_change(endpoint,who,token,head,base)
    if not isinstance(diff,str):raise ValueError('GitHub did not return a text diff')
    return diff,{'kind':'github-change','repo':repo,'number':number,'head':head,'base':base}


def require_change(endpoint,who,token,head,base):
    current,_=github.fresh(endpoint,who,token)
    if (current['head']['sha'],current['base']['sha'])!=(head,base):
        raise FileExistsError('The change moved; reopen it before attaching')

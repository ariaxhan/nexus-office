"""Complete paged GitHub source collection for the rebuildable search corpus."""
import hashlib
import json

import office_github as github

JSON_ACCEPT='application/vnd.github+json'


def pages(endpoint,who,token):
    separator='&' if '?' in endpoint else '?'
    current=f'{endpoint}{separator}per_page=100';seen=set()
    while current:
        if current in seen:raise ValueError('GitHub repeated a pagination cursor')
        seen.add(current)
        rows,stamp,current=github.fetch_page(current,who,token)
        yield from ((row,stamp) for row in rows)


def record(repo,category,row,number,stamp):
    path=f'{repo}#{github.number(number)}'
    key=path if category=='issue' else f"{path}:{category}:{row.get('id',number)}"
    identifier='projection:'+hashlib.sha256(('github:'+key).encode()).hexdigest()
    return {'id':identifier,'title':row.get('title') or f'{path} · {category}',
            'path':path,'project':repo,'kind':'github','category':category,
            'body':json.dumps(row,ensure_ascii=False,indent=2),
            'modified':stamp,'coverage':'Full retained GitHub record'}


CATEGORIES=('issue','diff','review','comment','inline discussion')


def identify(row):
    """The category of a snapshot record written before records carried one.

    Not a guess: the record's own id is a hash of its category, so every
    candidate is recomputed and the one that reproduces the id is the answer.
    A record whose id no candidate reproduces stays unidentified and is
    refetched, which is exactly the old behaviour.
    """
    if row.get('category') in CATEGORIES:return row['category']
    path=row.get('path')
    if not isinstance(path,str) or '#' not in path:return None
    repo,_,number=path.rpartition('#')
    try:body=json.loads(row.get('body') or '')
    except ValueError:body=None
    inner=body.get('id') if isinstance(body,dict) else None
    for category in CATEGORIES:
        key=path if category=='issue' else f"{path}:{category}:{inner if inner is not None else number}"
        if 'projection:'+hashlib.sha256(('github:'+key).encode()).hexdigest()==row.get('id'):
            return category
    return None


def repository_records(access,known,repo):
    who,token=github.read_identity(access,repo,known)
    pulls=[]
    for row,stamp in pages(f'repos/{repo}/issues?state=all&sort=created&direction=asc',who,token):
        yield record(repo,'issue',row,row['number'],stamp)
        if row.get('pull_request'):pulls.append(row['number'])
    for category,endpoint,parent in [('comment','issues/comments','issue_url'),('inline discussion','pulls/comments','pull_request_url')]:
        for row,stamp in pages(f'repos/{repo}/{endpoint}?sort=created&direction=asc',who,token):
            number=row[parent].rstrip('/').rsplit('/',1)[-1]
            yield record(repo,category,row,number,stamp)
    for number in pulls:
        yield from pull_records(repo,number,who,token)


def pull_records(repo,number,who,token):
    endpoint=f'repos/{repo}/pulls/{number}'
    before,stamp=github.fresh(endpoint,who,token)
    identity=(before['head']['sha'],before['base']['sha'])
    cache_owner=who+':'+':'.join(identity)
    diff,diff_stamp=github.diff(repo,number,identity[0],identity[1],who,token)
    yield record(repo,'diff',{'id':number,'title':before['title']+' · diff','head':identity[0],'base':identity[1],'diff':diff},number,diff_stamp)
    for row,observed in pages(endpoint+'/reviews',cache_owner,token):
        yield record(repo,'review',row,number,observed)
    after,_=github.fresh(endpoint,who,token)
    if (after['head']['sha'],after['base']['sha'])!=identity:
        raise FileExistsError(f'{repo}#{number} changed while collecting its diff and reviews')

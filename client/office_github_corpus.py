"""Complete paged GitHub source collection for the rebuildable search corpus."""
import hashlib
import json

import office_github as github

JSON_ACCEPT='application/vnd.github+json'


def pages(endpoint,who,token):
    page=1
    separator='&' if '?' in endpoint else '?'
    while True:
        rows,stamp=github.fetch(f'{endpoint}{separator}per_page=100&page={page}',who,token)
        if not isinstance(rows,list):raise ValueError('GitHub collection response is not a list')
        yield from ((row,stamp) for row in rows)
        if len(rows)<100:return
        page+=1


def record(repo,category,row,number,stamp):
    path=f'{repo}#{github.number(number)}'
    key=path if category=='issue' else f"{path}:{category}:{row.get('id',number)}"
    identifier='projection:'+hashlib.sha256(('github:'+key).encode()).hexdigest()
    return {'id':identifier,'title':row.get('title') or f'{path} · {category}',
            'path':path,'project':repo,'kind':'github','body':json.dumps(row,ensure_ascii=False,indent=2),
            'modified':stamp,'coverage':'Full retained GitHub record'}


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
    diff,diff_stamp=github.fetch(endpoint,cache_owner,token,'application/vnd.github.v3.diff')
    yield record(repo,'diff',{'id':number,'title':before['title']+' · diff','head':identity[0],'base':identity[1],'diff':diff},number,diff_stamp)
    for row,observed in pages(endpoint+'/reviews',cache_owner,token):
        yield record(repo,'review',row,number,observed)
    after,_=github.fresh(endpoint,who,token)
    if (after['head']['sha'],after['base']['sha'])!=identity:
        raise FileExistsError(f'{repo}#{number} changed while collecting its diff and reviews')

"""One resumable GitHub page or one head-bound pull-request snapshot."""
import copy
import json

import office_github_corpus as corpus

STAGES=('issues','comments','inline','heads','pulls','done')


VERSION=2


def initial():
    return {'version':VERSION,'stage':'issues','cursor':None,'seen':[],'pulls':[],'pull_index':0,'heads':{}}


def usable(checkpoint):
    """A checkpoint written by an older collector is restarted, not resumed.

    A pre-`heads` checkpoint carries no pull identities, so resuming it would
    refetch every pull in full and keep the server in the hot loop this stage
    exists to end. Restarting costs one cheap re-page and is correct.
    """
    return isinstance(checkpoint,dict) and checkpoint.get('version')==VERSION


def next_batch(access,known,repo,checkpoint,previous=None):
    state=copy.deepcopy(checkpoint)
    who,token=corpus.github.read_identity(access,repo,known)
    if state['stage']=='pulls':return pull_batch(repo,who,token,state,previous)
    if state['stage']=='done':return [],state
    return page_batch(repo,who,token,state)


def page_batch(repo,who,token,state):
    stage=state['stage']
    endpoints={'issues':'issues?state=all&sort=created&direction=asc','comments':'issues/comments?sort=created&direction=asc','inline':'pulls/comments?sort=created&direction=asc',
               'heads':'pulls?state=all&sort=created&direction=asc'}
    endpoint=state['cursor'] or f'repos/{repo}/{endpoints[stage]}&per_page=100'
    if endpoint in state['seen']:raise ValueError('GitHub repeated a pagination cursor')
    rows,stamp,following=corpus.github.fetch_page(endpoint,who,token)
    if following in [*state['seen'],endpoint]:raise ValueError('GitHub repeated a pagination cursor')
    state['seen'].append(endpoint);state['cursor']=following
    records=[record for record in (convert(repo,stage,row,stamp,state) for row in rows) if record]
    if following is None:
        state.update(stage=STAGES[STAGES.index(stage)+1],cursor=None,seen=[])
    return records,state


def convert(repo,stage,row,stamp,state):
    if stage=='heads':
        # The whole point of this stage: 100 pull-request identities per request.
        # Without it every pull costs four requests a generation whether or not
        # anything about it moved, which is what made this server a hot loop.
        head=(row.get('head') or {}).get('sha');base=(row.get('base') or {}).get('sha')
        if head and base:state['heads'][str(row['number'])]=[head,base]
        return None
    if stage=='issues':
        if row.get('pull_request') and row['number'] not in state['pulls']:state['pulls'].append(row['number'])
        return corpus.record(repo,'issue',row,row['number'],stamp)
    parent='issue_url' if stage=='comments' else 'pull_request_url'
    number=row[parent].rstrip('/').rsplit('/',1)[-1]
    category='comment' if stage=='comments' else 'inline discussion'
    return corpus.record(repo,category,row,number,stamp)


def pull_batch(repo,who,token,state,previous=None):
    index=state['pull_index']
    if index>=len(state['pulls']):
        state['stage']='done';return [],state
    number=state['pulls'][index]
    rows=retained(repo,number,state.get('heads') or {},previous)
    if rows is None:rows=list(corpus.pull_records(repo,number,who,token))
    state['pull_index']=index+1
    if state['pull_index']==len(state['pulls']):state['stage']='done'
    return rows,state


def retained(repo,number,heads,previous):
    """The last snapshot's diff and reviews, when this pull has not moved.

    A pull whose head and base are the shas the listing just reported is
    byte-identical to the one already on disk, so refetching it buys nothing
    and costs four GitHub requests. Absent a usable prior record this returns
    None and the caller fetches as before; correctness never depends on the
    cache being there.
    """
    identity=heads.get(str(number))
    if not identity or previous is None:return None
    tagged=[(corpus.identify(row),row) for row in previous.get(f'{repo}#{corpus.github.number(number)}',[])]
    if any(category is None for category,_ in tagged):return None
    rows=[row for category,row in tagged if category in ('diff','review')]
    diffs=[row for category,row in tagged if category=='diff']
    if len(diffs)!=1:return None
    try:body=json.loads(diffs[0]['body'])
    except (ValueError,KeyError):return None
    if [body.get('head'),body.get('base')]!=identity:return None
    return rows

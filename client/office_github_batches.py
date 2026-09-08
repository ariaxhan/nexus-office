"""One resumable GitHub page or one head-bound pull-request snapshot."""
import copy

import office_github_corpus as corpus

STAGES=('issues','comments','inline','pulls','done')


def initial():
    return {'stage':'issues','cursor':None,'seen':[],'pulls':[],'pull_index':0}


def next_batch(access,known,repo,checkpoint):
    state=copy.deepcopy(checkpoint)
    who,token=corpus.github.read_identity(access,repo,known)
    if state['stage']=='pulls':return pull_batch(repo,who,token,state)
    if state['stage']=='done':return [],state
    return page_batch(repo,who,token,state)


def page_batch(repo,who,token,state):
    stage=state['stage']
    endpoints={'issues':'issues?state=all&sort=created&direction=asc','comments':'issues/comments?sort=created&direction=asc','inline':'pulls/comments?sort=created&direction=asc'}
    endpoint=state['cursor'] or f'repos/{repo}/{endpoints[stage]}&per_page=100'
    if endpoint in state['seen']:raise ValueError('GitHub repeated a pagination cursor')
    rows,stamp,following=corpus.github.fetch_page(endpoint,who,token)
    if following in [*state['seen'],endpoint]:raise ValueError('GitHub repeated a pagination cursor')
    state['seen'].append(endpoint);state['cursor']=following
    records=[convert(repo,stage,row,stamp,state) for row in rows]
    if following is None:
        state.update(stage=STAGES[STAGES.index(stage)+1],cursor=None,seen=[])
    return records,state


def convert(repo,stage,row,stamp,state):
    if stage=='issues':
        if row.get('pull_request') and row['number'] not in state['pulls']:state['pulls'].append(row['number'])
        return corpus.record(repo,'issue',row,row['number'],stamp)
    parent='issue_url' if stage=='comments' else 'pull_request_url'
    number=row[parent].rstrip('/').rsplit('/',1)[-1]
    category='comment' if stage=='comments' else 'inline discussion'
    return corpus.record(repo,category,row,number,stamp)


def pull_batch(repo,who,token,state):
    index=state['pull_index']
    if index>=len(state['pulls']):
        state['stage']='done';return [],state
    rows=list(corpus.pull_records(repo,state['pulls'][index],who,token))
    state['pull_index']=index+1
    if state['pull_index']==len(state['pulls']):state['stage']='done'
    return rows,state

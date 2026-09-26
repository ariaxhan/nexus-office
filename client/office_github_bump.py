"""Bump: the source-backed way to put an issue in Tower's queue.

Tower selects an open issue labeled `ready` in a registry row with `enabled && tower_row`,
ordered by p0/p1/p2, and runs it only when `tower_gate` passes. Bump adds `ready` and the
chosen priority label (removing the other priority labels) on GitHub itself, so there is no
Office-only queue. It refuses, with Tower's own reason, whenever Tower would not select it.
"""
import office_github_inventory as inventory

PRIORITIES = ('p0', 'p1', 'p2')


def entry_for(repo):
    rows, error = inventory.registry_rows()
    if error:
        raise ValueError(error)
    return next((row for row in rows if row['repo'] == repo.lower()), None)


def assess(repo, issue, priority='p1'):
    """{'eligible', 'reason', 'eligibility', 'labels_after'} for bumping this issue now."""
    work = inventory._work()
    entry = entry_for(repo)
    labels = [label['name'] if isinstance(label, dict) else label for label in issue.get('labels', [])]
    after = sorted({name for name in labels if name.lower() not in PRIORITIES} | {'ready', priority})
    answer = {'eligible': False, 'eligibility': None, 'labels_after': after, 'tower_repo': bool(entry and entry['tower'])}
    reason = repo_refusal(repo, entry) or (issue.get('state') == 'closed' and 'closed: reopen it before bumping')
    if not reason:
        token = work._lane.set(work.TOWER_LABEL)  # judged as Tower's lane judges it: tower-v2 is Tower's own, not owned
        try:
            answer['eligibility'] = work.eligibility(dict(issue, labels=[{'name': name} for name in labels]))
        finally:
            work._lane.reset(token)
        if answer['eligibility'] in ('held', 'owned'):
            reason = f"{answer['eligibility']}: labels " + ', '.join(labels)
    if not reason:
        _, reason = work.tower_gate(entry, dict(issue, labels=[{'name': name} for name in after]))
    answer['reason'] = reason or ''
    answer['eligible'] = not reason
    return answer


def repo_refusal(repo, entry):
    if repo.lower().startswith(inventory.TBS_ORG.lower() + '/'):
        return 'TBS runs through its own coordinator, not Tower; bump is refused here. Use the TBS coordinator.'
    if entry is None:
        return 'not in the Tower registry'
    if not entry['enabled']:
        return 'disabled in the Tower registry'
    if not entry['tower']:
        return 'not a Tower repository (no risk rules or checkout)'
    return ''


def validate(body):
    if body.get('priority') not in PRIORITIES:
        raise ValueError('Bump needs a priority: p0, p1 or p2')

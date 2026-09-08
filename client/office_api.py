"""Additive mobile routes. Access checks stay at the existing HTTP door."""
from pathlib import Path
import office_github_context as github_context
import urllib.parse

import office_archives as archives
import office_objects as objects
import office_preferences as preferences
import office_media as media
import office_content as content
import office_search as search
import office_system as system
import office_tasks as tasks
import office_github as github
import office_jobs as jobs
import office_uploads as uploads
import office_user_state as user_state
import office_bot_history as bot_history
import office_github_actions as github_actions


def arguments(query):
    return {key: values[0] for key, values in urllib.parse.parse_qs(query).items()}


def get(handler, path, query):
    q = arguments(query)
    search.projection.WORLD = handler.world
    known={row['repo'] for row in (handler.world.snapshot or {}).get('stations',[])}
    if path.startswith('/api/github/'):
        known.update(root['name'] for root in tasks.projects())
    routes = {
        '/api/projects': lambda: {'items':[dict(root,folder_id=objects.encode(root['id'],''),task_capable=(Path(root['path'])/'.git').exists()) for root in objects.roots().values()]},
        '/api/bots/archives': bot_history.listing,
        '/api/bots/history': lambda: bot_history.history(q['id'],q.get('offset',0)),
        '/api/archives': lambda: archives.listing(q.get('cursor',0)),
        '/api/archives/detail': lambda: archives.transcript(q['id'],q.get('offset',0)),
        '/api/archives/messages': lambda: archives.messages(q['id'],q.get('offset',0)),
        '/api/objects': lambda: objects.browse(q.get('id', ''), q.get('cursor', 0)),
        '/api/objects/provenance': lambda: objects.provenance(q['id']),
        '/api/objects/detail': lambda: objects.read(q['id'], q.get('offset', 0)),
        '/api/preferences': preferences.read,
        '/api/user-state': user_state.read,
        '/api/github/collection': lambda: github.collection(handler.world.access(),known,q),
        '/api/github/checks': lambda: github.checks(handler.world.access(),known,q),
        '/api/github/timeline': lambda: github.timeline(handler.world.access(),known,q),
        '/api/github/reviews': lambda: github.reviews(handler.world.access(),known,q),
        '/api/github/detail': lambda: github.detail(handler.world.access(),known,q),
        '/api/github/comments': lambda: github.comments(handler.world.access(),known,q),
        '/api/github/diff': lambda: github.diff_page(handler.world.access(),known,q),
        '/api/github/files': lambda: github.files(handler.world.access(),known,q),
        '/api/github/branches': lambda: github.branches(handler.world.access(),known,q),
        '/api/github/tree': lambda: github.tree(handler.world.access(),known,q),
        '/api/tasks/capabilities': tasks.capabilities,
        '/api/tasks/sources': lambda: tasks.source_choices(q.get('project','')),
        '/api/tasks': lambda: tasks.active_listing() if q.get('active')=='1' else tasks.listing(q.get('cursor',0),q.get('project','')),
        '/api/tasks/permissions': tasks.permissions,
        '/api/tasks/detail': lambda: tasks.detail(q['id']),
        '/api/tasks/history': lambda: tasks.history(q['id'],q.get('cursor',0)),
        '/api/system/jobs': jobs.clock.read,
        '/api/system/job-log': lambda: jobs.logs(q['id'],q.get('offset',0),q.get('version','')),
        '/api/system/job-history': lambda: jobs.receipts(q['id'],q.get('offset',0)),
        '/api/system/machine': system.machine,
        '/api/system/plans': system.plans,
        '/api/system/notifications': lambda: system.notifications(q.get('cursor',0)),
        '/api/system/runs': lambda: system.runs(q.get('cursor',0),q.get('plan',''),q.get('state','')),
        '/api/system/flight': lambda: system.flight(q['id']),
        '/api/system/events': lambda: system.events(q['id'],q.get('cursor',0)),
        '/api/system/log': lambda: system.read_flight_log(q['id'],q.get('offset',0),q.get('lane',''),q.get('version','')),
        '/api/system/log-lanes': lambda: system.log_lanes(q['id']),
        '/api/search/object': lambda: search.object_detail(q['id'],q.get('offset',0),q.get('revision','')),
        '/api/search/all': lambda: search.search(q.get('q',''), q.get('cursor',0), q.get('kind',''), q.get('project','')),
        '/api/media': lambda: media.catalog(q.get('kind', 'all'), q.get('cursor', 0)),
        '/api/media/detail': lambda: media.detail(q['id']),
    }
    if path == '/api/uploads/content':
        uploads.content(handler,{'id':q['id'],'revision':q['revision']})
        return True
    if path == '/api/system/artifact':
        content.serve(handler,system.artifact(q['id']))
        return True
    if path == '/api/objects/content':
        _, resolved = objects.resolve(q['id'])
        content.serve(handler, resolved)
        return True
    if path == '/api/media/content':
        content.serve(handler, media.resolve(q['id']), preview=True)
        return True
    if path not in routes:
        return False
    handler._json(routes[path]())
    return True


def post(handler, path):
    if path in ('/api/github/command','/api/github/reconcile','/api/github/context'):
        known={root['name'] for root in tasks.projects()}|{row['repo'] for row in (handler.world.snapshot or {}).get('stations',[])}
        body=handler._read_json(limit=128*1024)
        if path.endswith('/context'):result=github_context.snapshot(handler.world.access(),known,body)
        elif path.endswith('/reconcile'):result=github_actions.reconcile(handler.world,known,body)
        else:result=github_actions.command(handler.world,known,body,handler.github_sync)
        handler._json(result)
        return True
    routes = {'/api/user-state': user_state.save, '/api/uploads': uploads.upload, '/api/objects/diff': objects.diff, '/api/system/command': system.command, '/api/objects/save': objects.save, '/api/preferences': preferences.save,
              '/api/tasks/start': tasks.start, '/api/tasks/say': tasks.say,
              '/api/tasks/control': tasks.control, '/api/tasks/answer': tasks.answer}
    if path not in routes:
        return False
    handler._json(routes[path](handler._read_json(limit=7 * 1024 * 1024)), 202 if path.startswith('/api/tasks/') else 200)
    return True

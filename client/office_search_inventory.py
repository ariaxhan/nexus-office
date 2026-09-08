"""Declared source observations committed with each search generation."""
from pathlib import Path
import os

import office_archives as archives
import office_bot_history as bots
import office_objects as objects
import office_projection as projection


def declared(roots):
    rows=[(root['name'],'file',[Path(root['path'])]) for root in roots]
    histories={}
    for engine,profile,path in archives.locations():
        histories.setdefault(profile+' '+engine,set()).add(path)
    rows.extend((name,'conversation',list(paths)) for name,paths in histories.items())
    base=objects.vault()
    rows.extend([
        ('Office voices','conversation',[bots.root()]),
        ('Library','podcast',[base/'_meta/podcasts/manifest.json']),
        ('Library','substrate',[base/'_meta/services/taper-site/pieces/manifest.json']),
        ('Nexus','task',[projection.office_system.run_board.LEDGER]),
        ('System / Nexus','log',[projection.office_system.run_board.LEDGER]),
        ('System / jobs','log',[base/projection.office_jobs.clock.REGISTRY]),
    ])
    return [{'source':name,'kind':kind,'paths':[str(path) for path in paths],
             'available':any(path.exists() and os.access(path,os.R_OK) for path in paths)}
            for name,kind,paths in rows]


def faults(source,errors):
    labels=[source['source'],*source['paths']]
    if source['kind']=='conversation':labels.append('Native histories')
    if source['source']=='System / Nexus':labels.extend(('Nexus ledger','Nexus logs'))
    if source['source']=='Library':labels.extend(('media',source['kind']+':','unavailable:'))
    if source['source']=='Office voices':labels.append('Bot histories')
    if source['source']=='Nexus':labels.append('Nexus ledger')
    if source['source']=='System / jobs':labels.extend(('Job registry','Job logs'))
    return [error for error in errors if any(str(error.get('source','')).startswith(label) or str(error.get('path','')).startswith(label) for label in labels)]


def complete(rows,inventory,status):
    result=[dict(row) for row in rows]
    groups={(row['source'],row['kind']) for row in rows}
    for source in inventory:
        errors=faults(source,status.get('errors',[]))
        if (source['source'],source['kind']) in groups:
            mark_errors(result,source,errors)
            continue
        state='unavailable' if not source['available'] else 'empty'
        if errors:state='error'
        result.append({'source':source['source'],'kind':source['kind'],'indexed':0,
                       'total':0 if state=='empty' else None,'state':state,
                       'coverage':'Checked; no retained objects' if state=='empty' else 'Source unavailable or unreadable',
                       'errors':errors,'observed_at':status['finished_at']})
    return result


def mark_errors(rows,source,errors):
    if not errors:return
    for row in rows:
        if (row['source'],row['kind'])==(source['source'],source['kind']):
            row.update(state='partial',errors=errors)


def unbuilt(sources):
    return [{'source':source['source'],'kind':source['kind'],'indexed':0,'total':None,
             'state':'unbuilt','coverage':'Not yet scanned','observed_at':None} for source in sources]

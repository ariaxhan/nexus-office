"""Phone-readable catalogs over existing podcast and Substrate artifacts."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
from html import unescape
import urllib.parse

import office_objects as objects


def text(raw):
    raw = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', raw, flags=re.S | re.I)
    return re.sub(r'\s+', ' ', unescape(re.sub('<[^>]+>', ' ', raw))).strip()


def media_id(kind, relative):
    return kind + ':' + relative


def resolve(identifier):
    kind, _, relative = identifier.partition(':')
    locations = {'podcast': objects.vault() / '_meta/podcasts',
                 'substrate': objects.vault() / '_meta/services/taper-site/pieces'}
    base = locations.get(kind)
    if base is None or not objects.permitted(relative):
        raise PermissionError('Unknown media item')
    path = base
    for part in Path(relative).parts:
        path /= part
        if path.is_symlink():
            raise PermissionError('Linked media is not served')
    path.resolve(strict=True).relative_to(base.resolve())
    if not path.is_file():
        raise FileNotFoundError('Media file is missing')
    return path


def url(identifier):
    return '/api/media/content?id=' + urllib.parse.quote(identifier, safe='')


def podcasts():
    base = objects.vault() / '_meta/podcasts'
    manifest = base / 'manifest.json'
    if not manifest.exists():
        return []
    data = json.loads(manifest.read_text())
    rows = []
    for item in data.get('episodes', []):
        try:
            relative = Path(item['audio_path']).relative_to(base).as_posix()
            identifier = media_id('podcast', relative)
            path = resolve(identifier)
        except (KeyError, OSError, ValueError, PermissionError) as exc:
            rows.append(dict(item,id='unavailable:'+str(item.get('id','episode')),kind='podcast',state='unavailable',error=str(exc)[:200]))
            continue
        row = dict(item, id=identifier, kind='podcast', url=url(identifier), bytes=path.stat().st_size)
        row.pop('audio_path', None)
        rows.append(row)
    return rows


def substrate():
    base = objects.vault() / '_meta/services/taper-site/pieces'
    manifest = base / 'manifest.json'
    if not manifest.exists():
        return []
    rows = []
    for item in json.loads(manifest.read_text()).get('pieces', []):
        identifier = media_id('substrate', item.get('file', ''))
        try:
            path = resolve(identifier)
            raw = path.read_text(errors='replace')
        except (OSError, ValueError, PermissionError) as exc:
            rows.append(dict(item,id=identifier,kind='substrate',state='unavailable',error=str(exc)[:200]))
            continue
        title = re.search(r'<title[^>]*>(.*?)</title>', raw, re.S | re.I)
        rows.append(dict(item, id=identifier, kind='substrate', url=url(identifier),
                         title=text(title.group(1)) if title else path.stem,
                         excerpt=text(raw)[:200]))
    return sorted(rows, key=lambda r: r.get('date', ''), reverse=True)


def catalog(kind='all', cursor=0):
    loaders = {'podcast': podcasts, 'substrate': substrate}
    rows, errors = [], []
    selected = loaders if kind == 'all' else {kind: loaders[kind]}
    for key, loader in selected.items():
        try:
            rows.extend(loader())
        except (OSError, ValueError) as error:
            errors.append({'source': key, 'error': str(error)})
    errors.extend({'source':row['id'],'error':row['error']} for row in rows if row.get('state')=='unavailable')
    rows.sort(key=lambda r: r.get('date', ''), reverse=True)
    start = max(0, int(cursor)); end = start + 40
    return {'items': rows[start:end], 'total': len(rows), 'errors': errors,
            'next_cursor': end if end < len(rows) else None}


def detail(identifier,include_provenance=True):
    path = resolve(identifier)
    if identifier.startswith('substrate:'):
        raw = path.read_text(errors='replace')
        piece=next((row for row in substrate() if row['id']==identifier),None)
        if piece is None:raise FileNotFoundError('Piece is not published in the Substrate catalog')
        return dict(piece,text=text(raw),provenance=poem_provenance(path,piece) if include_provenance else None)
    episode = next((r for r in podcasts() if r['id'] == identifier), None)
    if episode is None:
        raise FileNotFoundError('Episode is not published')
    script = Path(episode.get('script_path', str(path.with_suffix('.txt'))))
    relative=script.relative_to(objects.vault()/'_meta/podcasts').as_posix()
    if script.exists():script=resolve(media_id('podcast',relative))
    episode['text'] = script.read_text(errors='replace') if script.is_file() else ''
    return episode


def meta_reference(relative):
    base=objects.vault()/'_meta'
    identifier=objects.encode(hashlib.sha256(str(base.resolve()).encode()).hexdigest()[:16],relative)
    objects.resolve(identifier)
    return identifier


def poem_provenance(path,piece):
    base=objects.vault()/'_meta/services/taper-site'
    result={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'catalogued_at':(base/'pieces/manifest.json').stat().st_mtime,
            'source':meta_reference('services/taper-site/pieces/'+path.relative_to(base/'pieces').as_posix()),
            'manifest':meta_reference('services/taper-site/pieces/manifest.json'),
            'generation_receipt':'No exact generating-run receipt recorded in this catalog.',
            'publication':'Present in the Mac catalog; remote deployment is not verified by this record.'}
    try:
        registry=json.loads((objects.vault()/'_meta/services/registry.json').read_text())
        result['configured_producers']=[row['id'] for row in registry.get('jobs',[]) if piece.get('type') and row.get('taper_slug')==piece['type']]
        result['pipeline']=meta_reference('services/taper-site/README.md')
    except (OSError,ValueError) as exc:result['pipeline_error']=str(exc)[:200]
    try:
        proc=subprocess.run(['git','-C',str(objects.vault()),'log','-1','--format=%H%n%cI%n%s','--',str(path)],capture_output=True,text=True,timeout=5)
        fields=proc.stdout.strip().split('\n',2)
        if proc.returncode==0 and len(fields)==3:result['source_commit']=dict(zip(('sha','at','subject'),fields))
    except (OSError,subprocess.TimeoutExpired):result['source_commit_error']='Source history unavailable'
    return result

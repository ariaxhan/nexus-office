"""Validated, atomic publication into the existing private podcast library."""
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
from datetime import datetime,timezone


def duration(path):
    result=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(path)],capture_output=True,text=True,timeout=30,check=True)
    return float(json.loads(result.stdout)['format']['duration'])


def validate(audio,script):
    if not audio.is_file() or not script.is_file():raise FileNotFoundError('Audio and script are required')
    seconds=duration(audio)
    if not 1200<=seconds<=1800:raise ValueError(f'Episode is {seconds/60:.1f} minutes; required 20–30')
    subprocess.run(['ffmpeg','-v','error','-i',str(audio),'-f','null','-'],capture_output=True,timeout=180,check=True)
    words=len(script.read_text().split())
    if not 3500<=words<=5000:raise ValueError('Spoken script is outside the substantial-episode word budget')
    checked={'duration_s':round(seconds,3),'word_count':words,'audio_sha256':hashlib.sha256(audio.read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(script.read_bytes()).hexdigest()}
    quality=json.loads((audio.parent/'quality.json').read_text())
    if quality.get('passed') is not True:
        raise ValueError('Audio quality checks have not passed')
    if any(quality.get(key)!=checked[key] for key in ('audio_sha256','script_sha256')):
        raise ValueError('Audio or script changed after quality checks')
    return checked


def atomic_json(path,data):
    temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w') as stream:
        json.dump(data,stream,indent=2,ensure_ascii=False);stream.write('\n');stream.flush()
        import os
        os.fsync(stream.fileno())
    temporary.replace(path)


def publish(root,editorial,audio,script,chapters,date,episode_type='office-daily'):
    root=Path(root).resolve();audio=Path(audio).resolve();script=Path(script).resolve()
    audio.relative_to(root);script.relative_to(root)
    checked=validate(audio,script)
    if not chapters or chapters[0]['start_s']!=0:raise ValueError('Chapter timeline must start at zero')
    starts=[float(ch['start_s']) for ch in chapters]
    if starts!=sorted(set(starts)) or starts[-1]>=checked['duration_s']:raise ValueError('Invalid chapter timeline')
    now=datetime.now(timezone.utc).isoformat()
    episode={'id':date+'/'+episode_type,'date':date,'type':episode_type,'title':editorial['title'],
             'description':editorial['description'],'audio_path':str(audio),'script_path':str(script),
             'chapters':chapters,'sources':editorial['sources'],'voice':'qwen-wiry-professor',
             'model':'mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit','speed':1.0,'generated_at':now,**checked}
    manifest=root/'manifest.json'
    with (root/'.publish.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        data=json.loads(manifest.read_text()) if manifest.exists() else {'version':1,'episodes':[]}
        previous=next((row for row in data['episodes'] if row['id']==episode['id']),None)
        if previous:
            if any(previous.get(key)!=episode[key] for key in ('audio_sha256','script_sha256')):
                raise FileExistsError('This edition already has different published audio or text')
            return previous
        data['episodes'].insert(0,episode);data['updated_at']=now
        atomic_json(manifest,data)
    atomic_json(audio.parent/'published.json',episode)
    return episode

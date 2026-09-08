"""Align independent passage transcriptions; reject missing narration before publish."""
import difflib
import hashlib
import json
import re


def words(text):
    return re.findall(r'[a-z0-9]+',text.lower())


def transcribe(directory):
    import mlx_whisper
    manifest=json.loads((directory/'episode.json').read_text());segments=[];offset=0
    for chunk in manifest['chunks']:
        index=chunk['index'];audio=directory/f'episode-chunks/{index:03}.wav'
        checksum=hashlib.sha256(audio.read_bytes()).hexdigest();saved=directory/f'passage-{index}-verified.json'
        prior=json.loads(saved.read_text()) if saved.exists() else {}
        if prior.get('audio_sha256')==checksum:
            result=prior['result']
        else:
            result=mlx_whisper.transcribe(str(audio),path_or_hf_repo='mlx-community/whisper-large-v3-turbo',
                                         language='en',word_timestamps=True,condition_on_previous_text=False,verbose=False)
            saved.write_text(json.dumps({'audio_sha256':checksum,'result':result}))
        for segment in result['segments']:
            for word in segment.get('words',[]):
                segments.append({'word':word['word'],'start':word['start']+offset})
        offset+=chunk['duration_s']+.2
    return segments


def alignment(editorial,recognized):
    spoken=[];starts=[]
    for chapter in editorial['chapters']:
        starts.append(len(spoken));spoken.extend(words(chapter['text']))
    heard=[];times=[]
    for word in recognized:
        tokens=words(word['word']);heard.extend(tokens);times.extend([word['start']]*len(tokens))
    matcher=difflib.SequenceMatcher(None,spoken,heard,autojunk=False)
    blocks=matcher.get_matching_blocks()
    coverage=sum(b.size for b in blocks)/max(1,len(spoken))
    misses=[{'script':' '.join(spoken[a:b]),'heard':' '.join(heard[c:d])}
            for kind,a,b,c,d in matcher.get_opcodes() if kind!='equal' and max(b-a,d-c)>12]
    chapters=boundaries(editorial,starts,blocks,times)
    return {'word_coverage':coverage,'long_mismatches':misses,'chapters':chapters,'passed':coverage>=.95 and not misses}


def boundaries(editorial,starts,blocks,times):
    chapters=[]
    for index,start in enumerate(starts):
        match=next((b for b in blocks if b.size and b.a<=start<b.a+b.size),None)
        if match:position=match.b+start-match.a
        else:
            match=next((b for b in blocks if b.size and b.a>start),None)
            if match is None or match.a-start>12:raise ValueError('Chapter boundary cannot be aligned reliably')
            position=match.b
        chapters.append({'title':editorial['chapters'][index]['title'],'start_s':0 if index==0 else round(times[position],2)})
    return chapters


def verify(directory):
    editorial=json.loads((directory/'editorial.json').read_text())
    result=alignment(editorial,transcribe(directory))
    result.update(audio_sha256=hashlib.sha256((directory/'episode.mp3').read_bytes()).hexdigest(),
                  script_sha256=hashlib.sha256((directory/'script.txt').read_bytes()).hexdigest())
    (directory/'quality.json').write_text(json.dumps(result,indent=2)+'\n')
    (directory/'chapters.json').write_text(json.dumps(result['chapters'],indent=2)+'\n')
    if not result['passed']:raise ValueError('Narration alignment failed; publication held for repair')
    return result


def failed_passages(directory):
    bad=[];scores=[]
    manifest=json.loads((directory/'episode.json').read_text())
    for chunk in manifest['chunks']:
        index=chunk['index'];script=(directory/f'episode-chunks/{index:03}.txt').read_text()
        recognized=json.loads((directory/f'passage-{index}-verified.json').read_text())['result']['text']
        expected,heard=words(script),words(recognized)
        match=difflib.SequenceMatcher(None,expected,heard,autojunk=False)
        coverage=sum(block.size for block in match.get_matching_blocks())/max(1,len(expected))
        gaps=[max(b-a,d-c) for kind,a,b,c,d in match.get_opcodes() if kind!='equal']
        scores.append((coverage,index))
        if coverage<.90 or max(gaps or [0])>12:bad.append(index)
    return bad or [index for _,index in sorted(scores)[:3]]

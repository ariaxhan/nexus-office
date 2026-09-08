"""Resumable Qwen passages in the approved voice, at its natural speed."""
import hashlib
import json
from pathlib import Path

MODEL='mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit'


def passages(text):
    result=[];pending=[];count=0
    for paragraph in text.split('\n\n'):
        paragraph=paragraph.strip()
        if not paragraph:continue
        size=len(paragraph.split())
        if pending and count+size>125:
            result.append('\n\n'.join(pending));pending=[];count=0
        pending.append(paragraph);count+=size
    if pending:result.append('\n\n'.join(pending))
    return result


def render(directory,voice):
    import mlx.core as mx
    import numpy as np
    import soundfile as sf
    from mlx_audio.tts.utils import load_model
    chunks=directory/'episode-chunks';chunks.mkdir(exist_ok=True)
    texts=passages((directory/'script.txt').read_text())
    seeds=json.loads((directory/'render-seeds.json').read_text()) if (directory/'render-seeds.json').exists() else {}
    model=None;sample_rate=None;rows=[];audio=[]
    for index,text in enumerate(texts):
        target=chunks/f'{index:03}.wav';script=target.with_suffix('.txt')
        if not target.exists() or not script.exists() or script.read_text().strip()!=text:
            if model is None:model=load_model(MODEL)
            mx.random.seed(137+index+1009*seeds.get(str(index),0))
            results=list(model.generate(text=text,ref_audio=str(voice/'qwen-wiry-professor.wav'),
                         ref_text=(voice/'wiry-professor-audition.txt').read_text().strip(),
                         lang_code='English',temperature=.75,max_tokens=1800,verbose=False))
            segment=np.concatenate([np.asarray(result.audio) for result in results])
            sample_rate=results[0].sample_rate
            temporary=target.with_suffix('.tmp.wav');sf.write(temporary,segment,sample_rate);temporary.replace(target)
            script.write_text(text+'\n');mx.clear_cache()
        segment,rate=sf.read(target,dtype='float32')
        if sample_rate is not None and rate!=sample_rate:raise ValueError('Passage sample rates differ')
        sample_rate=rate;audio.extend([segment,np.zeros(int(.2*rate),dtype=np.float32)])
        rows.append({'index':index,'words':len(text.split()),'duration_s':len(segment)/rate})
        print(f'Passage {index+1}/{len(texts)} ready',flush=True)
    combined=np.concatenate(audio);temporary=directory/'episode.tmp.wav'
    sf.write(temporary,combined,sample_rate)
    manifest={'model':MODEL,'speed':1.0,'sample_rate':sample_rate,'duration_s':len(combined)/sample_rate,'chunks':rows}
    (directory/'episode.json').write_text(json.dumps(manifest,indent=2)+'\n')
    temporary.replace(directory/'episode.wav')


def repair_passages(directory,voice,indices):
    from .podcast_publish import atomic_json
    seed_file=directory/'render-seeds.json'
    seeds=json.loads(seed_file.read_text()) if seed_file.exists() else {}
    if any(seeds.get(str(index),0)>=2 for index in indices):
        raise ValueError('Narration repair budget exhausted; publication remains held')
    archive=directory/'repair-attempts';archive.mkdir(exist_ok=True)
    for index in indices:
        attempt=seeds.get(str(index),0)+1;seeds[str(index)]=attempt
        source=directory/f'episode-chunks/{index:03}.wav'
        atomic_json(seed_file,seeds)
        if source.exists():source.replace(archive/f'{index:03}-attempt-{attempt}.wav')
    render(directory,voice)

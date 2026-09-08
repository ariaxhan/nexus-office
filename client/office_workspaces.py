"""All checkout identities within Office's existing discovery boundary."""
from collections import deque
from pathlib import Path
import time
import sessions

CACHE={}


def discover(base):
    cached=CACHE.get(str(base))
    if cached and time.time()-cached[0]<120:return cached[1]
    rows=[];queue=deque([(base,0)])
    while queue:
        folder,depth=queue.popleft()
        if (folder/'.git').exists():
            origin=sessions.origin_nwo(str(folder))
            rows.append((origin or 'Local / '+folder.name,folder))
        if depth>sessions.WALK_DEPTH:continue
        try:children=sorted(folder.iterdir())
        except OSError:continue
        for path in children:
            if path.name.startswith('.') or path.name in sessions.WALK_SKIP or path.is_symlink():continue
            if path.is_dir():queue.append((path,depth+1))
    CACHE[str(base)]=(time.time(),rows)
    return rows

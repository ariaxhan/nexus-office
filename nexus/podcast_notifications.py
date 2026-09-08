"""Retryable Office inbox delivery; never regenerates or republishes audio."""
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time

from .ledger import Ledger
from .podcast_publish import atomic_json


def deliver(ledger,root,start_date):
    manifest=json.loads((root/'manifest.json').read_text());receipts=[]
    owner=ledger.plan_by_name('office-podcast-notifications')
    started_at=owner['created_at'] if owner else None
    for episode in manifest.get('episodes',[]):
        if not eligible(episode,start_date,started_at):continue
        relative=Path(episode['audio_path']).relative_to(root).as_posix()
        payload={'edition_id':episode['id'],'title':episode['title'],'media_id':'podcast:'+relative,
                 'duration_s':episode['duration_s'],'date':episode['date']}
        with ledger.tx():
            prior=ledger.conn.execute("SELECT id FROM events WHERE kind='office.podcast_ready' AND subject=? LIMIT 1",(episode['id'],)).fetchone()
            if prior:
                receipts.append({'edition_id':episode['id'],'event_id':prior['id'],'state':'already-delivered'});continue
            ledger._event('office.podcast_ready',episode['id'],payload,'podcast-notifier')
            receipts.append({'edition_id':episode['id'],'event_id':ledger.conn.execute('SELECT last_insert_rowid()').fetchone()[0],'state':'delivered'})
    return {'notifications':receipts,'observed_at':time.time()}


def main():
    root=Path(os.environ['OFFICE_RUNTIME_ROOT']).resolve()/'_meta/podcasts'
    ledger_path=os.environ.get('OFFICE_NEXUS_LEDGER') or os.environ['NEXUS_LEDGER']
    with closing(Ledger(ledger_path)) as ledger:
        result=deliver(ledger,root,sys.argv[1])
    atomic_json(Path.cwd()/'notification-receipt.json',result)
    return 0




def eligible(episode,start_date,started_at):
    if episode.get('date','')>=start_date:return True
    if started_at is None:return False
    stamp=episode.get('generated_at','')
    if not isinstance(stamp,str):return False
    try:published=datetime.fromisoformat(stamp.replace('Z','+00:00'))
    except (ValueError,TypeError):return False
    if published.tzinfo is None:return False
    return published.timestamp()>=started_at


if __name__=='__main__':raise SystemExit(main())

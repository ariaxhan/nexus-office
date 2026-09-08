from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from nexus.ledger import Ledger
from nexus import podcast_notifications as notifications,podcast_daily

class Notifications(unittest.TestCase):
    def test_retry_delivers_once_without_touching_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);audio=root/'episode.mp3';audio.write_bytes(b'fixture bytes')
            episode={'id':'2026-09-08/office-daily','date':'2026-09-08','title':'Fixture','audio_path':str(audio),'duration_s':1500}
            (root/'manifest.json').write_text(json.dumps({'episodes':[episode]}))
            with closing(Ledger(str(root/'ledger.sqlite'))) as ledger:
                first=notifications.deliver(ledger,root,'2026-09-08');second=notifications.deliver(ledger,root,'2026-09-08')
                self.assertEqual(first['notifications'][0]['event_id'],second['notifications'][0]['event_id'])
                self.assertEqual(len(ledger.events(kind='office.podcast_ready')),1)
                self.assertEqual(audio.read_bytes(),b'fixture bytes')

    def test_install_is_single_owner_and_preserves_user_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ledger_path=str(root/'ledger.sqlite')
            plan=podcast_daily.install(ledger_path,root)
            with closing(Ledger(ledger_path)) as ledger:ledger.set_plan_enabled(plan,False)
            self.assertEqual(podcast_daily.install(ledger_path,root),plan)
            with closing(Ledger(ledger_path)) as ledger:
                self.assertEqual(len(ledger.plans()),2)
                self.assertFalse(ledger.plan(plan)['enabled'])
                self.assertEqual(json.loads(ledger.plan(plan)['schedule']),{'at':'06:00'})

    def test_late_publication_of_older_edition_is_delivered(self):
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);audio=root/'episode.mp3';audio.write_bytes(b'fixture')
            with closing(Ledger(str(root/'ledger.sqlite'))) as ledger:
                plan=ledger.add_plan('office-podcast-notifications')
                started=ledger.plan(plan)['created_at']
                episode={'id':'2026-09-07/office-daily','date':'2026-09-07','title':'Late edition','audio_path':str(audio),'duration_s':1500,'generated_at':datetime.fromtimestamp(started+1,timezone.utc).isoformat()}
                old=dict(episode,id='old',generated_at=datetime.fromtimestamp(started-1,timezone.utc).isoformat())
                (root/'manifest.json').write_text(json.dumps({'episodes':[episode,old]}))
                first=notifications.deliver(ledger,root,'2026-09-08');second=notifications.deliver(ledger,root,'2026-09-08')
                self.assertEqual([r['edition_id'] for r in first['notifications']],['2026-09-07/office-daily'])
                self.assertEqual(second['notifications'][0]['state'],'already-delivered')

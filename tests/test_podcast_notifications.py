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

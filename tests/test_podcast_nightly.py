from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from nexus import podcast_daily as daily
from nexus.ledger import Ledger


class NightlyPodcast(unittest.TestCase):
    def test_now_changes_existing_morning_schedule_and_queues_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); database=str(root/'ledger.sqlite')
            plan=daily.install(database,root)
            with closing(Ledger(database)) as ledger:
                ledger.conn.execute('UPDATE plans SET schedule=?,enabled=0 WHERE id=?',
                                    (json.dumps({'at':'06:00'}),plan))
                ledger.conn.commit()
            with patch.object(daily,'install_from_environment',return_value=plan), patch.dict('os.environ',{'OFFICE_NEXUS_LEDGER':database}):
                first=daily.schedule_nightly_now()
                self.assertEqual(daily.schedule_nightly_now(),first)
            with closing(Ledger(database)) as ledger:
                self.assertEqual(json.loads(ledger.plan(plan)['schedule']),{'at':'21:00'})
                self.assertTrue(ledger.plan(plan)['enabled'])
                self.assertEqual(ledger.task(first)['plan_id'],plan)

    def test_old_daily_edition_does_not_suppress_nightly_production(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            old={'id':'2026-09-08/office-daily'}
            (root/'manifest.json').write_text(json.dumps({'episodes':[old]}))
            with patch.object(daily,'write_editorial',side_effect=RuntimeError('writer reached')) as writer:
                with self.assertRaisesRegex(RuntimeError,'writer reached'):
                    daily.produce(root,'2026-09-08')
                writer.assert_called_once_with(root/'2026-09-08/office-nightly','2026-09-08',root)
            self.assertEqual(json.loads((root/'manifest.json').read_text())['episodes'],[old])

    def test_evidence_only_uses_requested_days_emails(self):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary);root=base/'podcasts';root.mkdir();logs=base/'logs';logs.mkdir()
            (logs/'email-morning-2026-09-08.html').write_text('today')
            (logs/'email-morning-2026-09-07.html').write_text('yesterday')
            result=daily.today_evidence(root,'2026-09-08')
            self.assertIn('today',result);self.assertNotIn('yesterday',result)

    def test_length_repair_keeps_reviewed_draft_and_accepts_measured_words(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            too_long=json.dumps({'chapters':[{'text':'word '*4902}]})
            fitted=json.dumps({'chapters':[{'text':'word '*4300}]})
            (root/'editorial-reviewed.json').write_text(too_long)
            with patch.object(daily,'editorial_pass',return_value=fitted) as writer:
                self.assertEqual(daily.reviewed_editorial(root,'',{},'','2026-09-08'),too_long)
                writer.assert_not_called()
                self.assertEqual(daily.fit_editorial(root,too_long,{},''),fitted)
                writer.assert_called_once()
            self.assertEqual((root/'editorial-reviewed.json').read_text(),too_long)

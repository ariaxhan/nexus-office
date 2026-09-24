"""Office Watch uses Tower receipts and consumes each steering command once."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nexus.ledger import Ledger
from nexus import office_coordinator as office


class OfficeCoordinatorTest(unittest.TestCase):
    def test_priority_message_has_queued_read_and_acted_receipts(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'NEXUS_LEDGER': str(Path(folder) / 'ledger.sqlite')}):
            ledger = Ledger(office.default_path())
            try:
                with ledger.tx():
                    ledger.conn.execute("INSERT INTO tasks(id,origin,title,state,dedupe_key,created_at) VALUES ('issue','github-work','A defect','accepted',?,1)",
                                        (f'github:{office.REPO}#182',))
                body = {'id': 'office-182', 'text': 'prioritize #182'}
                self.assertFalse(office.say(body)['duplicate'])
                self.assertTrue(office.say(body)['duplicate'])
                self.assertIsNone(office.read()['items'][0]['read_at'])
                office.receive(ledger)
                office.receive(ledger)
                item = office.read()['items'][0]
                self.assertEqual(item['action'], 'prioritized #182')
                self.assertIsNotNone(item['read_at'])
                self.assertIsNotNone(item['acted_at'])
                self.assertEqual(len(ledger.events(kind='office.coordinator.priority', subject=f'github:{office.REPO}#182')), 1)
            finally:
                ledger.close()


if __name__ == '__main__':
    unittest.main()

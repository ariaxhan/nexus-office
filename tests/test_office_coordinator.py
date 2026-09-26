"""Office Watch uses Tower receipts and consumes each steering command once."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nexus.ledger import Ledger
from nexus import office_coordinator as office
from nexus import work


class OfficeCoordinatorTest(unittest.TestCase):
    def test_issue_failure_is_not_hidden_by_successful_empty_poll(self):
        plan = {'enabled': 1, 'quarantined_at': None}
        cycle = {'state': 'produced', 'created_at': 20}
        failure = {'state': 'failed', 'created_at': 10}
        self.assertEqual(office._health(plan, [cycle], [failure], None, 10, True), 'failing')
        self.assertEqual(office._health(plan, [cycle], [failure], None, 10, False), 'idle')

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
                self.assertEqual(work.office_priority(ledger, f'github:{office.REPO}#182', 3), -1)
            finally:
                ledger.close()


if __name__ == '__main__':
    unittest.main()

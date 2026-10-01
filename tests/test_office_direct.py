"""A direct issue conversation reserves one issue and stays available for follow-ups."""
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'client'))

from nexus.ledger import Ledger
from nexus.office_agent import Conversation
import office_direct


class DirectIssueConversation(unittest.TestCase):
    def test_reservation_start_replay_and_followup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = {'id': 'project-1', 'name': 'jessstrom/matra', 'path': str(root), 'label': 'Matra'}
            (root / '.git').mkdir()
            labels = set()
            writes = []

            def github_request(endpoint, method, payload, token):
                if endpoint.endswith('/issues/42/labels/hold') and method == 'DELETE':
                    labels.discard('hold')
                    return {}
                if endpoint.endswith('/issues/42'):
                    return {'number': 42, 'state': 'open', 'labels': [{'name': name} for name in labels]}
                if endpoint.endswith('/labels/hold'):
                    return {'name': 'hold'}
                raise AssertionError((endpoint, method))

            def github_command(world, known, body, sync):
                writes.append(body)
                labels.add('hold')
                return {'state': 'confirmed'}

            class World:
                def access(self):
                    return {}

            body = {'request_id': 'direct-request-12345', 'issue': {'repo': 'jessstrom/matra', 'number': 42},
                    'project': project['id'], 'engine': 'codex', 'profile': 'personal',
                    'prompt': 'What remains to fix the crackling?'}
            with patch.object(office_direct.run_board, 'LEDGER', root / 'ledger.sqlite'), \
                    patch.object(office_direct.tasks, 'projects', return_value=[project]), \
                    patch.object(office_direct.tasks.objects, 'vault', return_value=root), \
                    patch.object(office_direct.tasks, 'source_revision', return_value='a' * 40), \
                    patch.object(office_direct.profiles, 'require'), \
                    patch.object(office_direct.github, 'fresh_access', return_value=('test', 'token')), \
                    patch.object(office_direct.github, 'request', side_effect=github_request), \
                    patch.object(office_direct.github, 'command', side_effect=github_command), \
                    patch.object(office_direct, '_coordinator_idle'):
                first = office_direct.start(body, World())
                replay = office_direct.start(body, World())
                self.assertEqual(first['task_id'], replay['task_id'])
                self.assertEqual(len(writes), 1)
                self.assertEqual(office_direct.status('jessstrom/matra', 42)['conversation']['task_id'], first['task_id'])
                followup = office_direct.start({**body, 'request_id': 'direct-followup-12345',
                    'prompt': 'Please implement the remaining fix.'}, World())
                self.assertEqual(followup['task_id'], first['task_id'])
                with closing(Ledger(str(root / 'ledger.sqlite'))) as ledger:
                    messages = ledger.conn.execute("SELECT count(*) FROM events WHERE subject=? AND kind='office.message'",
                                                   (first['task_id'],)).fetchone()[0]
                    self.assertEqual(messages, 2)
                    spec = office_direct.task_ledger.specification(ledger, first['task_id'])
                    conversation = Conversation(ledger, {'id': 'flight', 'task_id': first['task_id']},
                                                root, root, spec)
                    conversation.save_outputs = lambda: None
                    conversation.provider({'method': 'turn/completed', 'params': {}})
                    self.assertFalse(conversation.closed)
                    phase = ledger.conn.execute("SELECT payload FROM events WHERE subject=? AND kind='office.phase' ORDER BY id DESC LIMIT 1",
                                                (first['task_id'],)).fetchone()['payload']
                    self.assertIn('listening', phase)
                with self.assertRaises(FileExistsError):
                    office_direct.release({'task_id': first['task_id']}, World())
                with closing(Ledger(str(root / 'ledger.sqlite'))) as ledger:
                    with ledger.tx():
                        ledger.conn.execute("UPDATE flights SET state='done' WHERE task_id=?", (first['task_id'],))
                self.assertTrue(office_direct.release({'task_id': first['task_id']}, World())['released'])
                self.assertNotIn('hold', labels)
                self.assertIsNone(office_direct.status('jessstrom/matra', 42)['conversation'])


if __name__ == '__main__':
    unittest.main()

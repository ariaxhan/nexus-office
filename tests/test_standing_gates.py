'Issue #306 (pd-11): checkable standing rules are gates in code, not prose.\n\nEach test drives the banned shape down its real path and watches it refuse:\na filename Aria was already sent, a body-bearing send by any route but the raw\ndraft, and a security-class item on its way to the board.\n'
import contextlib
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'client'))
from nexus import mail, standing_gates
import buzz
import office_feed_runner as feed_runner


class FakeGmail:
    """The two Gmail calls the raw-draft route makes, recorded."""
    def __init__(self):
        self.calls = []
        self.searches = []
        self.history = []
        self._listing = False

    def users(self):
        return self

    def drafts(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, maxResults):
        self.searches.append(q)
        self._listing = True
        return self

    def create(self, userId, body):
        self.calls.append(('create', body))
        return self

    def send(self, userId, body):
        self.calls.append(('send', body))
        return self

    def execute(self):
        if self._listing:
            self._listing = False
            return {'messages': self.history}
        return {'id': f'd{len(self.calls)}', 'message': {'id': 'm1'}}


class AttachmentFreshness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.ledger = self.dir / 'sent.json'
        self.body = self.dir / 'body.txt'
        self.body.write_text('Hi Aria,\n\nInvoice attached.\n')
        self.pdf = self.dir / 'invoice-003.pdf'
        self.pdf.write_bytes(b'%PDF-1.4 v1')

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, attach):
        gmail = FakeGmail()
        result = mail.send('ariaxhan@gmail.com', 'Invoice 003', self.body, attach,
                           service=gmail, ledger=self.ledger)
        return gmail, result

    def test_a_filename_already_sent_to_aria_is_refused_before_any_gmail_call(self):
        self.send([self.pdf])
        self.pdf.write_bytes(b'%PDF-1.4 v2 edited')
        gmail = FakeGmail()
        with self.assertRaises(standing_gates.StaleAttachment) as caught:
            mail.send('ariaxhan@gmail.com', 'Invoice 003 (fixed)', self.body, [self.pdf],
                      service=gmail, ledger=self.ledger)
        self.assertIn('invoice-003.pdf', str(caught.exception))
        self.assertEqual(gmail.calls, [])

    def test_a_renamed_edit_goes_out(self):
        self.send([self.pdf])
        renamed = self.dir / 'invoice-003-v2.pdf'
        self.pdf.rename(renamed)
        gmail, _ = self.send([renamed])
        self.assertEqual([c[0] for c in gmail.calls], ['create', 'send'])

    def test_historical_gmail_sent_filename_is_refused_before_draft_creation(self):
        gmail = FakeGmail()
        gmail.history = [{'id': 'older-message'}]
        with self.assertRaises(standing_gates.StaleAttachment):
            mail.send('ariaxhan@gmail.com', 'Invoice', self.body, [self.pdf],
                      service=gmail, ledger=self.ledger)
        self.assertEqual(gmail.calls, [])
        self.assertEqual(gmail.searches, ['in:sent to:ariaxhan@gmail.com filename:"invoice-003.pdf"'])

    def test_names_are_per_recipient_and_case_insensitive(self):
        self.send([self.pdf])
        gmail = FakeGmail()
        mail.send('tim@example.com', 'Invoice', self.body, [self.pdf], service=gmail, ledger=self.ledger)
        self.assertEqual(len(gmail.calls), 2)
        upper = self.dir / 'INVOICE-003.PDF'
        upper.write_bytes(b'x')
        with self.assertRaises(standing_gates.StaleAttachment):
            mail.send('Aria <AriaXHan@gmail.com>', 'Invoice', self.body, [upper],
                      service=FakeGmail(), ledger=self.ledger)

    def test_a_failed_send_does_not_burn_the_name(self):
        class Broken(FakeGmail):
            def send(self, userId, body):
                raise RuntimeError('network')
        with self.assertRaises(RuntimeError):
            mail.send('ariaxhan@gmail.com', 'x', self.body, [self.pdf],
                      service=Broken(), ledger=self.ledger)
        gmail, _ = self.send([self.pdf])
        self.assertEqual(len(gmail.calls), 2)


class RawDraftIsTheOnlyBodySend(unittest.TestCase):
    def test_the_draft_carries_exactly_the_body_file_and_no_thread_quote(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = pathlib.Path(tmp) / 'body.txt'
            body.write_text('Exactly this.\n')
            gmail = FakeGmail()
            mail.send('a@example.com', 's', body, [], service=gmail, ledger=pathlib.Path(tmp) / 'l.json')
        kind, created = gmail.calls[0]
        self.assertEqual(kind, 'create')
        message = mail.decode(created['message']['raw'])
        self.assertEqual(message.get_content().strip(), 'Exactly this.')
        self.assertIsNone(message['In-Reply-To'])
        self.assertNotIn('>', message.get_content())

    def test_the_cli_has_no_reply_or_inline_body_route(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            mail.main(['--help'])
        text = out.getvalue()
        self.assertIn('--body-file', text)
        for banned in ('--reply', '--quote', '--thread', '--body ', '--stdin'):
            self.assertNotIn(banned, text)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            mail.main(['--to', 'a@b.c', '--subject', 's', '--reply', '123'])

    def test_no_tooling_in_this_repo_calls_the_quote_dumping_reply(self):
        hits = subprocess.run(['git', 'grep', '-nE',
                               r'gmail \+reply|gmail\s+\+reply|messages\(\)\.send\(',
                               '--', 'client', 'nexus', 'scripts', 'ops'],
                              cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(hits.stdout, '')


class SecurityTopicFilter(unittest.TestCase):
    SEEDED = [
        {'kind': 'pr_landed', 'text': 'landed: nexus-office #306 standing gates'},
        {'kind': 'alert', 'text': 'New sign-in to your Google account from Chrome on Mac'},
        {'kind': 'alert', 'text': 'GitHub: a third-party OAuth application was authorized'},
        {'kind': 'alert', 'text': 'Supabase: password reset requested'},
        {'kind': 'alert', 'text': 'Dependabot: critical vulnerability in lodash'},
        {'kind': 'lane_refused', 'text': 'a lane refused on tbs #12: tests failed'},
    ]

    def test_the_topic_list_is_data(self):
        topics = standing_gates.security_topics()
        self.assertTrue(topics)
        self.assertTrue(all(isinstance(t['pattern'], str) and t['topic'] for t in topics))

    def test_a_seeded_run_drops_every_security_class_item(self):
        kept, dropped = standing_gates.partition(self.SEEDED)
        self.assertEqual([i['kind'] for i in kept], ['pr_landed', 'lane_refused'])
        self.assertEqual(len(dropped), 4)
        self.assertTrue(all(d['dropped_for'] for d in dropped))

    def test_evaluator_rejects_security_source_before_bundling(self):
        rows = [{'title': 'Password reset requested', 'text': 'A reset alert for Aria', 'id': '1'},
                {'title': 'Studio opening', 'text': 'New classes this week', 'id': '2'}]
        with mock.patch.object(feed_runner, 'published_hashes', return_value=set()), \
             mock.patch.object(feed_runner, 'source_rows', return_value=rows), \
             mock.patch.object(feed_runner, 'podcast_rows', return_value=[]), \
             mock.patch.object(feed_runner, 'work_rows', return_value=[]), \
             mock.patch.object(feed_runner, 'buzz_rows', return_value=[]), \
             mock.patch.object(feed_runner, 'bundles', return_value=[]) as bundles, \
             mock.patch.object(feed_runner, 'available_models', side_effect=RuntimeError('stopped')), \
             self.assertRaisesRegex(RuntimeError, 'stopped'):
            import asyncio
            asyncio.run(feed_runner.run())
        self.assertEqual(bundles.call_args.args[0], [rows[1]])

    def test_evaluator_refuses_a_security_class_generated_post(self):
        with self.assertRaisesRegex(ValueError, 'standing security exclusion'):
            feed_runner.refuse_security_output('Studio update', 'New sign-in alert for your account')

    def test_a_security_item_never_reaches_the_relay(self):
        env = {'OFFICE_BUZZ_SECRET': 's', 'OFFICE_BUZZ_HOOK_URL': 'https://relay.test/h', 'OFFICE_BUZZ_MIN_S': '0'}
        with mock.patch.dict(os.environ, env), mock.patch.object(buzz, '_post', return_value=True) as post:
            for item in self.SEEDED:
                buzz.notify(item['kind'], item['text'], subject=item['text'])
        posted = [c.args[2] for c in post.call_args_list]
        self.assertEqual(posted, [self.SEEDED[0]['text'], self.SEEDED[5]['text']])

    def test_a_raised_gate_is_never_filtered(self):
        env = {'OFFICE_BUZZ_SECRET': 's', 'OFFICE_BUZZ_HOOK_URL': 'https://relay.test/h', 'OFFICE_BUZZ_MIN_S': '0'}
        text = buzz.gate_raised({'bot': 'codex', 'permission': 'bash', 'target': 'rotate the leaked OAuth token'})
        with mock.patch.dict(os.environ, env), mock.patch.object(buzz, '_post', return_value=True) as post:
            self.assertTrue(buzz.notify('gate_raised', text, subject='g1'))
        post.assert_called_once()

    def test_ordinary_words_are_not_security_class(self):
        for text in ('landed: auth-free onboarding copy', 'security deposit invoice for the studio',
                     'password field placeholder copy on the login page'):
            self.assertIsNone(standing_gates.security_class(text), text)


if __name__ == '__main__':
    unittest.main()

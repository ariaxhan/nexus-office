from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_github_actions as actions

class GitHubActions(unittest.TestCase):
    def test_double_tap_has_one_external_mutation(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(actions.run_board,'LEDGER',Path(directory)/'ledger.sqlite'),patch.object(actions,'fresh_access',return_value=('fixture','token')),patch.object(actions,'perform',return_value={'number':42}) as perform:
            world=SimpleNamespace(access=lambda:None);body={'action':'create','repo':'fixture/repo','title':'Fixture','body':'Details','request_id':'github-request-123456'}
            first=actions.command(world,{'fixture/repo'},body,None)
            self.assertEqual(actions.command(world,{'fixture/repo'},body,None),first)
            self.assertEqual(perform.call_count,1)
            with self.assertRaises(FileExistsError):actions.command(world,{'fixture/repo'},dict(body,title='Changed'),None)

    def test_ambiguous_network_result_never_replays_write(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(actions.run_board,'LEDGER',Path(directory)/'ledger.sqlite'),patch.object(actions,'fresh_access',return_value=('fixture','token')),patch.object(actions,'perform',side_effect=TimeoutError('Reply lost')) as perform:
            world=SimpleNamespace(access=lambda:None);body={'action':'comment','repo':'fixture/repo','number':1,'body':'Details','request_id':'github-request-123456'}
            first=actions.command(world,{'fixture/repo'},body,None)
            self.assertEqual(first['state'],'unconfirmed')
            self.assertEqual(actions.command(world,{'fixture/repo'},body,None),first)
            self.assertEqual(perform.call_count,1)

    def test_review_refuses_changed_head_before_post(self):
        body={'repo':'fixture/repo','number':1,'body':'Review','head':'a'*40,'request_id':'github-request-123456','event':'APPROVE'}
        with patch.object(actions,'request',return_value={'head':{'sha':'b'*40}}) as request:
            with self.assertRaises(FileExistsError):actions.perform('review',body,'fixture','token',None)
            self.assertEqual(request.call_count,1);self.assertEqual(request.call_args.args[1],'GET')

    def test_reconciliation_finds_exact_marker_without_replaying(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(actions.run_board,'LEDGER',Path(directory)/'ledger.sqlite'),patch.object(actions,'fresh_access',return_value=('fixture','token')),patch.object(actions,'perform',side_effect=TimeoutError('Lost response')) as perform:
            world=SimpleNamespace(access=lambda:None)
            body={'action':'comment','repo':'fixture/repo','number':1,'body':'Details','request_id':'github-request-123456'}
            actions.command(world,{'fixture/repo'},body,None)
            source={'id':42,'user':{'login':'fixture'},'body':'Details\n<!-- office-request:github-request-123456 -->'}
            with patch.object(actions,'request',return_value=[source]) as request:
                receipt=actions.reconcile(world,{'fixture/repo'},{'request_id':body['request_id']})
            self.assertEqual(receipt['state'],'confirmed')
            self.assertEqual(receipt['result']['id'],42)
            self.assertEqual(request.call_args.args[1],'GET')
            self.assertEqual(perform.call_count,1)
            self.assertEqual(actions.command(world,{'fixture/repo'},body,None)['state'],'confirmed')

    def test_absence_never_proves_write_failed(self):
        body={'action':'comment','repo':'fixture/repo','number':1,'request_id':'github-request-123456'}
        with patch.object(actions,'request',return_value=[]):
            result,page=actions.observe_outcome(body,'fixture','token',1)
        self.assertIsNone(result)
        self.assertIsNone(page)

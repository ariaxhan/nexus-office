"""A flight exports only artifacts produced by its own attempt."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import os
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))


class AttemptArtifacts(unittest.TestCase):
    def test_failed_capture_never_exports_an_earlier_attempts_patch(self):
        import subprocess
        from nexus.office_agent import Conversation
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);output=root/'output';output.mkdir()
            (root/'changes.patch').write_text('stale patch from attempt one\n')
            (output/'changes.patch').write_text('stale patch from attempt one\n')
            conversation=Conversation(None,{'id':'flight_two','task_id':'task_fixture'},root,root,{'source_revision':'fixture'})
            conversation.emit=lambda kind,payload:None
            with patch('nexus.office_agent.changes',side_effect=subprocess.CalledProcessError(128,'git')),\
                 patch('nexus.office_agent.Path.cwd',return_value=output):
                conversation.save_outputs()
            self.assertFalse((output/'changes.patch').exists())
            self.assertFalse((root/'changes.patch').exists())

    def test_provider_cannot_turn_internal_approval_into_human_ownership(self):
        from nexus.office_agent import Conversation
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            conversation=Conversation(None,{'id':'flight','task_id':'task'},root,root,{})
            events=[];answers=[]
            conversation.emit=lambda kind,payload:events.append(kind)
            conversation.adapter=type('Adapter',(),{'answer':lambda self,*args:answers.append(args)})()
            conversation.provider_request({'id':'approval-1',
                'method':'item/commandExecution/requestApproval',
                'params':{'title':'Approve merge'}})
            self.assertEqual(answers[0][1],'accept')
            self.assertNotIn('office.permission',events)
            self.assertIn('office.internal_approval_resolved',events)

    def test_provider_question_gets_no_fabricated_aria_answer(self):
        from nexus.office_agent import Conversation
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            conversation=Conversation(None,{'id':'flight','task_id':'task'},root,root,{})
            events=[];answers=[]
            conversation.emit=lambda kind,payload:events.append(kind)
            conversation.adapter=type('Adapter',(),{'answer':lambda self,*args:answers.append(args)})()
            conversation.provider_request({'id':'question-1',
                'method':'item/tool/requestUserInput',
                'params':{'questions':[{'id':'q','question':'Should Aria retry CI?'}]}})
            self.assertEqual(answers[0][1],'answer')
            self.assertIn('Office has no human answer',answers[0][2]['q'])
            self.assertNotIn('office.permission',events)

    def test_unfinished_turn_continues_then_remains_office_owned_blocked_across_restart(self):
        from nexus.ledger import Ledger
        from nexus import office_tasks
        from nexus.office_agent import Conversation
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            ledger=Ledger(str(root/'ledger.sqlite'));self.addCleanup(ledger.close)
            spec={'engine':'codex','profile':'personal','project':{'id':'repo'},
                  'prompt':'Land PR and verify production'}
            receipt=office_tasks.submit(ledger,'request-123456789',spec)
            saved=office_tasks.specification(ledger,receipt['task_id'])
            sent=[]
            def conversation():
                item=Conversation(ledger,{'id':'flight','task_id':receipt['task_id']},root,root,saved)
                item.adapter=type('Adapter',(),{'message':lambda self,text:sent.append(text)})()
                item.save_outputs=lambda:None
                return item
            first=conversation()
            for _ in range(3):first.provider({'method':'turn/completed','params':{}})
            self.assertEqual(len(sent),3)
            restarted=conversation()
            restarted.provider({'method':'turn/completed','params':{}})
            self.assertEqual(len(sent),3)
            events=ledger.events(kind='office.phase')
            self.assertEqual(__import__('json').loads(events[-1]['payload'])['state'],'office_owned_blocked')
            self.assertEqual(saved['outcome_contract']['terminal_condition'],spec['prompt'])

    def test_verified_parent_outcome_ends_turn(self):
        from nexus.ledger import Ledger
        from nexus import office_tasks
        from nexus.office_agent import Conversation
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
        import report_outcome
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            ledger=Ledger(str(root/'ledger.sqlite'));self.addCleanup(ledger.close)
            spec={'engine':'codex','profile':'personal','project':{'id':'repo'},'prompt':'Verify production'}
            receipt=office_tasks.submit(ledger,'request-123456789',spec)
            with patch.dict(os.environ,{'OFFICE_TASK_ID':receipt['task_id'],
                                         'OFFICE_NEXUS_LEDGER':ledger.path}):
                self.assertEqual(report_outcome.main(['--evidence','production probe PASS']),0)
            item=Conversation(ledger,{'id':'flight','task_id':receipt['task_id']},root,root,
                              office_tasks.specification(ledger,receipt['task_id']))
            item.save_outputs=lambda:None
            item.provider({'method':'turn/completed','params':{}})
            self.assertTrue(item.closed)


if __name__=='__main__':
    unittest.main()

import json
from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nexus.ledger import Ledger
from nexus import office_tasks

class Permissions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ledger=Ledger(str(Path(self.tmp.name)/'ledger.sqlite'));self.addCleanup(self.ledger.close)
        spec={'engine':'codex','profile':'personal','project':{'id':'root'},'prompt':'Read a file'}
        receipt=office_tasks.submit(self.ledger,'request-123456789',spec)
        self.task=receipt['task_id'];self.flight=receipt['flight_id']
        self.ledger.set_state(self.flight,'running',expect='queued')
        self.ledger.event('office.permission',self.task,{'flight_id':self.flight,'id':'native-unique','method':'item/commandExecution/requestApproval','params':{'command':'test'}},source='office-engine')
        self.permission=self.ledger.events(kind='office.permission')[-1]['id']

    def test_exact_permission_answer_and_cross_device_conflict(self):
        first=office_tasks.answer(self.ledger,self.task,'answer-123456789',self.permission,'accept')
        self.assertEqual(first,office_tasks.answer(self.ledger,self.task,'answer-123456789',self.permission,'accept'))
        with self.assertRaises(FileExistsError):office_tasks.answer(self.ledger,self.task,'different-123456789',self.permission,'decline')
        with self.assertRaises(FileNotFoundError):office_tasks.answer(self.ledger,self.task,'answer-123456789',self.permission+100,'accept')

    def test_ended_attempt_cannot_receive_permission_or_interrupt(self):
        self.ledger.set_state(self.flight,'cancelled',expect='running')
        with self.assertRaises(FileExistsError):office_tasks.answer(self.ledger,self.task,'answer-123456789',self.permission,'accept')
        with self.assertRaises(FileExistsError):office_tasks.control(self.ledger,self.task,'control-123456789','interrupt',self.flight)

    def test_closed_request_cannot_be_answered_again(self):
        self.ledger.event('office.permission_closed',self.task,{'permission_id':self.permission},source='office-engine')
        with self.assertRaises(FileExistsError):office_tasks.answer(self.ledger,self.task,'answer-123456789',self.permission,'accept')

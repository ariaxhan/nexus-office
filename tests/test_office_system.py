from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_system as system
from nexus.ledger import Ledger

class SystemReceipts(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ledger=Ledger(str(Path(self.tmp.name)/'ledger.sqlite'));self.addCleanup(self.ledger.close)

    def test_rejected_command_is_not_permanently_ambiguous(self):
        first=system.apply_command(self.ledger,'run','missing-plan','request-12345678')
        self.assertEqual(first['result']['state'],'rejected')
        self.assertEqual(system.apply_command(self.ledger,'run','missing-plan','request-12345678'),first)

    def test_lost_receipt_reconciles_without_replaying(self):
        with patch.object(system,'plan_command',return_value={'task_id':'task-fixture'}) as perform,patch.object(system,'record_command',side_effect=RuntimeError('receipt lost')):
            with self.assertRaises(RuntimeError):system.apply_command(self.ledger,'run','plan-fixture','request-12345678')
        with patch.object(system,'plan_command') as replay,patch.object(system,'observe_command',return_value={'task_id':'task-fixture','state':'queued'}):
            result=system.apply_command(self.ledger,'run','plan-fixture','request-12345678')
        self.assertEqual(result['result']['task_id'],'task-fixture')
        self.assertEqual(perform.call_count,1)
        replay.assert_not_called()

    def test_retry_recovery_binds_exact_request_after_taskless_flight(self):
        plan=self.ledger.add_plan('fixture')
        old=self.ledger.create_flight(plan)
        self.ledger.set_state(old,'failed')
        with patch.object(system,'record_command',side_effect=RuntimeError('receipt lost')):
            with self.assertRaises(RuntimeError):system.apply_command(self.ledger,'retry',old,'retry-request-123456')
        count=len(self.ledger.flights())
        result=system.apply_command(self.ledger,'retry',old,'retry-request-123456')
        self.assertEqual(len(self.ledger.flights()),count)
        self.assertNotEqual(result['result']['flight_id'],old)
        self.assertEqual(result['result']['state'],'queued')

    def test_completed_manual_run_still_reconciles(self):
        plan=self.ledger.add_plan('fixture')
        with patch.object(system,'record_command',side_effect=RuntimeError('receipt lost')):
            with self.assertRaises(RuntimeError):system.apply_command(self.ledger,'run',plan,'run-request-123456')
        task=self.ledger.conn.execute('SELECT id FROM tasks WHERE dedupe_key=?',('phone-run:run-request-123456',)).fetchone()['id']
        self.ledger.conn.execute("UPDATE tasks SET state='completed' WHERE id=?",(task,));self.ledger.conn.commit()
        result=system.apply_command(self.ledger,'run',plan,'run-request-123456')
        self.assertEqual(result['result']['task_id'],task)
        self.assertEqual(result['result']['state'],'completed')

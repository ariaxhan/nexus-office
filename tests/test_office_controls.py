"""An ended attempt cannot be controlled or turned into a permission gate."""
from pathlib import Path
import sys,tempfile,unittest,time,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nexus.ledger import Ledger
from nexus import office_tasks
from nexus import tower


class Controls(unittest.TestCase):
    def test_ended_attempt_cannot_receive_interrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=Ledger(str(Path(directory)/'ledger.sqlite'))
            self.addCleanup(ledger.close)
            spec={'engine':'codex','profile':'personal','project':{'id':'root'},'prompt':'Read a file'}
            receipt=office_tasks.submit(ledger,'request-123456789',spec)
            flight=receipt['flight_id'];task=receipt['task_id']
            ledger.set_state(flight,'running',expect='queued')
            ledger.set_state(flight,'cancelled',expect='running')
            with self.assertRaises(FileExistsError):
                office_tasks.control(ledger,task,'control-123456789','interrupt',flight)

    def test_failed_office_flights_retry_then_stay_office_owned_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=Ledger(str(Path(directory)/'ledger.sqlite'))
            self.addCleanup(ledger.close)
            spec={'engine':'codex','profile':'personal','project':{'id':'root'},'prompt':'Land and verify PR'}
            receipt=office_tasks.submit(ledger,'request-123456789',spec)
            task=receipt['task_id']
            ledger.set_task_state(task,'running',expect='accepted')
            for attempt in range(4):
                flight=list(ledger.flights(task_id=task))[0]
                ledger.set_state(flight['id'],'running',expect='queued')
                ledger.set_state(flight['id'],'failed',expect='running')
                tower._retry_exhausted(ledger,time.time())
                self.assertEqual(ledger.task(task)['state'],'running')
                if attempt<3:
                    self.assertEqual(len(list(ledger.flights(task_id=task))),attempt+2)
            events=[json.loads(row['payload']) for row in ledger.events(kind='office.phase')]
            self.assertEqual(events[-1]['state'],'office_owned_blocked')
            tower._retry_exhausted(ledger,time.time())
            self.assertEqual(len(ledger.events(kind='office.phase')),1)


if __name__=='__main__':unittest.main()

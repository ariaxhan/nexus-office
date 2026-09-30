"""One durable human-input path; technical failures cannot enter Needs You."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import human_asks


class HumanInputTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'asks.sqlite'
        self.proof={'identifier':'task-123','execution_ref':'task-123',
                    'gate_type':'inaccessible_authentication',
                    'action':'Enter the MFA code on Aria’s device',
                    'why_agent_cannot_do_it':'Only Aria has the device',
                    'authorization_gap':'The task authorized release, not access to her device',
                    'resume_after_answer':'Resume the original release task'}

    def request(self, **changes):
        with human_asks.connect(self.path) as db:
            return human_asks.request_human_input(db,**dict(self.proof,**changes))

    def test_mfa_is_one_durable_request_and_answer_returns_ownership(self):
        self.assertEqual(human_asks.ownership('task-123','running',self.path),'OFFICE_OWNED')
        self.assertEqual(self.request(),'task-123')
        self.assertEqual(self.request(),'task-123')
        self.assertEqual(len(human_asks.listing(self.path)['items']),1)
        self.assertEqual(human_asks.ownership('task-123','running',self.path),'HUMAN_INPUT_REQUIRED')
        with human_asks.connect(self.path) as db:
            result=human_asks.resolve_human_input(db,'task-123','MFA completed')
        self.assertEqual(result['execution_ref'],'task-123')
        self.assertEqual(human_asks.listing(self.path)['items'],[])
        self.assertEqual(human_asks.ownership('task-123','running',self.path),'OFFICE_OWNED')
        self.assertEqual(human_asks.ownership('task-123','done',self.path),'DONE')
        with human_asks.connect(self.path) as db:
            self.assertNotIn('owner',{row['name'] for row in db.execute('PRAGMA table_info(asks)')})

    def test_product_choice_is_one_request(self):
        self.request(gate_type='new_judgment',action='Choose A or B for the lesson',
                     why_agent_cannot_do_it='Both materially different choices remain')
        self.assertEqual(len(human_asks.listing(self.path)['items']),1)

    def test_pr_ci_deploy_release_rollback_and_worker_retry_asks_are_rejected(self):
        for action in ('Rerun CI','Merge the PR','Deploy the site','Retry release',
                       'Rollback and ask Aria','Restart the worker',
                        'Aria must approve merge','Approve publishing frozen L045',
                        'Decide whether to continue after rollback',
                        'Next, Aria should rerun CI','Ask Aria to deploy the site',
                        'Decide whether to merge the authorized PR',
                        'L045 awaiting Aria hub approval'):
            with self.subTest(action=action),self.assertRaises(ValueError):
                self.request(identifier=action,gate_type='new_judgment',action=action)
        self.assertEqual(human_asks.listing(self.path)['items'],[])

    def test_untyped_and_incomplete_requests_are_rejected(self):
        with self.assertRaises(ValueError):
            self.request(gate_type='technical_blocker')
        with self.assertRaises(ValueError):
            self.request(authorization_gap='')
        self.assertEqual(human_asks.listing(self.path)['items'],[])

    def test_removed_external_toolchain_volume_is_office_owned_environment_repair(self):
        for action in ('Mount /Volumes/the-drive to restore Xcode',
                       'Configured tool/dependency path points to a removed external volume'):
            with self.subTest(action=action),self.assertRaises(ValueError):
                self.request(identifier=action,gate_type='physical_action',action=action)
        self.assertEqual(human_asks.ownership('task-123','running',self.path),'OFFICE_OWNED')
        self.assertEqual(human_asks.listing(self.path)['items'],[])

    def test_restart_keeps_human_request(self):
        self.request()
        self.assertEqual(human_asks.listing(self.path)['items'][0]['id'],'task-123')
        self.assertEqual(human_asks.ownership('task-123','running',self.path),'HUMAN_INPUT_REQUIRED')

    def test_v1_open_asks_are_reclassified_without_deleting_history(self):
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE asks(id TEXT PRIMARY KEY,source TEXT NOT NULL,
                source_ref TEXT NOT NULL,owner TEXT NOT NULL,action TEXT NOT NULL,
                created_at TEXT NOT NULL,observed_at TEXT NOT NULL,state TEXT NOT NULL,
                resolution_evidence TEXT,last_source_verification TEXT,
                source_stale INTEGER NOT NULL DEFAULT 0)""")
            db.execute("""CREATE TABLE ask_events(id INTEGER PRIMARY KEY,ask_id TEXT NOT NULL,
                at TEXT NOT NULL,state TEXT NOT NULL,evidence TEXT NOT NULL)""")
            db.execute("INSERT INTO asks(id,source,source_ref,owner,action,created_at,observed_at,state)"
                       " VALUES('legacy','github','repo#1','aria','Rerun CI','yesterday','yesterday','open')")
            db.execute('PRAGMA user_version=1')
        self.assertEqual(human_asks.listing(self.path)['items'],[])
        with human_asks.connect(self.path) as db:
            row=db.execute("SELECT state,resolution_evidence FROM asks WHERE id='legacy'").fetchone()
            self.assertNotIn('owner',{column['name'] for column in db.execute('PRAGMA table_info(asks)')})
        self.assertEqual(row['state'],'reassigned')
        self.assertIn('legacy ask',json.loads(row['resolution_evidence'])['reason'])

    def test_v2_migration_retains_valid_requests_without_owner_column(self):
        self.request()
        with sqlite3.connect(self.path) as db:
            db.execute('ALTER TABLE asks ADD COLUMN owner TEXT')
            db.execute("UPDATE asks SET owner='aria'")
            db.execute('PRAGMA user_version=2')
        self.assertEqual(human_asks.ownership('task-123','running',self.path),'HUMAN_INPUT_REQUIRED')
        with human_asks.connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],3)
            self.assertNotIn('owner',{column['name'] for column in db.execute('PRAGMA table_info(asks)')})

    def test_future_schema_refused(self):
        self.request()
        with sqlite3.connect(self.path) as db:db.execute('PRAGMA user_version=99')
        with self.assertRaises(RuntimeError):human_asks.listing(self.path)


if __name__=='__main__':unittest.main()

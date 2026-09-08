import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
from nexus.ledger import Ledger
from nexus import office_tasks
import office_profiles

class TaskReceipts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ledger=Ledger(str(Path(self.temp.name)/'ledger.sqlite'));self.addCleanup(self.ledger.close)
        self.spec={'engine':'codex','profile':'personal','project':{'id':'root','path':'/fixture'},'source_revision':'abc','prompt':'Read the spec'}

    def test_source_commit_selection_is_exact_and_part_of_retry_identity(self):
        import office_tasks as api
        import subprocess
        root=Path(self.temp.name)/'checkout';root.mkdir()
        def git(*args):return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True,text=True).stdout.strip()
        git('init');git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-m','first')
        first=git('rev-parse','HEAD')
        git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-m','second')
        project={'id':'fixture','path':str(root)}
        with patch.object(api,'projects',return_value=[project]):
            choices=api.source_choices('fixture')
        self.assertNotEqual(choices['revision'],first)
        self.assertEqual(api.source_revision(project,first),first)
        with self.assertRaises(ValueError):api.source_revision(project,'--help')
        self.assertNotEqual(office_tasks.digest(self.spec),office_tasks.digest(dict(self.spec,source_ref=first)))
        self.assertEqual(office_tasks.digest(self.spec),office_tasks.digest(dict(self.spec,source_ref='HEAD')))

    def test_project_filter_applies_before_pagination(self):
        import office_tasks as api
        target=office_tasks.submit(self.ledger,'target-project-request',self.spec)
        for i in range(45):office_tasks.submit(self.ledger,f'other-project-request-{i}',dict(self.spec,project={'id':'other','path':'/other'}))
        with patch.object(api.run_board,'LEDGER',Path(self.temp.name)/'ledger.sqlite'):
            page=api.listing(project='root')
        self.assertEqual([row['id'] for row in page['items']],[target['task_id']])
        self.assertIsNone(page['next_cursor'])

    def test_recent_task_history_pages_back_without_gaps(self):
        import office_tasks as api
        item=office_tasks.submit(self.ledger,'history-paging-request',self.spec)
        for i in range(250):self.ledger.event('office.message',item['task_id'],{'text':str(i)},source='test')
        with patch.object(api.run_board,'LEDGER',Path(self.temp.name)/'ledger.sqlite'):
            cursor=-1;events=[]
            while cursor is not None:
                page=api.history(item['task_id'],cursor)
                events=page['items']+events;cursor=page['previous_cursor']
        messages=[row['payload']['text'] for row in events if row['kind']=='office.message']
        self.assertEqual(messages[-250:],[str(i) for i in range(250)])
        self.assertEqual(len({row['id'] for row in events}),len(events))

    def test_dead_runner_cannot_keep_reporting_working(self):
        import office_tasks as api
        item=office_tasks.submit(self.ledger,'dead-runner-request',self.spec)
        self.ledger.conn.execute("UPDATE flights SET state='running',pid=999999,started_at=1 WHERE id=?",(item['flight_id'],))
        self.ledger.conn.commit()
        with patch.object(api.run_board,'LEDGER',Path(self.temp.name)/'ledger.sqlite'),patch.object(api.flight_runtime,'alive',return_value=False):
            row=api.active_listing()['items'][0]
        self.assertEqual(row['phase'],'runner missing; awaiting reconciliation')
        self.assertGreater(row['observed_at'],1)

    def test_running_task_older_than_forty_completed_tasks_stays_visible(self):
        import office_tasks as api
        first=office_tasks.submit(self.ledger,'old-active-request',self.spec)
        for index in range(45):
            item=office_tasks.submit(self.ledger,f'completed-request-{index}',dict(self.spec,prompt=f'Finished {index}'))
            self.ledger.conn.execute("UPDATE flights SET state='produced' WHERE id=?",(item['flight_id'],))
        self.ledger.conn.commit()
        with patch.object(api.run_board,'LEDGER',Path(self.temp.name)/'ledger.sqlite'):
            rows=api.active_listing()['items']
        self.assertEqual([row['id'] for row in rows],[first['task_id']])

    def test_double_tap_and_reconnect_create_exactly_one_flight(self):
        one=office_tasks.submit(self.ledger,'request-12345678',self.spec)
        new_head=dict(self.spec,source_revision='def')
        two=office_tasks.submit(self.ledger,'request-12345678',new_head)
        self.assertEqual(one,two)
        self.assertEqual(len(self.ledger.flights()),1)
        self.assertEqual(office_tasks.specification(self.ledger,one['task_id'])['source_revision'],'abc')
        with self.assertRaises(FileExistsError):
            office_tasks.submit(self.ledger,'request-12345678',dict(self.spec,profile='tbs'))

    def test_message_is_durable_and_deduplicated(self):
        task=office_tasks.submit(self.ledger,'request-12345678',self.spec)['task_id']
        first=office_tasks.message(self.ledger,task,'message-12345678','Correction')
        self.assertEqual(first,office_tasks.message(self.ledger,task,'message-12345678','Correction'))
        with self.assertRaises(FileExistsError):
            office_tasks.message(self.ledger,task,'message-12345678','Different')

    def test_request_failure_rolls_back_plan_task_flight_together(self):
        with patch.object(self.ledger,'_event',side_effect=RuntimeError('fixture crash')):
            with self.assertRaises(RuntimeError):office_tasks.submit(self.ledger,'request-12345678',self.spec)
        self.assertFalse(self.ledger.flights())
        self.assertFalse(self.ledger.tasks())
        self.assertFalse(self.ledger.plans())

class AccountIsolation(unittest.TestCase):
    def test_personal_claude_unsets_profile_and_inherited_credentials(self):
        with patch.object(office_profiles,'seats',return_value={('claude','personal'):None}),patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':'/wrong','ANTHROPIC_API_KEY':'fixture','OPENAI_API_KEY':'fixture','CODEX_THREAD_ID':'parent'}):
            env=office_profiles.environment('claude','personal')
            for key in ('CLAUDE_CONFIG_DIR','ANTHROPIC_API_KEY','OPENAI_API_KEY','CODEX_THREAD_ID'):
                self.assertNotIn(key,env)

    def test_missing_tbs_never_falls_back(self):
        with patch.object(office_profiles,'seats',return_value={('codex','tbs'):Path('/nonexistent-office-profile')}):
            with self.assertRaises(FileNotFoundError):office_profiles.environment('codex','tbs')

class PersistentConversation(unittest.TestCase):
    def test_new_message_resumes_same_task_with_one_new_attempt(self):
        from nexus import tower
        with tempfile.TemporaryDirectory() as directory:
            ledger=Ledger(str(Path(directory)/'ledger.sqlite'))
            self.addCleanup(ledger.close)
            spec={'engine':'codex','profile':'personal','project':{'id':'root'},'prompt':'Read a file'}
            receipt=office_tasks.submit(ledger,'request-123456789',spec)
            task_id=receipt['task_id'];flight_id=receipt['flight_id']
            ledger.set_task_state(task_id,'running',expect='accepted')
            ledger.set_state(flight_id,'running',expect='queued')
            flight=ledger.flight(flight_id)
            self.assertTrue(tower._produced(ledger,flight,directory,{'ok':True,'artifacts':[]},1))
            self.assertEqual(ledger.task(task_id)['state'],'running')
            office_tasks.message(ledger,task_id,'message-123456789','Continue')
            office_tasks.message(ledger,task_id,'message-123456789','Continue')
            flights=ledger.flights(task_id=task_id)
            self.assertEqual(len(flights),2)
            self.assertEqual(flights[0]['attempt'],2)
            self.assertEqual(flights[0]['state'],'queued')

class NativeEdgeCases(unittest.TestCase):
    def test_local_only_repository_has_no_fabricated_origin(self):
        import subprocess
        from nexus.office_agent import prepare
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            def git(*args):
                return subprocess.check_output(['git','-C',str(source),*args],text=True).strip()
            git('init','-q');git('config','user.email','fixture@example.invalid');git('config','user.name','Fixture')
            (source/'README.md').write_text('Local only\n')
            git('add','README.md');git('commit','-qm','Fixture')
            from contextlib import closing
            with closing(Ledger(str(root/'ledger.sqlite'))) as ledger:
                _,checkout=prepare(ledger,'task_fixture',{'project':{'path':str(source)},'source_revision':git('rev-parse','HEAD')})
            self.assertEqual((checkout/'README.md').read_text(),'Local only\n')
            self.assertEqual(subprocess.check_output(['git','-C',str(checkout),'remote'],text=True),'')

    def test_denial_uses_supported_native_cancel(self):
        from nexus.office_rpc import approval_response
        request={'method':'item/commandExecution/requestApproval','params':{'availableDecisions':['accept','cancel']}}
        self.assertEqual(approval_response(request,'decline'),{'decision':'cancel'})
        request['params']['availableDecisions'].append('decline')
        self.assertEqual(approval_response(request,'decline'),{'decision':'decline'})

    def test_close_before_first_output_still_emits_required_artifact(self):
        from nexus.office_agent import Conversation
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);output=root/'output';output.mkdir()
            conversation=Conversation(None,{'id':'flight_fixture','task_id':'task_fixture'},root,root,{'source_revision':'fixture'})
            with patch('nexus.office_agent.changes',return_value=''),patch('nexus.office_agent.Path.cwd',return_value=output):
                conversation.save_outputs()
            self.assertTrue((output/'response.md').is_file())

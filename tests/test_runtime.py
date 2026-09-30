"""Runtime gates are a view of validated human-input records."""
from __future__ import annotations
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'client'))
import human_asks
import runtime


class GateViewTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=pathlib.Path(self.tmp.name)/'human-input.sqlite'
        self.env=mock.patch.dict(os.environ,{'OFFICE_HUMAN_ASKS_DB':str(self.db)})
        self.env.start();self.addCleanup(self.env.stop)
        self.old=human_asks.DB
        human_asks.DB=self.db;self.addCleanup(setattr,human_asks,'DB',self.old)

    def request(self,kind='inaccessible_authentication',identifier='task-1'):
        with human_asks.connect(self.db) as db:
            human_asks.request_human_input(db,identifier=identifier,execution_ref=identifier,
                gate_type=kind,action='Choose lesson A or B' if kind=='new_judgment' else 'Enter MFA code on Aria device',
                why_agent_cannot_do_it='Only Aria has the device',
                authorization_gap='Task authorization does not provide the factor',
                resume_after_answer='Resume the original task')

    def test_empty_and_unvalidated_gate_files_do_not_create_human_ownership(self):
        root=pathlib.Path(self.tmp.name)/'_meta'/'state';root.mkdir(parents=True)
        (root/'pending-question.json').write_text(json.dumps({'id':'legacy','permission':'run_bash'}))
        self.assertEqual(runtime.read_gate()['state'],'clear')
        self.assertEqual(runtime.read_gates(),{'state':'ok','gates':[]})

    def test_valid_record_is_the_only_native_gate_source(self):
        self.request()
        one=runtime.read_gate();many=runtime.read_gates()['gates']
        self.assertEqual(one['id'],'task-1')
        self.assertEqual(one,many[0])
        self.assertEqual(one['permission'],'inaccessible_authentication')

    def test_product_judgment_appears_in_native_needs_you_but_cannot_be_allowed(self):
        self.request('new_judgment')
        self.assertEqual(runtime.read_gates()['gates'][0]['permission'],'new_judgment')
        self.assertEqual(len(human_asks.listing(self.db)['items']),1)
        ok,_=runtime.answer_gate(None,'task-1','allow',False)
        self.assertFalse(ok)

    def test_native_answer_uses_central_resolution_path(self):
        self.request()
        with mock.patch('office_tasks.answer_human_input',return_value={'ok':True}) as answer:
            ok,_=runtime.answer_gate(None,'task-1','allow',False)
        self.assertTrue(ok)
        answer.assert_called_once()


class BoardTest(unittest.TestCase):
    def test_an_unreachable_dashboard_is_down_not_empty(self):
        # "The runtime is not running" and "nothing is happening" must never
        # render the same. A silent zero is the false-green this project exists
        # to kill.
        os.environ["OFFICE_RUNTIME_URL"] = "http://127.0.0.1:59999"
        try:
            import importlib, runtime
            rt = importlib.reload(runtime)
            board = rt.read_board()
            self.assertEqual(board["state"], "down")
            self.assertIn("detail", board)
        finally:
            os.environ.pop("OFFICE_RUNTIME_URL", None)


if __name__ == "__main__":
    unittest.main()

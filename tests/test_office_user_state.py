import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_user_state as state
import office_preferences as preferences

class UserState(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.env=patch.dict(os.environ,OFFICE_STATE=str(Path(self.tmp.name).resolve()));self.env.start();self.addCleanup(self.env.stop)

    def test_retry_and_delayed_device_cannot_erase_newer_position(self):
        now=time.time()*1000
        one={'kind':'listening','id':'episode','value':12,'recorded_at':now}
        first=state.save(one);self.assertTrue(first['applied'])
        self.assertFalse(state.save(one)['applied'])
        state.save(dict(one,value=4,recorded_at=now+1))
        self.assertFalse(state.save(one)['applied'])
        self.assertEqual(state.read()['items']['listening']['episode']['value'],4)

    def test_forget_removes_remote_activity_and_rejects_late_writes(self):
        state.save({'kind':'recent','id':'file','value':{'kind':'file','id':'f'},'recorded_at':time.time()*1000})
        preferences.save({'revision':0,'preferences':{'remember':False}})
        self.assertIsNone(state.read()['items']['recent']['file']['value'])
        with self.assertRaises(PermissionError):state.save({'kind':'listening','id':'episode','value':2,'recorded_at':time.time()*1000})

    def test_saved_objects_survive_forget(self):
        state.save({'kind':'saved','id':'file','value':{'kind':'file','id':'f'},'recorded_at':time.time()*1000})
        preferences.save({'revision':0,'preferences':{'remember':False}})
        self.assertEqual(state.read()['items']['saved']['file']['value']['id'],'f')

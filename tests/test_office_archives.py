import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_archives as archives

class Archives(unittest.TestCase):
    def test_same_cwd_old_histories_keep_exact_engine_and_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();personal=root/'personal';tbs=root/'tbs';personal.mkdir();tbs.mkdir()
            for folder,identifier in ((personal,'native-one'),(tbs,'native-two')):
                path=folder/'session.jsonl'
                rows=[{'type':'queue-operation'}]*35+[{'type':'session_meta','payload':{'id':identifier,'cwd':'/same/project'}},{'type':'response_item','payload':{'role':'assistant','content':[{'type':'output_text','text':'Retained answer'}]}}]
                path.write_text(''.join(json.dumps(row)+'\n' for row in rows));os.utime(path,(time.time()-86400*5,)*2)
            with patch.object(archives,'CACHE',{'at':0,'items':[],'errors':[]}),patch.object(archives,'locations',return_value=[('codex','personal',personal),('codex','tbs',tbs)]):
                data=archives.inventory(refresh=True)
                self.assertEqual({(row['engine_session_id'],row['profile']) for row in data['items']},{('native-one','personal'),('native-two','tbs')})
                self.assertEqual(len({row['id'] for row in data['items']}),2)
                for row in data['items']:
                    page=archives.messages(row['id'])
                    self.assertEqual(page['items'][0]['text'],'Retained answer')
                    self.assertIsNone(page['next_offset'])

    def test_raw_utf8_boundary_never_corrupts_a_character(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'raw.jsonl';text='a'*65535+'🌿'+'end';path.write_text(text)
            with patch.object(archives,'lookup',return_value=({'path':str(path)},path)):
                first=archives.transcript('fixture');second=archives.transcript('fixture',first['next_offset'])
                self.assertEqual(first['text']+second['text'],text)

    def test_recent_messages_page_backwards_without_gaps_or_utf8_damage(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'session.jsonl'
            rows=[{'type':'response_item','payload':{'role':'assistant','content':str(i)+' 🌿 '+('x'*70000 if i==55 else '')}} for i in range(95)]
            path.write_text('\n'.join(json.dumps(row,ensure_ascii=False) for row in rows))
            with patch.object(archives,'lookup',return_value=({'path':str(path)},path)):
                cursor=-1;all_items=[]
                while cursor is not None:
                    page=archives.messages('fixture',cursor)
                    all_items=page['items']+all_items
                    cursor=page['previous_offset']
                self.assertEqual([item['text'] for item in all_items],[row['payload']['content'] for row in rows])

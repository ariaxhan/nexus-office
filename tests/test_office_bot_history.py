import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_bot_history as history

class BotHistory(unittest.TestCase):
    def test_all_turns_beyond_harness_cap_and_archives_remain_reachable(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            rows=[{'role':'user','content':f'Older retained turn {i}'} for i in range(250)]
            (root/'sphinx.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            (root/'sphinx-20260901T120000Z.jsonl').write_text(json.dumps({'role':'assistant','content':'Archived answer'})+'\n')
            with patch.object(history,'root',return_value=root):
                self.assertEqual(len(history.listing()['items']),2)
                result=[];offset=0
                while offset is not None:
                    page=history.history('bot-history:sphinx.jsonl',offset);result.extend(page['items']);offset=page['next_offset']
                self.assertEqual(result,rows)
                result=[];offset=-1
                while offset is not None:
                    page=history.history('bot-history:sphinx.jsonl',offset);result=page['items']+result;offset=page['previous_offset']
                self.assertEqual(result,rows)
                errors=[];records=list(history.records(errors))
                self.assertEqual(len(records),2);self.assertFalse(errors)
                self.assertTrue(all(Path(row['source_path']).is_file() for row in records))

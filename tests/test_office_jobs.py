from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
from office_jobs import read_log

class LogPages(unittest.TestCase):
    def test_utf8_log_continuation_keeps_complete_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'job.log';text='a'*65535+'🌿 next line\n';path.write_text(text)
            first=read_log(path);second=read_log(path,first['next_offset'],first['version'])
            self.assertEqual(first['text']+second['text'],text)
            self.assertIsNone(second['next_offset'])

    def test_rotation_does_not_mix_two_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory).resolve()/'job.log';path.write_text('a'*70000)
            first=read_log(path);path.rename(path.with_suffix('.old'));path.write_text('new run\n')
            second=read_log(path,first['next_offset'],first['version'])
            self.assertEqual(second['state'],'rotated');self.assertEqual(second['text'],'')

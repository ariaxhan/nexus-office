from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_github_stage as stage
import office_github_cache as cache

class Stage(unittest.TestCase):
    def test_restart_resumes_committed_batch_and_publishes_only_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'repo.jsonl';target.write_text('prior snapshot')
            states=[]
            def batch(access,known,repo,state):
                index=state.get('index',0);states.append(index)
                return [{'id':str(index),'body':'data'}],{'stage':'done' if index==2 else 'issues','index':index+1}
            with patch.object(stage.batches,'next_batch',side_effect=batch):
                first=stage.advance(None,[],'a/b',target,limit=1)
                self.assertEqual(first['records'],1);self.assertEqual(target.read_text(),'prior snapshot')
                with patch.object(stage.batches,'next_batch',side_effect=ConnectionError('rate limited')):
                    with self.assertRaises(ConnectionError):stage.advance(None,[],'a/b',target,limit=1)
                result=stage.advance(None,[],'a/b',target,limit=8)
            self.assertEqual(states,[0,1,2]);self.assertEqual(result['count'],3)
            self.assertFalse(target.with_suffix('.stage.sqlite').exists())
            with target.open() as stream:self.assertEqual(cache.read_header(stream,'a/b')['count'],3)

    def test_batch_rows_and_cursor_commit_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            db,path=stage.connect(Path(directory)/'repo.jsonl')
            try:
                with self.assertRaises(KeyError):stage.commit(db,[{'id':'one'},{'missing':'id'}],{'stage':'done'})
                self.assertEqual(db.execute('SELECT count(*) FROM records').fetchone()[0],0)
                self.assertIsNone(db.execute('SELECT * FROM checkpoint').fetchone())
            finally:db.close()
            self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_stage_refuses_symbolic_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'repo.jsonl';other=root/'other';other.write_text('keep')
            target.with_suffix('.stage.sqlite').symlink_to(other)
            with self.assertRaises(OSError):stage.connect(target)
            self.assertEqual(other.read_text(),'keep')

    def test_crash_after_publication_never_refreshes_old_generation_age(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'repo.jsonl'
            with patch.object(stage.batches,'next_batch',return_value=([{'id':'one'}],{'stage':'done'})),patch.object(stage.time,'time',return_value=100),patch.object(Path,'unlink',side_effect=OSError('crash after replace')):
                with self.assertRaises(OSError):stage.advance(None,[],'a/b',target)
            before=target.read_bytes()
            with patch.object(stage.batches,'next_batch',side_effect=AssertionError('done stage fetched again')),patch.object(stage.time,'time',return_value=9000):
                recovered=stage.advance(None,[],'a/b',target)
            self.assertEqual(target.read_bytes(),before)
            self.assertEqual(recovered['finished_at'],100)
            self.assertFalse(target.with_suffix('.stage.sqlite').exists())

    def test_low_storage_retains_checkpoint_and_old_snapshot(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'repo.jsonl';target.write_text('prior snapshot')
            with patch.object(stage.batches,'next_batch',return_value=([{'id':'one'}],{'stage':'issues','cursor':'next'})):
                stage.advance(None,[],'a/b',target,limit=1)
            before=target.with_suffix('.stage.sqlite').read_bytes()
            with patch.object(stage.shutil,'disk_usage',return_value=SimpleNamespace(free=4*1024**3)),patch.object(stage.batches,'next_batch',side_effect=AssertionError('provider read while storage constrained')):
                with self.assertRaisesRegex(OSError,'5 GiB'):stage.advance(None,[],'a/b',target)
            self.assertEqual(target.read_text(),'prior snapshot');self.assertEqual(target.with_suffix('.stage.sqlite').read_bytes(),before)

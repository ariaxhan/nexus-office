from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_github_cache as cache

class Cache(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=patch.object(cache,'root',return_value=Path(self.temp.name).resolve());self.root.start()
        self.addCleanup(self.temp.cleanup);self.addCleanup(self.root.stop)
        cache.STATUS.clear()

    def publish(self,rows):
        with patch.object(cache.corpus,'repository_records',return_value=iter(rows)):
            return cache.collect(None,['a/b'],'a/b')

    def test_late_failure_preserves_complete_prior_snapshot(self):
        self.publish([{'id':'one','body':'old'}]);before=cache.path('a/b').read_bytes()
        def broken(*args):
            yield {'id':'two','body':'partial'}
            raise ConnectionError('late failure')
        with patch.object(cache.corpus,'repository_records',side_effect=broken):
            with self.assertRaises(ConnectionError):cache.collect(None,['a/b'],'a/b')
        self.assertEqual(cache.path('a/b').read_bytes(),before)
        self.assertEqual(len(list(Path(self.temp.name).iterdir())),1)

    def test_open_reader_keeps_one_generation_during_replace(self):
        old=self.publish([{'id':'one'},{'id':'two'}])
        reader=cache.records(['a/b'],[]);first=next(reader)
        self.publish([{'id':'new'}]);second=next(reader)
        self.assertEqual([first['id'],second['id']],['one','two'])
        self.assertEqual(second['coverage'],'GitHub corpus:'+old['generation'])

    def test_corrupt_snapshot_aborts_index_instead_of_pruning_old_records(self):
        self.publish([{'id':'one'}])
        with cache.path('a/b').open('a') as stream:stream.write('broken\n')
        errors=[]
        with self.assertRaises(RuntimeError):list(cache.records(['a/b'],errors))
        self.assertEqual(errors[0]['source'],'a/b')

    def test_empty_new_generation_is_not_indexed_with_stale_rows(self):
        self.publish([])
        rows=cache.coverage(['a/b'],[{'source':'a/b','coverage':'GitHub corpus:old','indexed':1}])
        self.assertEqual(rows[0]['state'],'indexing')
        self.assertEqual(cache.coverage(['a/b'],[])[0]['state'],'indexed')

    def test_corrupt_header_is_visible_without_breaking_coverage(self):
        cache.path('a/b').write_text('broken\n')
        self.assertEqual(cache.coverage(['a/b'],[])[0]['state'],'error')

    def test_access_failure_is_reported_and_releases_lock(self):
        from types import SimpleNamespace
        world=SimpleNamespace(snapshot={'stations':[{'repo':'a/b'}]},access=lambda:(_ for _ in ()).throw(ConnectionError('offline')))
        cache.LOCK.acquire();cache.refresh_all(world)
        self.assertFalse(cache.LOCK.locked())
        self.assertEqual(cache.STATUS['a/b']['state'],'error')

    def test_valid_json_truncation_aborts_generation(self):
        self.publish([{'id':'one'},{'id':'two'}])
        target=cache.path('a/b');lines=target.read_text().splitlines(True)
        target.write_text(''.join(lines[:2]))
        with self.assertRaises(RuntimeError):list(cache.records(['a/b'],[]))

    def test_local_github_checkout_is_in_collection_scope(self):
        from types import SimpleNamespace
        with patch.dict(cache.os.environ,{'OFFICE_RUNTIME_ROOT':'/vault'}),patch.object(cache.office_workspaces,'discover',return_value=[('other/repo',Path('/vault/project')),('Local / scratch',Path('/vault/scratch'))]):
            self.assertEqual(cache.known(SimpleNamespace(snapshot={'stations':[{'repo':'a/b'}]})),['a/b','other/repo'])

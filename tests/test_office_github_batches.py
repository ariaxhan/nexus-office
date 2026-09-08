from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_github_batches as batches

class Batches(unittest.TestCase):
    def test_checkpoint_keeps_cursor_and_pull_inventory_without_mutating_input(self):
        state=batches.initial();item={'id':1,'number':7,'pull_request':{}}
        item['pull_request']={'url':'pull'}
        with patch.object(batches.corpus.github,'read_identity',return_value=('seat','unused')),patch.object(batches.corpus.github,'fetch_page',return_value=([item],1,'repos/a/b/issues?after=x')):
            rows,new=batches.next_batch(None,['a/b'],'a/b',state)
        self.assertEqual(new['pulls'],[7]);self.assertEqual(new['cursor'],'repos/a/b/issues?after=x')
        self.assertEqual(len(rows),1);self.assertEqual(state['pulls'],[]);self.assertIsNone(state['cursor'])

    def test_repeated_provider_cursor_fails_after_resume(self):
        state=batches.initial();state.update(cursor='repos/a/b/issues?after=x',seen=['repos/a/b/issues?after=x'])
        with self.assertRaises(ValueError):batches.page_batch('a/b','seat','unused',state)

    def test_failed_pull_does_not_advance_checkpoint(self):
        state=dict(batches.initial(),stage='pulls',pulls=[7])
        with patch.object(batches.corpus.github,'read_identity',return_value=('seat','unused')),patch.object(batches.corpus,'pull_records',side_effect=FileExistsError('head changed')):
            with self.assertRaises(FileExistsError):batches.next_batch(None,['a/b'],'a/b',state)
        self.assertEqual(state['pull_index'],0)

    def test_bad_next_cursor_is_never_committed_and_provider_can_recover(self):
        state=batches.initial();endpoint='repos/a/b/issues?state=all&sort=created&direction=asc&per_page=100'
        with patch.object(batches.corpus.github,'read_identity',return_value=('seat','unused')),patch.object(batches.corpus.github,'fetch_page',side_effect=[([],1,endpoint),([],2,None)]):
            with self.assertRaises(ValueError):batches.next_batch(None,['a/b'],'a/b',state)
            rows,recovered=batches.next_batch(None,['a/b'],'a/b',state)
        self.assertEqual(recovered['stage'],'comments');self.assertEqual(state,batches.initial())

from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_github_corpus as corpus

class Corpus(unittest.TestCase):
    def test_paging_includes_records_beyond_first_hundred(self):
        with patch.object(corpus.github,'fetch',side_effect=[([{'id':i} for i in range(100)],1),([{'id':100}],2)]) as fetch:
            rows=list(corpus.pages('repos/owner/repo/issues?state=all','owner','unused'))
        self.assertEqual(len(rows),101)
        self.assertIn('&per_page=100&page=2',fetch.call_args.args[0])

    def test_repository_includes_body_comments_inline_reviews_and_diff(self):
        issue={'id':1,'number':5,'title':'Title','body':'bodyneedle','pull_request':{'url':'pull'}}
        def fetch(endpoint,*args):
            if '/issues?' in endpoint:return [issue],1
            if '/issues/comments?' in endpoint:return [{'id':2,'body':'commentneedle','issue_url':'https://api.github.com/repos/owner/repo/issues/5'}],1
            if '/pulls/comments?' in endpoint:return [{'id':3,'body':'inlineneedle','pull_request_url':'https://api.github.com/repos/owner/repo/pulls/5'}],1
            if '/reviews?' in endpoint:return [{'id':4,'body':'reviewneedle'}],1
            return 'diffneedle',1
        head={'title':'Title','head':{'sha':'a'},'base':{'sha':'b'}}
        with patch.object(corpus.github,'identity',return_value=('owner','unused')),patch.object(corpus.github,'fetch',side_effect=fetch),patch.object(corpus.github,'fresh',return_value=(head,1)):
            rows=list(corpus.repository_records(None,['owner/repo'],'owner/repo'))
        self.assertEqual(len(rows),5)
        for needle in ['bodyneedle','commentneedle','inlineneedle','reviewneedle','diffneedle']:self.assertTrue(any(needle in row['body'] for row in rows))
        self.assertEqual(len({row['id'] for row in rows}),5)
        self.assertTrue(all(row['path']=='owner/repo#5' for row in rows))

    def test_changed_head_cannot_complete_a_diff_snapshot(self):
        before={'title':'Title','head':{'sha':'a'},'base':{'sha':'b'}}
        after=dict(before,head={'sha':'new'})
        with patch.object(corpus.github,'fresh',side_effect=[(before,1),(after,2)]),patch.object(corpus.github,'fetch',side_effect=[('diff',1),([],1)]):
            with self.assertRaises(FileExistsError):list(corpus.pull_records('owner/repo',5,'owner','unused'))

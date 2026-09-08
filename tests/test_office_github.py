import base64
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_github

class GitHubBlob(unittest.TestCase):
    def test_large_contents_response_fetches_immutable_blob(self):
        sha='a'*40
        with patch.object(office_github,'fetch',return_value=({'encoding':'base64','content':base64.b64encode(b'Large document').decode()},1)) as fetch:
            result=office_github.blob_content('owner/repo',{'encoding':'none','sha':sha},'seat','fixture')
        self.assertEqual(result,b'Large document')
        self.assertEqual(fetch.call_args.args[0],'repos/owner/repo/git/blobs/'+sha)

    def test_unavailable_content_is_never_silently_empty(self):
        with self.assertRaises(ValueError):
            office_github.blob_content('owner/repo',{'encoding':'none'},'seat','fixture')

    def test_paginated_diff_rejects_another_head(self):
        with patch.object(office_github,'fresh',return_value=({'head':{'sha':'b'*40}},1)):
            with self.assertRaises(FileExistsError):office_github.guard_head('owner/repo','1','who','token','a'*40)

    def test_tree_resolves_branch_once_and_returns_immutable_navigation_ref(self):
        sha='b'*40
        with patch.object(office_github,'identity',return_value=('seat','fixture')),patch.object(office_github,'fetch',side_effect=[({'sha':sha},1),([{'path':'README.md','name':'README.md'}],2)]) as fetch:
            result=office_github.tree(None,['owner/repo'],{'repo':'owner/repo','ref':'feature/mobile'})
        self.assertEqual(result['ref'],sha)
        self.assertIn('feature%2Fmobile',fetch.call_args_list[0].args[0])
        self.assertTrue(fetch.call_args_list[1].args[0].endswith('?ref='+sha))

    def test_exact_commit_tree_does_not_resolve_moving_branch(self):
        sha='a'*40
        with patch.object(office_github,'identity',return_value=('seat','fixture')),patch.object(office_github,'fetch',return_value=([],1)) as fetch:
            result=office_github.tree(None,['owner/repo'],{'repo':'owner/repo','ref':sha})
        self.assertEqual(fetch.call_count,1);self.assertEqual(result['ref'],sha)

    def test_branches_have_complete_pagination_and_commit_identity(self):
        rows=[{'name':str(n),'commit':{'sha':'a'*40}} for n in range(100)]
        with patch.object(office_github,'identity',return_value=('seat','fixture')),patch.object(office_github,'fetch',return_value=(rows,1)):
            result=office_github.branches(None,['owner/repo'],{'repo':'owner/repo','cursor':'2'})
        self.assertEqual(result['next_cursor'],3);self.assertEqual(result['items'][0]['sha'],'a'*40)

    def test_read_access_never_grants_write_identity(self):
        from types import SimpleNamespace
        access=SimpleNamespace(read_token_for=lambda repo:('reader','read-token'),token_for=lambda repo:(None,None))
        self.assertEqual(office_github.read_identity(access,'owner/repo',['owner/repo']),('reader','read-token'))
        with self.assertRaises(PermissionError):office_github.identity(access,'owner/repo',['owner/repo'])
        with self.assertRaises(PermissionError):office_github.read_identity(access,'outside/repo',['owner/repo'])

    def test_read_resolver_accepts_repository_without_push_and_leaves_write_cache(self):
        import office_sync_shim
        mod=office_sync_shim.mod
        access=object.__new__(mod.Access);access.mine=['reader'];access.cache={'owner/repo':''}
        with patch.object(access,'_token',return_value='fixture'),patch.object(mod,'sh',return_value=(0,'owner/repo\n','')) as sh:
            self.assertEqual(access.read_token_for('owner/repo'),('reader','fixture'))
            self.assertEqual(access.read_token_for('owner/repo'),('reader','fixture'))
            self.assertEqual(sh.call_count,1)
        self.assertEqual(access.cache,{'owner/repo':''})

    def test_repeated_tree_read_does_not_strip_shared_cached_blob(self):
        payload={'encoding':'base64','content':base64.b64encode(b'kept text').decode(),'sha':'b'*40}
        with patch.object(office_github,'identity',return_value=('seat','fixture')),patch.object(office_github,'fetch',return_value=(payload,1)):
            first=office_github.tree(None,[],{'repo':'owner/repo','ref':'a'*40})
            second=office_github.tree(None,[],{'repo':'owner/repo','ref':'a'*40})
        self.assertEqual(first['object']['text'],'kept text');self.assertEqual(second['object']['text'],'kept text')
        self.assertIn('content',payload)

    def test_page_preserves_provider_cursor_and_rejects_foreign_host(self):
        from types import SimpleNamespace
        result=SimpleNamespace(returncode=0,stdout='HTTP/2.0 200 OK\nLink: <https://api.github.com/repositories/1/issues?after=cursor>; rel="next"\n\n[{"id":1}]',stderr='')
        with patch.object(office_github.subprocess,'run',return_value=result):
            rows,_,next_page=office_github.fetch_page('repos/owner/repo/issues','seat','fixture')
        self.assertEqual(rows,[{'id':1}]);self.assertEqual(next_page,'repositories/1/issues?after=cursor')
        with self.assertRaises(ValueError):office_github.page_endpoint('https://elsewhere.test/repos/owner/repo')

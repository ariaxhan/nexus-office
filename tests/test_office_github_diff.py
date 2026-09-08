from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_github_diff as local
import office_github as github

class Diff(unittest.TestCase):
    def test_diff_uses_only_saved_commits_and_preserves_dirty_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True,stderr=subprocess.DEVNULL).strip()
            def commit(text):
                (root/'file.txt').write_text(text);git('add','file.txt');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture');return git('rev-parse','HEAD')
            git('init','-b','fixture');base=commit('before\n');head=commit('saved change\n')
            (root/'file.txt').write_text('unsaved work\n')
            with patch.object(local.objects,'vault',return_value=root),patch.object(local.office_workspaces,'discover',return_value=[('owner/repo',root)]):
                diff=local.read('owner/repo',base,head)
            self.assertIn('+saved change',diff);self.assertNotIn('unsaved work',diff)
            self.assertEqual((root/'file.txt').read_text(),'unsaved work\n')

    def test_only_provider_large_diff_failure_uses_local_fallback(self):
        with patch.object(github,'fetch',side_effect=ValueError('GitHub diff too large (HTTP 406)')),patch.object(local,'read',return_value='full diff') as read:
            text,_=github.diff('owner/repo',1,'a'*40,'b'*40,'seat','fixture')
        self.assertEqual(text,'full diff');read.assert_called_once_with('owner/repo','b'*40,'a'*40)
        with patch.object(github,'fetch',side_effect=ValueError('HTTP 401')),patch.object(local,'read') as read:
            with self.assertRaises(ValueError):github.diff('owner/repo',1,'a'*40,'b'*40,'seat','fixture')
        read.assert_not_called()

    def test_large_diff_windows_preserve_all_text_without_growing_page(self):
        text='a'*65535+'🌱'+'b'*100000
        first=github.diff_window(text,0);second=github.diff_window(text,first['diff_next_cursor']);third=github.diff_window(text,second['diff_next_cursor'])
        self.assertEqual(first['diff']+second['diff']+third['diff'],text)
        self.assertEqual(second['diff_previous_cursor'],0);self.assertIsNone(third['diff_next_cursor'])
        self.assertTrue(all(len(page['diff'])<=65536 for page in (first,second,third)))

    def test_base_only_change_invalidates_diff(self):
        with patch.object(github,'fresh',return_value=({'head':{'sha':'a'*40},'base':{'sha':'c'*40}},1)):
            with self.assertRaises(FileExistsError):github.guard_diff('owner/repo',1,'seat','token','a'*40,'b'*40)

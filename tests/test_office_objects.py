"""Mobile file boundaries, concurrency and byte ranges use real files."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import office_objects as objects
import office_content as content
import office_preferences as preferences


class Objects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        roots = {'root': {'id': 'root', 'name': 'fixture', 'path': str(self.root)}}
        self.patcher = patch.object(objects, 'roots', return_value=roots)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def file(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return objects.encode('root', name)

    def test_navigation_returns_working_root_parent(self):
        self.file('docs/spec.md', 'draft')
        result = objects.browse(objects.encode('root', 'docs'))
        self.assertEqual(objects.browse(result['parent'])['items'][0]['name'], 'docs')

    def test_policy_denies_secrets_traversal_symlinks_all_routes(self):
        self.file('.env', 'private')
        self.file('docs/secret.md', 'work')
        (self.root / 'linked').symlink_to(self.root / 'docs', target_is_directory=True)
        for name in ('.', '.env', '../outside', 'linked/secret.md', '/etc/passwd', 'auth.json'):
            with self.subTest(name=name), self.assertRaises(PermissionError):
                objects.read(objects.encode('root', name))
        self.assertTrue(objects.permitted('tokenizer.py'))
        self.assertTrue(objects.permitted('.github/workflows/verify.yml'))
        self.assertTrue(objects.permitted('config.toml'))

    def test_utf8_chunk_boundary_and_whole_file_revision(self):
        text='a'*(objects.MAX_TEXT-1)+'🌿'+'ending'
        identifier=self.file('long.md',text)
        first=objects.read(identifier)
        second=objects.read(identifier,first['next_offset'])
        self.assertEqual(first['text']+second['text'],text)
        self.assertTrue(second['is_text'])
        self.assertEqual(first['revision'],second['revision'])
        self.assertEqual(first['revision'],hashlib.sha256(text.encode()).hexdigest())

    def test_edit_requires_exact_revision_and_preserves_external_change(self):
        identifier = self.file('note.md', 'one')
        first = objects.read(identifier)
        (self.root / 'note.md').write_text('external')
        with self.assertRaises(FileExistsError):
            objects.save({'id': identifier, 'text': 'two', 'revision': first['revision']})
        fresh = objects.read(identifier)
        saved = objects.save({'id': identifier, 'text': 'three', 'revision': fresh['revision']})
        self.assertEqual(saved['text'], 'three')
        self.assertEqual(saved['revision'], hashlib.sha256(b'three').hexdigest())

    def test_invalid_utf8_is_download_only(self):
        identifier = self.file('binary.bin', '')
        (self.root / 'binary.bin').write_bytes(b'\xff\xfe')
        self.assertFalse(objects.read(identifier)['editable'])

    def test_pagination_has_no_missing_objects(self):
        for i in range(203):
            self.file(f'{i:03}.md', str(i))
        a = objects.browse(objects.encode('root', ''))
        b = objects.browse(objects.encode('root', ''), a['next_cursor'])
        c = objects.browse(objects.encode('root', ''), b['next_cursor'])
        self.assertEqual(len({r['id'] for page in (a,b,c) for r in page['items']}), 203)
        self.assertIsNone(c['next_cursor'])


    def test_numbered_selection_keeps_exact_revision_and_line_range(self):
        identifier=self.file('notes.md','first\n🌿 second\nthird\nfourth\n')
        data=objects.read(identifier)
        self.assertEqual((data['line_start'],data['line_end']),(1,4))
        reference={'id':identifier,'revision':data['revision'],'start_line':2,'end_line':3}
        self.assertEqual(objects.selection(reference),'🌿 second\nthird\n')
        self.assertEqual(objects.read(identifier,len('first\n'.encode()))['line_start'],2)
        (self.root/'notes.md').write_text('changed\n')
        with self.assertRaises(FileExistsError):objects.selection(reference)

    def test_selection_cannot_silently_truncate_missing_lines(self):
        identifier=self.file('short.md','one\ntwo\n');data=objects.read(identifier)
        with self.assertRaises(ValueError):objects.selection({'id':identifier,'revision':data['revision'],'start_line':2,'end_line':9})

    def test_checkout_provenance_reports_branch_commit_and_untracked_changes(self):
        import subprocess
        def git(*args):
            return subprocess.check_output(['git','-C',str(self.root),*args],text=True,stderr=subprocess.DEVNULL).strip()
        git('init','-b','phone-fixture');(self.root/'README.md').write_text('saved\n')
        git('add','README.md');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture')
        identifier=objects.encode('root','README.md');first=objects.provenance(identifier)
        self.assertEqual(first['branch'],'phone-fixture');self.assertEqual(first['commit'],git('rev-parse','HEAD'));self.assertFalse(first['dirty'])
        (self.root/'untracked.txt').write_text('new')
        self.assertTrue(objects.provenance(identifier)['dirty'])

    def test_collection_provenance_does_not_imply_a_git_checkout(self):
        result=objects.provenance(objects.encode('root',''))
        self.assertEqual(result['state'],'collection');self.assertNotIn('branch',result)


class Ranges(unittest.TestCase):
    def test_seek_and_suffix(self):
        self.assertEqual(content.bounds('bytes=20-29', 100), (20,29,206))
        self.assertEqual(content.bounds('bytes=-10', 100), (90,99,206))
        self.assertEqual(content.bounds('bytes=90-', 100), (90,99,206))
        for bad in ('bytes=100-', 'bytes=4-2', 'bytes=0-2,5-9', 'bytes=-0'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                content.bounds(bad,100)


class Preferences(unittest.TestCase):
    def test_persistence_conflict_and_reset(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, OFFICE_STATE=directory):
            first = preferences.read()
            saved = preferences.save({'revision': first['revision'], 'preferences': {'background':'#eaddcc','haptics':False}})
            self.assertEqual(preferences.read(), saved)
            with self.assertRaises(FileExistsError):
                preferences.save({'revision': first['revision'], 'preferences': {'size':110}})
            restored = preferences.save({'revision': saved['revision'], 'reset':True})
            self.assertEqual(restored['preferences'], preferences.DEFAULTS)
            with self.assertRaises(ValueError):
                preferences.save({'revision':restored['revision'], 'preferences':{'size':9000}})

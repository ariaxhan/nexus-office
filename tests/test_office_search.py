import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import office_search as search

class Search(unittest.TestCase):
    def setUp(self):
        observed=patch.object(search.inventory,'declared',return_value=[])
        observed.start();self.addCleanup(observed.stop)

    def test_full_text_beyond_first_chunk_and_honest_binary_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            (root/'code.py').write_text('a '*40000+'\nrare_needle=42\n')
            (root/'picture.png').write_bytes(b'\x00binary')
            (root/'.env').write_text('credential_should_never_appear')
            roots={'r':{'id':'r','name':'repo','path':str(root)}}
            with patch.dict(os.environ, OFFICE_STATE=str(root/'state')), patch.object(search.objects,'roots',return_value=roots), patch.object(search,'path',return_value=root/'index.sqlite'), patch.object(search.archives,'records',return_value=()), patch.object(search.bot_history,'records',return_value=()), patch.object(search,'media_records',return_value=()), patch.object(search.projection,'records',return_value=()):
                search.LOCK.acquire()
                search.rebuild()
                result=search.search('rare_needle')
                self.assertEqual(result['total'],1)
                self.assertIn('rare_needle',result['items'][0]['excerpt'])
                self.assertEqual(search.search('credential_should_never_appear')['total'],0)
                image=search.search('picture.png')['items'][0]
                self.assertEqual(image['coverage'],'name only: binary')
                self.assertIsNone(result['next_cursor'])
                (root/'code.py').unlink()
                search.LOCK.acquire();search.rebuild()
                self.assertEqual(search.search('rare_needle')['total'],0)

    def test_cursor_never_continues_changed_generation_or_query(self):
        self.assertEqual(search.cursor_offset('123:40:abc','123','abc'),40)
        for cursor in ('123:40:abc','40'):
            with self.assertRaises(FileExistsError):search.cursor_offset(cursor,'124','abc')
        with self.assertRaises(FileExistsError):search.cursor_offset('123:40:abc','123','def')

    def test_unchanged_file_reuses_words_and_changed_file_replaces_them(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);work=base/'work';work.mkdir();file=work/'note.md'
            file.write_text('originalneedle')
            root={'id':'r','name':'repo','path':str(work)}
            with patch.object(search,'path',return_value=base/'index.sqlite'):
                db=search.connect()
                try:
                    search.index_file(db,root,file)
                    count=db.execute('SELECT count(*) FROM words').fetchone()[0]
                    with patch.object(search,'file_record',side_effect=AssertionError('unchanged content was read again')):
                        search.index_file(db,root,file)
                    self.assertEqual(db.execute('SELECT count(*) FROM words').fetchone()[0],count)
                    file.write_text('replacedneedle')
                    search.index_file(db,root,file)
                    self.assertEqual(db.execute("SELECT count(*) FROM words WHERE words MATCH 'originalneedle'").fetchone()[0],0)
                    self.assertGreater(db.execute("SELECT count(*) FROM words WHERE words MATCH 'replacedneedle'").fetchone()[0],0)
                finally:db.close()

    def test_unchanged_projection_preserves_fts_rows_and_owner_lookup_is_indexed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(search,'path',return_value=Path(directory)/'index.sqlite'):
            db=search.connect()
            try:
                row={'id':'projection:event:1','title':'Event','path':'','project':'Office','kind':'event','body':'original','modified':1,'coverage':'full'}
                search.insert(db,row)
                with patch.object(search,'delete_words',side_effect=AssertionError('unchanged projection replaced')):
                    search.insert(db,row)
                plan=db.execute('EXPLAIN QUERY PLAN SELECT word_rowid FROM word_owners WHERE object_id=?',(row['id'],)).fetchall()
                self.assertTrue(any('INDEX word_owners_object' in item['detail'] for item in plan))
                search.insert(db,dict(row,body='replacement'))
                self.assertEqual(db.execute('SELECT count(*) FROM words').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT count(*) FROM word_owners').fetchone()[0],1)
            finally:db.close()

    def test_existing_fts_rows_gain_owner_mapping_once(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as directory, patch.object(search,'path',return_value=Path(directory)/'index.sqlite'):
            with sqlite3.connect(search.path()) as old:
                old.execute('CREATE VIRTUAL TABLE words USING fts5(id UNINDEXED,title,path,body)')
                old.execute("INSERT INTO words VALUES ('legacy','Title','','old content')")
            db=search.connect()
            try:
                self.assertEqual(db.execute('SELECT object_id FROM word_owners').fetchone()[0],'legacy')
                search.delete_words(db,'legacy')
                self.assertEqual(db.execute('SELECT count(*) FROM words').fetchone()[0],0)
                db.commit()
            finally:db.close()
            db=search.connect()
            try:self.assertEqual(db.execute('SELECT count(*) FROM word_owners').fetchone()[0],0)
            finally:db.close()


    def test_embedded_media_is_streamed_out_without_losing_surrounding_text(self):
        import io
        prefix='before '*9360+'<img src="'
        source=prefix+'data:image/png;base64,'+'QUJD'*100000+'"> afterneedle'
        result=''.join(search.searchable_chunks(io.StringIO(source)))
        self.assertEqual(result,prefix+'data:image/png;base64,'+'"> afterneedle')
        self.assertLess(len(result),len(source)/4)

    def test_plain_source_text_is_preserved_across_chunk_boundaries(self):
        import io
        source=('ordinary code and words 🌿 '+('z'*300)+'\n')*700
        self.assertEqual(''.join(search.searchable_chunks(io.StringIO(source))),source)


    def test_unquoted_data_uri_does_not_consume_following_line(self):
        import io
        source='data:image/png;base64,QUJD\nAFTERNEEDLE\n'
        self.assertEqual(''.join(search.searchable_chunks(io.StringIO(source))),'data:image/png;base64,\nAFTERNEEDLE\n')

    def test_json_escaped_newlines_do_not_leak_encoded_payload(self):
        import io
        source='{"data_base64":"'+'QUJD'*10000+'\\n'+'REVG'*10000+'","text":"readable"}'
        self.assertEqual(''.join(search.searchable_chunks(io.StringIO(source))),'{"data_base64":"","text":"readable"}')

    def test_projection_body_also_excludes_binary_payload(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(search,'path',return_value=Path(directory)/'index.sqlite'):
            db=search.connect()
            try:
                row={'id':'event','title':'Event','path':'','project':'Office','kind':'event','body':'{"data_base64":"'+'QUJD'*10000+'","text":"visible"}','modified':1,'coverage':'full'}
                search.insert(db,row)
                self.assertLess(len(db.execute('SELECT body FROM objects').fetchone()[0]),100)
                self.assertEqual(db.execute("SELECT count(*) FROM words WHERE words MATCH 'visible'").fetchone()[0],1)
            finally:db.close()

    def test_low_storage_rolls_back_and_preserves_committed_search(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory, patch.object(search,'path',return_value=Path(directory)/'index.sqlite'), patch.object(search.objects,'roots',return_value={}), patch.object(search.archives,'records',return_value=()), patch.object(search.bot_history,'records',return_value=()), patch.object(search,'media_records',return_value=()), patch.object(search.projection,'records',return_value=()):
            row={'id':'old','title':'Old','path':'','project':'Office','kind':'event','body':'retainedneedle','modified':1,'coverage':'full'}
            db=search.connect()
            try:
                search.insert(db,row);db.commit()
            finally:db.close()
            with patch.object(search,'STORAGE_CHECK_AT',0), patch.object(search.time,'monotonic',return_value=10), patch.object(search.shutil,'disk_usage',return_value=SimpleNamespace(free=4*1024**3)):
                search.LOCK.acquire()
                search.rebuild([dict(row,id='new',body='discardedneedle')])
            self.assertEqual(search.STATUS['state'],'error')
            self.assertIn('5 GB',search.STATUS['error'])
            db=search.connect()
            try:
                self.assertEqual(db.execute('SELECT id FROM objects').fetchall()[0][0],'old')
                self.assertEqual(db.execute("SELECT count(*) FROM words WHERE words MATCH 'retainedneedle'").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT count(*) FROM words WHERE words MATCH 'discardedneedle'").fetchone()[0],0)
            finally:db.close()

    def test_ready_is_not_visible_until_commit_finishes(self):
        from unittest.mock import MagicMock
        for fails in (False,True):
            db=MagicMock();db.__enter__.return_value=db
            def commit(*args):
                self.assertEqual(search.STATUS['state'],'indexing')
                if fails:raise OSError('commit failed')
            db.__exit__.side_effect=commit
            with patch.object(search,'connect',return_value=db), patch.object(search,'source_coverage',return_value=[]), patch.object(search.objects,'roots',return_value={}), patch.object(search.archives,'records',return_value=()), patch.object(search.bot_history,'records',return_value=()), patch.object(search,'media_records',return_value=()), patch.object(search.projection,'records',return_value=()):
                search.LOCK.acquire();search.rebuild()
            self.assertEqual(search.STATUS['state'],'error' if fails else 'ready')

    def test_committed_coverage_reuses_generation_summary(self):
        from unittest.mock import MagicMock
        import json
        db=MagicMock();db.execute.return_value.fetchone.return_value=(json.dumps({'generation':12,'sources':[{'source':'repo','indexed':42}]}),)
        result=search.source_coverage(db,{'finished_at':12})
        self.assertEqual(result[0]['indexed'],42)
        self.assertEqual(db.execute.call_count,1)
        self.assertNotIn('GROUP BY',db.execute.call_args.args[0])

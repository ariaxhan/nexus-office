from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_projection as projection
import office_search as search

class CurrentProjection(unittest.TestCase):
    def test_task_open_refreshes_owner_and_rejects_changed_continuation(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);ledger=base/'ledger.sqlite'
            with sqlite3.connect(ledger) as db:
                db.executescript("CREATE TABLE tasks(id TEXT,title TEXT,state TEXT); CREATE TABLE flights(id TEXT,task_id TEXT,state TEXT,created_at REAL); INSERT INTO tasks VALUES('task_x','Current title','running');")
            def connect():
                db=sqlite3.connect(ledger);db.row_factory=sqlite3.Row;return db
            row=projection.record('task','Old title','task_x','stale body','Nexus')
            with patch.object(search,'path',return_value=base/'search.sqlite'),patch.object(projection.office_system,'connect',side_effect=connect):
                db=search.connect();search.insert(db,row);db.commit();db.close()
                first=search.object_detail(row['id'])
                self.assertEqual(first['title'],'Current title');self.assertIn('Current title',first['text']);self.assertNotIn('stale body',first['text'])
                with sqlite3.connect(ledger) as db:db.execute("UPDATE tasks SET state='done'")
                with self.assertRaises(FileExistsError):search.object_detail(row['id'],10,first['revision'])
                self.assertIn('done',search.object_detail(row['id'])['text'])
                with sqlite3.connect(ledger) as db:db.execute('DELETE FROM tasks')
                with self.assertRaises(FileNotFoundError):search.object_detail(row['id'])

    def test_github_result_routes_to_current_provider_reader(self):
        row=projection.record('github','Old title','owner/repo#123','old discussion','owner/repo')
        self.assertEqual(projection.current(row)['target'],{'kind':'github','repo':'owner/repo','number':123})

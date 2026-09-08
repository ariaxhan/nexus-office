import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_projection as projection
import office_search as search

class LogSearch(unittest.TestCase):
    def test_registered_job_full_text_is_indexed_and_unregistered_logs_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();logs=root/'_meta/logs/jobs';logs.mkdir(parents=True)
            (logs/'daily.log').write_text('padding '*12000+'late_log_needle')
            (logs/'unregistered.log').write_text('not_owned')
            errors=[]
            with patch.object(projection.office_jobs.objects,'vault',return_value=root),patch.object(projection.office_jobs.clock,'read_registry',return_value=({'daily':{}},[])),patch.object(search,'path',return_value=root/'index.sqlite'):
                rows=list(projection.job_log_records(errors));self.assertEqual([row['id'] for row in rows],['job-log:daily'])
                db=search.connect()
                try:
                    self.assertEqual(search.index_projection(db,rows[0],errors),1)
                    self.assertGreater(db.execute("SELECT count(*) FROM words WHERE words MATCH 'late_log_needle'").fetchone()[0],0)
                finally:db.close()
            self.assertFalse(errors)

    def test_ledger_flight_and_lane_keys_reopen_exact_owner_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();ledger=root/'ledger.sqlite';flights=root/'flights';base=flights/'flt_abc';(base/'lanes').mkdir(parents=True)
            (base/'log').write_text('main');(base/'lanes/review.out').write_text('lane')
            with sqlite3.connect(ledger) as db:db.execute('CREATE TABLE flights(id TEXT)');db.execute("INSERT INTO flights VALUES ('flt_abc')")
            with patch.object(projection.office_system.run_board,'LEDGER',ledger),patch.object(projection.office_system.run_board,'FLIGHTS',flights):
                rows=list(projection.flight_log_records([]))
            self.assertEqual([json.loads(row['id'][11:]) for row in rows],[['flt_abc',''],['flt_abc','review.out']])

    def test_linked_log_is_reported_not_indexed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();source=root/'real.log';source.write_text('private');link=root/'linked.log';link.symlink_to(source)
            errors=[]
            self.assertIsNone(projection.log_record('id','title',link,'System',errors))
            self.assertEqual(len(errors),1)

    def test_invalid_registry_identity_never_indexes_outside_owner_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();logs=root/'_meta/logs';logs.mkdir(parents=True);(logs/'outside.log').write_text('must not index')
            errors=[]
            with patch.object(projection.office_jobs.objects,'vault',return_value=root),patch.object(projection.office_jobs.clock,'read_registry',return_value=({'../outside':{}},[])):
                self.assertEqual(list(projection.job_log_records(errors)),[])
            self.assertEqual(len(errors),1)

    def test_index_and_reader_share_flight_and_lane_validation(self):
        errors=[]
        self.assertFalse(projection.valid_owner('../outside',projection.office_system.validate_flight,'Nexus',errors))
        self.assertFalse(projection.valid_owner('nested/output.out',projection.office_system.validate_lane,'Nexus',errors))
        self.assertFalse(projection.valid_owner('spaces here.out',projection.office_system.validate_lane,'Nexus',errors))
        self.assertTrue(projection.valid_owner('review.out',projection.office_system.validate_lane,'Nexus',errors))
        self.assertEqual(len(errors),3)

"""Migration must retain WAL locks across another process opening/closing."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from nexus.ledger import Ledger

class ProcessVisibility(unittest.TestCase):
    def test_migration_backup_preserves_other_process_wal_visibility(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'ledger.sqlite')
            with closing(Ledger(path)) as owner:
                owner.event('before-child')
                script="from nexus.ledger import Ledger; import sys; db=Ledger(sys.argv[1]); db.event('child'); db.close()"
                subprocess.run([sys.executable,'-c',script,path],check=True,
                               cwd=Path(__file__).resolve().parents[1],timeout=30)
                owner.event('after-child')
                with closing(Ledger(path)) as reader:
                    self.assertEqual([r['kind'] for r in reader.events()],
                                     ['ledger.migrated','before-child','child','after-child'])
                    self.assertEqual(reader.conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')

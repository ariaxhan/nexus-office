import base64
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_uploads as uploads
from nexus.office_agent import materialize_attachment

class Uploads(unittest.TestCase):
    def test_binary_upload_receipt_retry_and_exact_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();folder=root/'context';folder.mkdir()
            raw=b'\x89PNG\x00photo fixture'
            with patch.object(uploads.run_board,'LEDGER',root/'ledger.sqlite'):
                body={'name':'photo.png','base64':base64.b64encode(raw).decode()}
                first=uploads.upload(body);self.assertEqual(uploads.upload(body),first)
                attachment=uploads.resolve({key:first[key] for key in ('id','revision')})
                materialize_attachment(folder,attachment)
                target=folder/attachment['name'];self.assertEqual(target.read_bytes(),raw)
                target.chmod(0o600);target.write_bytes(b'changed')
                with self.assertRaises(ValueError):materialize_attachment(folder,attachment)

    def test_path_and_revision_rejections(self):
        with self.assertRaises(ValueError):uploads.upload({'name':'../private','base64':''})
        with self.assertRaises(ValueError):uploads.upload({'name':'file','base64':'invalid?'})
        with self.assertRaises(ValueError):uploads.resolve({'id':'upload:../private','revision':'wrong'})

    def test_followup_attachment_receipt_is_part_of_dedupe(self):
        from contextlib import closing
        from nexus.ledger import Ledger
        from nexus import office_tasks
        with tempfile.TemporaryDirectory() as directory,closing(Ledger(str(Path(directory)/'ledger.sqlite'))) as ledger:
            spec={'engine':'codex','profile':'personal','project':{'id':'fixture'},'prompt':'Read attachment'}
            task=office_tasks.submit(ledger,'request-12345678',spec)['task_id']
            payload={'text':'Read this','attachments':[{'revision':'abc','name':'file'}]}
            first=office_tasks.message_payload(ledger,task,'message-12345678',payload)
            self.assertEqual(first,office_tasks.message_payload(ledger,task,'message-12345678',payload))
            with self.assertRaises(FileExistsError):office_tasks.message_payload(ledger,task,'message-12345678',dict(payload,attachments=[]))

from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_github_context as context

class Context(unittest.TestCase):
    def test_exact_file_selection_preserves_source_identity(self):
        body={'repo':'owner/repo','path':'README.md','ref':'a'*40,'blob':'b'*40,'start_line':2,'end_line':3}
        with patch.object(context.github,'tree',return_value={'object':{'readable':True,'sha':'b'*40,'text':'one\ntwo\nthree\nfour'}}):
            text,source=context.file_context(None,[],body)
        self.assertEqual(text,'two\nthree\n');self.assertEqual(source['commit'],'a'*40);self.assertEqual(source['start_line'],2)

    def test_blob_mismatch_and_invalid_selection_reject(self):
        body={'repo':'owner/repo','path':'README.md','ref':'a'*40,'blob':'b'*40}
        with patch.object(context.github,'tree',return_value={'object':{'readable':True,'sha':'c'*40,'text':'one'}}):
            with self.assertRaises(FileExistsError):context.file_context(None,[],body)
        body.update(start_line=True,end_line=1)
        with patch.object(context.github,'tree',return_value={'object':{'readable':True,'sha':'b'*40,'text':'one'}}):
            with self.assertRaises(ValueError):context.file_context(None,[],body)

    def test_changed_base_after_diff_rejects_snapshot(self):
        body={'number':1,'head':'a'*40,'base':'b'*40}
        before={'head':{'sha':'a'*40},'base':{'sha':'b'*40}}
        after={'head':{'sha':'a'*40},'base':{'sha':'c'*40}}
        with patch.object(context.github,'fresh',side_effect=[(before,1),(after,2)]),patch.object(context.github,'fetch',return_value=('diff',1)):
            with self.assertRaises(FileExistsError):context.change_context('owner/repo','seat','fixture',body)

    def test_snapshot_uses_existing_upload_receipt_with_provenance_inside_bytes(self):
        import base64,json
        body={'kind':'file','repo':'owner/repo'}
        with patch.object(context.github,'read_identity',return_value=('seat','fixture')),patch.object(context,'file_context',return_value=('chosen text',{'commit':'a'*40})),patch.object(context.uploads,'upload',return_value={'id':'upload:one','revision':'hash'}) as upload:
            data=context.snapshot(None,[],body)
        text=base64.b64decode(upload.call_args.args[0]['base64']).decode()
        self.assertIn('chosen text',text);self.assertIn('a'*40,text)
        self.assertEqual(data['attachment']['id'],'upload:one')

from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import chat,office_uploads

class BotUploads(unittest.TestCase):
    def test_validated_receipts_are_forwarded_without_inline_file_bytes(self):
        room=chat.Chatroom();refs=[{'id':'upload:'+'a'*64,'revision':'b'*64}]
        with patch.object(office_uploads,'attachments',return_value=[{'name':'fixture.txt'}]),patch.object(chat,'read_bots',return_value=[{'id':'relay'}]),patch.object(chat.threading,'Thread') as thread:
            status,_=room.say({'bot':'relay','message':'','uploads':refs})
        self.assertEqual(status,202);self.assertEqual(thread.call_args.kwargs['args'][-1],refs)
        with patch.object(chat.rt,'post',return_value={}) as post:
            room._turn('relay','',(),refs)
        self.assertEqual(post.call_args.args[1]['uploads'],refs)
        self.assertNotIn('attachments',post.call_args.args[1])

    def test_invalid_receipt_is_refused_before_queue(self):
        room=chat.Chatroom()
        with patch.object(office_uploads,'attachments',side_effect=ValueError('Invalid receipt')),patch.object(chat.threading,'Thread') as thread:
            status,data=room.say({'bot':'relay','message':'read','uploads':[{}]})
        self.assertEqual(status,400);self.assertIn('receipt',data['error']);thread.assert_not_called()

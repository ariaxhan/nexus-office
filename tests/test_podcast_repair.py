from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
from nexus import podcast_daily as daily,podcast_render as render

class NarrationRepair(unittest.TestCase):
    def test_bad_passage_is_repaired_before_quality_can_pass(self):
        with patch.object(daily.podcast_quality,'verify',side_effect=[ValueError('bad'),{'passed':True}]) as verify,patch.object(daily.podcast_quality,'failed_passages',return_value=[14]),patch.object(render,'repair_passages') as repair,patch.object(daily,'encode_audio') as encode:
            result=daily.validated_audio(Path('/fixture'),Path('/voice'))
        self.assertTrue(result['passed']);self.assertEqual(verify.call_count,2)
        repair.assert_called_once_with(Path('/fixture'),Path('/voice'),[14]);encode.assert_called_once()

    def test_repair_keeps_rejected_audio_and_changes_seed_with_hard_limit(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(render,'render'):
            root=Path(directory);chunks=root/'episode-chunks';chunks.mkdir();wave=chunks/'014.wav'
            wave.write_bytes(b'first rejected audio');render.repair_passages(root,Path('/voice'),[14])
            self.assertEqual((root/'repair-attempts/014-attempt-1.wav').read_bytes(),b'first rejected audio')
            wave.write_bytes(b'second rejected audio');render.repair_passages(root,Path('/voice'),[14])
            with self.assertRaises(ValueError):render.repair_passages(root,Path('/voice'),[14])

import hashlib,json
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nexus import podcast_publish as publish

class Publication(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.audio=self.root/'episode.mp3';self.audio.write_bytes(b'audio fixture')
        self.script=self.root/'script.txt';self.script.write_text('word '*4000)
        self.editorial={'title':'Fixture','description':'Private test','sources':[]}
        self.chapters=[{'title':'Start','start_s':0}]
        self.qa={'passed':True,'audio_sha256':hashlib.sha256(self.audio.read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(self.script.read_bytes()).hexdigest()}
        (self.root/'quality.json').write_text(json.dumps(self.qa))
        self.duration=patch.object(publish,'duration',return_value=1500);self.duration.start();self.addCleanup(self.duration.stop)
        self.decoder=patch.object(publish.subprocess,'run');self.decoder.start();self.addCleanup(self.decoder.stop)

    def test_manifest_written_once_with_content_receipt(self):
        args=(self.root,self.editorial,self.audio,self.script,self.chapters,'2026-09-08')
        first=publish.publish(*args);second=publish.publish(*args)
        self.assertEqual(first,second)
        self.assertEqual(len(json.loads((self.root/'manifest.json').read_text())['episodes']),1)
        self.assertEqual(first['voice'],'qwen-wiry-professor')
        self.assertEqual(first['duration_s'],1500)

    def test_failed_quality_or_changed_audio_never_publishes(self):
        self.qa['passed']=False;(self.root/'quality.json').write_text(json.dumps(self.qa))
        with self.assertRaises(ValueError):publish.validate(self.audio,self.script)
        self.qa['passed']=True;(self.root/'quality.json').write_text(json.dumps(self.qa));self.audio.write_bytes(b'changed')
        with self.assertRaises(ValueError):publish.validate(self.audio,self.script)
        self.assertFalse((self.root/'manifest.json').exists())

    def test_short_episode_refused(self):
        with patch.object(publish,'duration',return_value=1199):
            with self.assertRaises(ValueError):publish.validate(self.audio,self.script)

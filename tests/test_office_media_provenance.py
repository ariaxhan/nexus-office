import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))
import office_media as media

class Provenance(unittest.TestCase):
    def test_poem_provenance_links_current_sources_without_inventing_run_or_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'_meta/services/taper-site';(base/'pieces').mkdir(parents=True)
            (base/'pieces/nested').mkdir()
            path=base/'pieces/nested/2026-09-08-pulse.html';path.write_text('<section>poem</section>')
            (base/'pieces/manifest.json').write_text('{}');(base/'README.md').write_text('pipeline')
            (root/'_meta/services/registry.json').write_text(json.dumps({'jobs':[{'id':'midday-pulse','taper_slug':'pulse'},{'id':'other','taper_slug':'morning'},{'id':'unrelated'}]}))
            with patch.object(media.objects,'vault',return_value=root), patch.object(media,'meta_reference',side_effect=lambda value:value), patch.object(media.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='abc\n2026-09-08T00:00:00Z\nAdd poem\n')):
                result=media.poem_provenance(path,{'type':'pulse'})
                unspecified=media.poem_provenance(path,{})
            self.assertEqual(result['configured_producers'],['midday-pulse'])
            self.assertEqual(unspecified['configured_producers'],[])
            self.assertIn('pieces/nested/',result['source'])
            self.assertEqual(result['source_commit']['sha'],'abc')
            self.assertIn(path.name,result['source'])
            self.assertIn('not verified',result['publication'])
            self.assertIn('No exact',result['generation_receipt'])
            self.assertEqual(len(result['sha256']),64)

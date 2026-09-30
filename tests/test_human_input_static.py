"""CI guard: one durable human-ownership creation path and no legacy producers."""
import ast
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=[*ROOT.joinpath('client').rglob('*.py'),*ROOT.joinpath('nexus').rglob('*.py')]


class SingleCreationPath(unittest.TestCase):
    def test_browser_bundle_matches_the_reviewed_human_input_source(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'office-bundle.js'
            subprocess.run([str(ROOT/'node_modules/.bin/esbuild'),
                            'client/phone/office.js','--bundle','--format=esm',
                            '--target=safari17',f'--outfile={output}'],
                           cwd=ROOT,check=True,capture_output=True)
            self.assertEqual(output.read_bytes(),(ROOT/'client/phone/office-bundle.js').read_bytes(),
                             'run npm run bundle and land the generated browser code')

    def test_only_request_human_input_can_write_human_owned_rows(self):
        creators=[]
        for path in SOURCE:
            tree=ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.FunctionDef) and node.name=='request_human_input':
                    creators.append((path.relative_to(ROOT).as_posix(),node.lineno))
                if not isinstance(node,ast.Constant) or not isinstance(node.value,str):
                    continue
                sql=node.value
                if re.search(r'(?i)\bINSERT\s+INTO\s+asks\b|\bUPDATE\s+asks\s+SET\s+owner\s*=\s*[\x27\x22]aria',sql):
                    self.assertEqual(path,ROOT/'client/human_asks.py',f'direct human ownership write: {path}:{node.lineno}')
        self.assertEqual(len(creators),1,creators)
        self.assertEqual(creators[0][0],'client/human_asks.py')
        self.assertNotIn("owner='aria'",(ROOT/'client/human_asks.py').read_text().split('def request_human_input',1)[1])

    def test_legacy_bypasses_cannot_return(self):
        forbidden=('human_asks.observe(', 'human_asks.declarations(',
                    'human_ask_declarations', "office.permission'",
                    'needs_you = True', "return 'needs_you'",
                    'create_human_ask(', 'needs_you.append(',
                    'waiting_on_aria', 'needs_aria', 'needs_approval',
                    'waiting_for_approval', 'blocked_on_user',
                    'ask_aria(', 'hand_back_to_user')
        for path in SOURCE:
            if path.name=='human_asks.py':
                continue
            content=path.read_text()
            for phrase in forbidden:
                with self.subTest(path=str(path),phrase=phrase):
                    self.assertNotIn(phrase,content)
        office=(ROOT/'client/phone/office.js').read_text()
        self.assertIn("items.push(...(result.value.items||[]).map(item=>({kind:'human-ask',item})))",office)
        self.assertEqual(office.count("kind:'human-ask'"),1)
        self.assertNotIn("items.push(...(result.value.stations",office)
        self.assertNotIn("kind:'gate'",office)
        self.assertNotIn("kind:'buzz'",office)
        chat=(ROOT/'client/chat.py').read_text()
        self.assertIn("return {item['source_ref'] for item in human_asks.listing()['items']}",chat)
        self.assertNotIn('waiting_count += "waiting on human" in labels',chat)
        self.assertNotIn('waiting on you', (ROOT/'client/office-sync.py').read_text())
        self.assertNotIn('waiting on you', (ROOT/'client/automation.py').read_text())
        native=(ROOT/'app/Office/Model/Store.swift').read_text()
        self.assertNotIn('if wallNeeds > 0 { return .needsYou }',native)
        self.assertNotIn('if stations.contains(where: { StateRules.deskState($0) == .waiting }) { return .needsYou }',native)


if __name__=='__main__':
    unittest.main()

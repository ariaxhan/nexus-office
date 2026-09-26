"""A flight exports only artifacts produced by its own attempt."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'client'))


class AttemptArtifacts(unittest.TestCase):
    def test_failed_capture_never_exports_an_earlier_attempts_patch(self):
        import subprocess
        from nexus.office_agent import Conversation
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);output=root/'output';output.mkdir()
            (root/'changes.patch').write_text('stale patch from attempt one\n')
            (output/'changes.patch').write_text('stale patch from attempt one\n')
            conversation=Conversation(None,{'id':'flight_two','task_id':'task_fixture'},root,root,{'source_revision':'fixture'})
            conversation.emit=lambda kind,payload:None
            with patch('nexus.office_agent.changes',side_effect=subprocess.CalledProcessError(128,'git')),\
                 patch('nexus.office_agent.Path.cwd',return_value=output):
                conversation.save_outputs()
            self.assertFalse((output/'changes.patch').exists())
            self.assertFalse((root/'changes.patch').exists())


if __name__=='__main__':
    unittest.main()

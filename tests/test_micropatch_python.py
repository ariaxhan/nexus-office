"""The benchmark verifier finds a Python 3.12 even when Homebrew drops it from PATH."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import micropatch as worker  # noqa: E402


class TestPython(unittest.TestCase):
    def test_override_wins(self):
        self.assertEqual("/x/py", worker.test_python({"OFFICE_TEST_PYTHON": "/x/py"}, lambda n: None, lambda: "/uv/py"))

    def test_path_python_used_when_present(self):
        self.assertEqual("python3.12", worker.test_python({}, lambda n: "/bin/" + n, lambda: "/uv/py"))

    def test_uv_copy_used_when_path_has_none(self):
        self.assertEqual("/uv/py", worker.test_python({}, lambda n: None, lambda: "/uv/py"))


if __name__ == "__main__":
    unittest.main()

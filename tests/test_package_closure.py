"""Every module a nexus file imports ships in this tree (#308 review: cli/tower imported a dispatch
module the landed tree did not hold, so every `python3 -m nexus` command died on ImportError)."""

import ast
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tracked():
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "nexus"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return {p for p in out if os.path.exists(os.path.join(ROOT, p))}


class PackageClosure(unittest.TestCase):
    def test_every_relative_import_is_a_file_in_the_tree(self):
        tracked = _tracked()
        missing = []
        for path in sorted(p for p in tracked if p.endswith(".py") and p.count("/") == 1):
            tree = ast.parse(open(os.path.join(ROOT, path)).read(), path)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.ImportFrom) and node.level == 1):
                    continue
                names = [node.module] if node.module else [a.name for a in node.names]
                for name in names:
                    base = "nexus/" + name.split(".")[0]
                    if base + ".py" not in tracked and base + "/__init__.py" not in tracked:
                        missing.append(f"{path}: {name}")
        self.assertEqual(missing, [])

    def test_cli_and_tower_import_and_dispatch_parses(self):
        r = subprocess.run([sys.executable, "-c", "import nexus.cli, nexus.tower, nexus.granola"], cwd=ROOT,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, "-m", "nexus", "dispatch", "granola", "--help"], cwd=ROOT,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()

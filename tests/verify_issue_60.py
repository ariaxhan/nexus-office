"""Focused phone sessions behavior check for issue 60."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
subprocess.run(['node', '--test', 'tests/issue_60.test.mjs'], cwd=ROOT, check=True)

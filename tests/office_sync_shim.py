"""Import `client/office-sync.py` under a name Python can actually say.

The file is hyphenated because it is a command first and a module second, which
is right for something you type. A test still has to reach inside it, so this
loads it by path rather than renaming the command everybody runs.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLIENT = ROOT / "client"
sys.path.insert(0, str(CLIENT))

_spec = importlib.util.spec_from_file_location("office_sync", CLIENT / "office-sync.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

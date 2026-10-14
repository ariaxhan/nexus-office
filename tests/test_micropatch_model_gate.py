"""The MacBook small-model gate (#235; FAULT-BOOK F-001..F-003).

Aria's ruling (2026-10-10): no big Ollama models on the MacBook at all — it does not have the
capacity; big models run on the Studio. The gate refuses before any Ollama request is made.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
HARNESS_SRC = ROOT.parents[1] / "the-tradition-harness" / "src"
if HARNESS_SRC.is_dir():
    sys.path.insert(0, str(HARNESS_SRC))

import micropatch as mp  # noqa: E402
from nexus import executor  # noqa: E402

try:
    import micropatch_tradition as mt  # noqa: E402
except Exception:  # noqa: BLE001 - the harness worker is only importable in its own environment
    mt = None

MACBOOK = "arias-MacBook-Pro-2"
STUDIO = "arias-Mac-Studio"
BIG = ("qwen3-coder:30b", "qwen3.8:27b", "devstral-small-2:24b")


class Gate(unittest.TestCase):
    def test_macbook_refuses_big_models(self):
        for model in BIG:
            with self.assertRaises(mp.BigModelRefused, msg=model):
                mp.ensure_model_allowed(model, host=MACBOOK)

    def test_refusal_names_the_model_the_ruling_and_the_allowlist(self):
        refusal = mp.model_refusal("qwen3-coder:30b", host=MACBOOK)
        self.assertIn("qwen3-coder:30b", refusal)
        self.assertIn("Aria's ruling", refusal)
        self.assertIn("lfm2.5:8b", refusal)

    def test_macbook_allows_the_small_allowlist(self):
        for model in mp.SMALL_MODEL_ALLOWLIST:
            self.assertIsNone(mp.model_refusal(model, host=MACBOOK))
            mp.ensure_model_allowed(model, host=MACBOOK)  # must not raise

    def test_macbook_refuses_anything_unlisted(self):
        self.assertIsNotNone(mp.model_refusal("some-new-model:9b", host=MACBOOK))

    def test_other_hosts_are_unrestricted(self):
        for model in BIG + ("anything:1b",):
            self.assertIsNone(mp.model_refusal(model, host=STUDIO))
            mp.ensure_model_allowed(model, host=STUDIO)  # must not raise

    def test_host_detection_agrees_between_caller_and_prompt(self):
        # Both gates key on the same LocalHostName jobctl host-pinning uses.
        self.assertEqual(mp.local_host_name(), executor.local_host_name())


class SolveGate(unittest.TestCase):
    @unittest.skipUnless(mt is not None, "tradition harness not importable in this environment")
    def test_solve_refuses_a_big_model_before_any_work(self):
        case = {"commit": "0" * 40, "overlay": [], "tests": [], "path": "scripts/micropatch.py"}
        with patch.object(mp, "local_host_name", return_value=MACBOOK):
            with self.assertRaises(mp.BigModelRefused):
                mt.solve(case, "qwen3-coder:30b", "cold")


class PromptClause(unittest.TestCase):
    def test_macbook_prompt_restricts_the_local_worker(self):
        text = executor.local_implementation(host=MACBOOK)
        self.assertIn("tradition implement --contract", text)  # base instruction intact
        self.assertIn("--model lfm2.5:8b", text)
        self.assertIn("Aria's ruling", text)

    def test_other_host_prompt_is_unchanged(self):
        self.assertEqual(executor.LOCAL_IMPLEMENTATION, executor.local_implementation(host=STUDIO))


if __name__ == "__main__":
    unittest.main()

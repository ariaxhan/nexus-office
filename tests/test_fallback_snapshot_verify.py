import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/fallback_snapshot_verify.py"
spec = importlib.util.spec_from_file_location("fallback_snapshot_verify", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class VerifyTest(unittest.TestCase):
    def test_command_cleans_inbox_after_accept_and_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = root / "key"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                            "-f", str(key)], check=True)
            allowed = root / "allowed"
            allowed.write_text("office-fallback " + (root / "key.pub").read_text())
            destination = root / "verified.json"
            for tamper in (False, True):
                snapshot = root / "world.json"
                from datetime import datetime, timezone
                stamp = datetime.now(timezone.utc).isoformat()
                snapshot.write_text(json.dumps({"generated": stamp, "stations": []}))
                subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key),
                                "-n", "office-fallback", str(snapshot)], check=True,
                               capture_output=True)
                signature = root / "world.json.sig"
                if tamper:
                    snapshot.write_text(snapshot.read_text().replace("[]", '[{"repo":"tampered"}]'))
                result = subprocess.run(["python3", str(SCRIPT), "--snapshot", str(snapshot),
                                         "--signature", str(signature), "--allowed-signers",
                                         str(allowed), "--destination", str(destination)],
                                        capture_output=True)
                self.assertEqual(result.returncode == 0, not tamper)
                self.assertFalse(snapshot.exists())
                self.assertFalse(signature.exists())
                self.assertTrue(destination.exists())

    def test_signature_freshness_and_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = root / "key"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                            "-f", str(key)], check=True)
            allowed = root / "allowed"
            allowed.write_text("office-fallback " + (root / "key.pub").read_text())
            snapshot = root / "world.json"
            snapshot.write_text(json.dumps({"generated": "2026-10-16T23:00:00Z",
                                            "stations": []}))
            subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key),
                            "-n", "office-fallback", str(snapshot)], check=True,
                           capture_output=True)
            signature = root / "world.json.sig"
            destination = root / "verified.json"
            stamp = module.verify(snapshot, signature, allowed, destination,
                                  now=1792191600)
            self.assertEqual(stamp, "2026-10-16T23:00:00Z")
            self.assertEqual(destination.read_bytes(), snapshot.read_bytes())
            wrong_principal = root / "wrong-principal"
            wrong_principal.write_text("someone-else " + (root / "key.pub").read_text())
            with self.assertRaises(ValueError):
                module.verify(snapshot, signature, wrong_principal,
                              root / "wrong-output.json", now=1792191600)
            other = root / "other"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                            "-f", str(other)], check=True)
            wrong_key = root / "wrong-key"
            wrong_key.write_text("office-fallback " + (root / "other.pub").read_text())
            with self.assertRaises(ValueError):
                module.verify(snapshot, signature, wrong_key,
                              root / "wrong-output.json", now=1792191600)
            wrong_namespace = root / "other-namespace"
            alternate = subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key),
                                        "-n", "different"], input=snapshot.read_bytes(),
                                       check=True, capture_output=True)
            wrong_namespace.write_bytes(alternate.stdout)
            with self.assertRaises(ValueError):
                module.verify(snapshot, wrong_namespace, allowed,
                              root / "wrong-output.json", now=1792191600)
            snapshot.write_text(snapshot.read_text().replace("[]", '[{"repo":"changed"}]'))
            with self.assertRaises(ValueError):
                module.verify(snapshot, signature, allowed, destination,
                              now=1792191600)
            self.assertIn(b"stations", destination.read_bytes())
            snapshot.write_text(json.dumps({"generated": "2026-10-16T23:00:00Z",
                                            "stations": []}))
            with self.assertRaises(ValueError):
                module.verify(snapshot, signature, allowed, destination,
                              now=1792192300)
            with self.assertRaises(ValueError):
                module.verify(snapshot, signature, allowed, destination,
                              now=1792191600)


if __name__ == "__main__":
    unittest.main()

"""The repository check for the sandbox plan: cadence, timeout, isolation from the core."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nexus import sandbox_probes as sp  # noqa: E402
from nexus.checkout_probe import Checkout  # noqa: E402
from nexus.ledger import Ledger, loads  # noqa: E402
from nexus.messaging import Destination, ProviderReceipt  # noqa: E402

TRANSACTIONAL = {
    "nexus.checkout_probe", "nexus.care_probe", "nexus.journeys", "nexus.messaging",
    "nexus.sandbox_probes", "checkout_probe", "care_probe", "journeys", "messaging",
    "sandbox_probes",
}
CORE = [ROOT / "nexus" / name for name in
        ("tower.py", "flights.py", "ledger.py", "cli.py", "radio.py", "landing.py",
         "repairs.py", "probes.py", "__main__.py")] + sorted((ROOT / "client").glob("*.py"))


def module_imports(path: pathlib.Path) -> set:
    """Imports at module level only: the core may not load a probe when it starts."""
    names = set()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(f"{node.module or ''}.{alias.name}".strip(".") for alias in node.names)
            names.update(alias.name for alias in node.names)
    return names


class CheckoutClient:
    def create_checkout(self, request):
        return Checkout("checkout-abcd", request["amount"], request["currency"],
                        request["success_redirect"], False, {"status": "open"})

    def expire_checkout(self, checkout_id):
        pass


def clients(env):
    assert set(env) == {sp.CLIENTS_ENV, "NEXUS_SANDBOX_KEY"}, env
    return {"checkout": CheckoutClient()}


class PlanBoundsTest(unittest.TestCase):
    def test_cadence_is_hourly_or_slower(self):
        self.assertEqual(3600.0, sp.plan_definition()["schedule"]["every"])
        self.assertEqual(7200.0, sp.plan_definition(7200)["schedule"]["every"])
        with self.assertRaises(ValueError):
            sp.plan_definition(300)

    def test_timeout_is_bounded_and_ends_before_the_next_run(self):
        self.assertEqual(1800.0, sp.plan_definition(timeout_s=1800)["budget"]["timeout_s"])
        for timeout in (0, 1801, 3601):
            with self.assertRaises(ValueError):
                sp.plan_definition(timeout_s=timeout)

    def test_install_adds_the_plan_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(f"{tmp}/ledger.sqlite")
            plan = ledger.plan(sp.install_plan(ledger))
            self.assertEqual(0, plan["enabled"])
            self.assertEqual({"every": 3600.0}, loads(plan["schedule"]))
            self.assertEqual(0, loads(plan["budget"])["max_retries"])
            self.assertIn("sandbox-probes run --timeout 600", loads(plan["inputs"])["cmd"])
            with self.assertRaises(ValueError):
                sp.install_plan(ledger)

    def test_configured_timeout_reaches_the_flight_command(self):
        self.assertIn("--timeout 17", sp.plan_definition(timeout_s=17)["inputs"]["cmd"])

    def test_cli_installs_the_disabled_plan_with_exact_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "ledger.sqlite"
            installed = subprocess.run(
                [sys.executable, "-m", "nexus", "--ledger", str(path),
                 "sandbox-probes", "install", "--every", "7200", "--timeout", "17"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(0, installed.returncode, installed.stderr)
            plan = Ledger(str(path)).plan_by_name(sp.PLAN_NAME)
            self.assertEqual(0, plan["enabled"])
            self.assertEqual({"every": 7200.0}, loads(plan["schedule"]))
            self.assertEqual(17.0, loads(plan["budget"])["timeout_s"])
            self.assertIn("--timeout 17", loads(plan["inputs"])["cmd"])


class IsolationTest(unittest.TestCase):
    def test_core_imports_no_transactional_probe(self):
        for path in CORE:
            self.assertFalse(module_imports(path) & TRANSACTIONAL, path.name)

    def test_only_sandbox_variables_reach_the_client_factory(self):
        environ = {sp.CLIENTS_ENV: f"{__name__}:clients", "NEXUS_SANDBOX_KEY": "sk_test",
                   "STRIPE_LIVE_KEY": "sk_live", "HOME": "/nowhere"}
        self.assertEqual({sp.CLIENTS_ENV, "NEXUS_SANDBOX_KEY"}, set(sp.sandbox_env(environ)))
        with self.assertRaisesRegex(ValueError, "client factory missing"):
            sp.load_clients(environ)

    def test_main_hides_live_environment_and_redacts_sandbox_secrets(self):
        secret = "sandbox-secret-value"
        module = sys.modules[__name__]
        previous = getattr(module, "isolated_clients", None)

        def isolated_clients(env):
            self.assertNotIn("STRIPE_LIVE_KEY", os.environ)
            self.assertEqual(secret, os.environ["NEXUS_SANDBOX_KEY"])
            raise RuntimeError(f"provider rejected {secret}")

        module.isolated_clients = isolated_clients
        output = io.StringIO()
        environ = {sp.CLIENTS_ENV: f"{__name__}:isolated_clients",
                   "NEXUS_SANDBOX_KEY": secret, "STRIPE_LIVE_KEY": "live-secret"}
        try:
            with contextlib.redirect_stdout(output):
                self.assertEqual(1, sp.main(environ, 30, pathlib.Path("unused")))
        finally:
            if previous is None:
                delattr(module, "isolated_clients")
            else:
                module.isolated_clients = previous
        report = json.loads(output.getvalue())
        self.assertEqual("provider rejected [redacted]", report["error"])
        self.assertNotIn(secret, output.getvalue())

    def test_all_transactional_clients_are_required(self):
        with self.assertRaisesRegex(ValueError, "care, journey, messaging"):
            sp.load_clients({sp.CLIENTS_ENV: f"{__name__}:clients",
                             "NEXUS_SANDBOX_KEY": "test"})

    def test_scheduler_runs_messaging_with_the_shared_run_id(self):
        class Messages:
            def __init__(self):
                self.receipts = {}

            def send(self, channel, destination, body, run_id):
                self.receipts[f"{channel}-id"] = [
                    ProviderReceipt("accepted", f"{channel}-accepted"),
                    ProviderReceipt("delivered", f"{channel}-delivered"),
                ]
                return f"{channel}-id"

            def wait_receipt(self, provider_id, timeout_s):
                return self.receipts[provider_id].pop(0)

        clients = {
            "messaging": Messages(),
            "messaging_destinations": {
                "kakao": Destination("kakao-sandbox"),
                "sms": Destination("15550000123"),
            },
        }
        rows = sp._probes(clients, "sandbox-fixed", 8, pathlib.Path("unused"))["messaging"]()
        self.assertEqual({"sandbox-fixed"}, {row["run_id"] for row in rows})
        self.assertEqual({"kakao", "sms"}, {row["channel"] for row in rows})

    def test_run_refuses_without_a_client_factory(self):
        for environ in ({}, {sp.CLIENTS_ENV: "nowhere"}):
            with self.assertRaises(ValueError):
                sp.load_clients(environ)


if __name__ == "__main__":
    unittest.main()

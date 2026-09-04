"""The one scheduler path for transactional probes: a slow, disabled-by-default plan.

The five-minute core (tower, flights, ledger, cli, the client door) never imports
this module or any transactional probe. Transactional work only happens inside a
flight of the `sandbox-probes` plan, which is added disabled, runs no faster than
hourly, and hands its client factory only `NEXUS_SANDBOX_*` variables, so a live
credential cannot reach a probe by accident. `tests/test_sandbox_probes.py` is
the repository check for all three bounds.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import os
import shlex
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import care_probe, checkout_probe, journeys

PLAN_NAME = "sandbox-probes"
MIN_EVERY_S = 3600.0
MAX_TIMEOUT_S = 1800.0
CLIENTS_ENV = "NEXUS_SANDBOX_CLIENTS"
ENV_PREFIX = "NEXUS_SANDBOX_"
REQUIRED_CLIENTS = frozenset({"checkout", "care", "journey", "browser_factory"})


def plan_definition(every_s: float = MIN_EVERY_S, timeout_s: float = 600.0) -> dict:
    """The plan row, bounded: hourly or slower, and a timeout that ends before the next run."""
    if every_s < MIN_EVERY_S:
        raise ValueError(f"cadence must be at least {MIN_EVERY_S:g}s, got {every_s:g}")
    if not 0 < timeout_s <= min(MAX_TIMEOUT_S, every_s):
        raise ValueError(f"timeout must be within (0, {min(MAX_TIMEOUT_S, every_s):g}]s")
    command = (f"{shlex.quote(sys.executable)} -m nexus sandbox-probes run "
               f"--timeout {timeout_s:g}")
    return {"name": PLAN_NAME, "kind": "script", "schedule": {"every": every_s},
            "inputs": {"cmd": command},
            "budget": {"timeout_s": timeout_s, "max_retries": 0, "concurrency": 1},
            "resources": ["sandbox"],
            "resolution_policy": {"may_retry": True, "may_accept": True}}


def install_plan(ledger, every_s: float = MIN_EVERY_S, timeout_s: float = 600.0) -> str:
    """Add the plan disabled. Enabling it is a separate, deliberate command."""
    if ledger.plan_by_name(PLAN_NAME) is not None:
        raise ValueError(f"plan {PLAN_NAME} already exists")
    plan_id = ledger.add_plan(**plan_definition(every_s, timeout_s))
    ledger.set_plan_enabled(plan_id, False)
    return plan_id


def sandbox_env(environ: dict) -> dict:
    return {key: value for key, value in environ.items() if key.startswith(ENV_PREFIX)}


def load_clients(environ: dict) -> dict:
    """`NEXUS_SANDBOX_CLIENTS=module:function`; the function sees sandbox variables only."""
    env = sandbox_env(environ)
    module_name, _, function = env.get(CLIENTS_ENV, "").partition(":")
    if not module_name or not function:
        raise ValueError(f"{CLIENTS_ENV} must name module:function")
    clients = getattr(importlib.import_module(module_name), function)(env)
    if not clients:
        raise ValueError("client factory returned no sandbox clients")
    missing = sorted(REQUIRED_CLIENTS - set(clients))
    if missing:
        raise ValueError(f"client factory missing: {', '.join(missing)}")
    return clients


@contextmanager
def isolated_environment(environ: dict):
    """Make live process credentials unavailable while adapters import and run."""
    before = dict(os.environ)
    os.environ.clear()
    os.environ.update(sandbox_env(environ))
    try:
        yield dict(os.environ)
    finally:
        os.environ.clear()
        os.environ.update(before)


def run(clients: dict, run_id: str, timeout_s: float, evidence_dir: Path) -> dict:
    """Every configured probe once, under one run id. Evidence for all, pass only if all pass."""
    report = {"run_id": run_id, "ok": True, "probes": {}}
    for name, probe in _probes(clients, run_id, timeout_s, evidence_dir).items():
        try:
            report["probes"][name] = probe()
        except (checkout_probe.ProbeFailure, care_probe.ProbeFailure) as failure:
            report["probes"][name] = _rows(failure.evidence)
            report["ok"] = False
        except journeys.JourneyFailure as failure:
            report["probes"][name] = _rows((failure.evidence,))
            report["ok"] = False
    return report


def _probes(clients: dict, run_id: str, timeout_s: float, evidence_dir: Path) -> dict:
    probes = {}
    if "checkout" in clients:
        probes["checkout"] = lambda: _rows(checkout_probe.run_checkout_probe(
            clients["checkout"], timeout_s=timeout_s))
    if "care" in clients:
        probes["care"] = lambda: _rows(care_probe.run_care_probe(
            clients["care"], clients.get("care_threshold", 5), timeout_s=timeout_s))
    if "journey" in clients:
        probes["journeys"] = lambda: list(journeys.run_journeys(
            clients["journey"], clients["browser_factory"], evidence_dir / run_id,
            timeout_s=timeout_s))
    return probes


def _rows(evidence) -> list:
    return [dataclasses.asdict(row) for row in evidence]


def main(environ: dict, timeout_s: float, evidence_dir: Path) -> int:
    """The flight body: exit 0 only when every probe passed; the report is the log."""
    run_id = f"sandbox-{uuid.uuid4().hex}"
    safe_env = sandbox_env(environ)
    try:
        with isolated_environment(safe_env) as isolated:
            report = run(load_clients(isolated), run_id, timeout_s, evidence_dir)
    except Exception as exc:
        report = {"run_id": run_id, "ok": False, "error": str(exc)}
    report = _redact(report, tuple(
        value for key, value in safe_env.items()
        if key != CLIENTS_ENV and len(value) >= 8
    ))
    print(json.dumps(report, sort_keys=True, default=str))
    return 0 if report["ok"] else 1


def _redact(value, secrets: tuple[str, ...]):
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[redacted]")
    return value

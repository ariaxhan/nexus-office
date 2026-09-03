"""`python3 -m nexus`: the escape hatch, and the only way in from a keyboard.

Every command here is one that a person needs at 2am with no context: what is
happening, stop, start again, kill that, try that again. Nothing here needs ssh
archaeology or a log grep to answer.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys

from . import flights as fl
from . import tower
from .ledger import Ledger, loads

PLIST_LABEL = "com.nexus.tower"
PLIST_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "launchd", f"{PLIST_LABEL}.plist")


def _ledger(args) -> Ledger:
    return Ledger(args.ledger)


def cmd_tower_once(args):
    led = _ledger(args)
    report = tower.tick(led)
    print(json.dumps(report, sort_keys=True))
    return 0


def cmd_tower_run(args):
    led = _ledger(args)
    tower.run(led, interval=args.interval, iterations=args.iterations)
    return 0


def cmd_status(args):
    print(tower.status(_ledger(args)))
    return 0


def cmd_pause(args):
    tower.pause(_ledger(args))
    print("paused: tower reaps and reconciles, launches nothing")
    return 0


def cmd_resume(args):
    tower.resume(_ledger(args))
    print("resumed")
    return 0


def cmd_kill(args):
    led = _ledger(args)
    flight = led.flight(args.flight)
    if flight is None:
        print(f"no such flight: {args.flight}", file=sys.stderr)
        return 1
    fl.kill(flight["pid"])
    if led.set_state(args.flight, "cancelled", expect=flight["state"],
                     result={"ok": False, "artifacts": [],
                             "error": {"code": "cancelled", "detail": "operator"}, "cost": {}}):
        led.release_leases(args.flight)
        print(f"cancelled {args.flight}")
        return 0
    print(f"{args.flight} moved on its own; nothing to cancel", file=sys.stderr)
    return 1


def cmd_retry(args):
    led = _ledger(args)
    flight = led.flight(args.flight)
    if flight is None:
        print(f"no such flight: {args.flight}", file=sys.stderr)
        return 1
    task_id = flight["task_id"]
    if task_id:
        task = led.task(task_id)
        if task and task["state"] in ("abandoned",):
            print(f"task {task_id} is abandoned; add a new one", file=sys.stderr)
            return 1
    new = led.create_flight(flight["plan_id"], task_id=task_id,
                            attempt=flight["attempt"] + 1, source="click",
                            unique_for_task=True)
    if new is None:
        print("that task already has a flight in the air", file=sys.stderr)
        return 1
    print(new)
    return 0


def cmd_plans_add(args):
    led = _ledger(args)
    schedule = {}
    if args.every:
        schedule["every"] = args.every
    if args.at:
        schedule["at"] = args.at
    if args.on:
        schedule["on"] = args.on
    plan = led.add_plan(
        name=args.name, kind="script", schedule=schedule,
        inputs={"cmd": args.cmd}, outputs=args.output or [],
        budget={"timeout_s": args.timeout, "max_retries": args.max_retries,
                "concurrency": args.concurrency},
        resources=args.resource or [],
        resolution_policy={"may_retry": True, "may_accept": True})
    print(plan)
    return 0


def cmd_plans_list(args):
    led = _ledger(args)
    for plan in led.plans():
        print(f"{plan['id']}  {plan['name']}  {loads(plan['schedule'], {})}"
              f"  {'quarantined' if plan['quarantined_at'] else 'ok'}")
    return 0


def cmd_flight_run(args):
    """The detached runner. Writes result.json; never talks to the ledger."""
    result = fl.run_script(args.workspace, args.cmd, timeout_s=args.timeout,
                           outputs=args.output or [])
    return 0 if result["ok"] else 1


def cmd_install(args):
    """Write the plist and load it. launchd only keeps tower alive; it decides nothing."""
    target_dir = os.path.expanduser("~/Library/LaunchAgents")
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, f"{PLIST_LABEL}.plist")
    with open(PLIST_TEMPLATE, "rb") as handle:
        plist = plistlib.load(handle)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plist["ProgramArguments"] = [sys.executable, "-m", "nexus", "tower", "run",
                                 "--interval", str(args.interval)]
    plist["WorkingDirectory"] = repo
    plist.setdefault("EnvironmentVariables", {})["PYTHONPATH"] = repo
    with open(target, "wb") as handle:
        plistlib.dump(plist, handle)
    subprocess.run(["launchctl", "unload", target], capture_output=True, check=False)
    loaded = subprocess.run(["launchctl", "load", target], capture_output=True, check=False)
    print(target)
    if loaded.returncode != 0:
        print(loaded.stderr.decode().strip(), file=sys.stderr)
        return 1
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="nexus", description="one ledger, one tower")
    parser.add_argument("--ledger", default=None, help="ledger path (default: NEXUS_LEDGER)")
    subs = parser.add_subparsers(dest="command", required=True)

    tower_p = subs.add_parser("tower", help="the controller")
    tower_subs = tower_p.add_subparsers(dest="tower_command", required=True)
    once = tower_subs.add_parser("once", help="one tick")
    once.set_defaults(func=cmd_tower_once)
    loop = tower_subs.add_parser("run", help="tick forever")
    loop.add_argument("--interval", type=float, default=5.0)
    loop.add_argument("--iterations", type=int, default=None)
    loop.set_defaults(func=cmd_tower_run)

    for name, func, help_text in (
        ("status", cmd_status, "what nexus knows, as a table"),
        ("pause", cmd_pause, "stop launching; keep reconciling"),
        ("resume", cmd_resume, "launch again"),
    ):
        sub = subs.add_parser(name, help=help_text)
        sub.set_defaults(func=func)

    kill_p = subs.add_parser("kill", help="cancel a flight and its process")
    kill_p.add_argument("flight")
    kill_p.set_defaults(func=cmd_kill)

    retry_p = subs.add_parser("retry", help="another attempt at the same task")
    retry_p.add_argument("flight")
    retry_p.set_defaults(func=cmd_retry)

    plans_p = subs.add_parser("plans", help="standing responsibilities")
    plans_subs = plans_p.add_subparsers(dest="plans_command", required=True)
    add = plans_subs.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--cmd", required=True)
    add.add_argument("--every", type=float)
    add.add_argument("--at", help="HH:MM local")
    add.add_argument("--on", help="event kind")
    add.add_argument("--timeout", type=float, default=600.0)
    add.add_argument("--max-retries", type=int, default=2, dest="max_retries")
    add.add_argument("--concurrency", type=int, default=tower.DEFAULT_CONCURRENCY)
    add.add_argument("--output", action="append", help="a file the flight must produce")
    add.add_argument("--resource", action="append", help="integration target to lease")
    add.set_defaults(func=cmd_plans_add)
    listing = plans_subs.add_parser("list")
    listing.set_defaults(func=cmd_plans_list)

    run_p = subs.add_parser("flight-run", help=argparse.SUPPRESS)
    run_p.add_argument("--workspace", required=True)
    run_p.add_argument("--cmd", required=True)
    run_p.add_argument("--timeout", type=float, default=600.0)
    run_p.add_argument("--output", action="append")
    run_p.set_defaults(func=cmd_flight_run)

    install = subs.add_parser("install", help="write and load the launchd plist")
    install.add_argument("--interval", type=float, default=5.0)
    install.set_defaults(func=cmd_install)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)

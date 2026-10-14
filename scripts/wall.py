#!/usr/bin/env python3
"""The Wall of Failure, from the shell: add a line, watch a line, retire a line.

    wall.py add "The plain statement of what happened" [--detail "..."]
                [--date YYYY-MM-DD] [--repeat] [--source "where it is recorded"]
    wall.py watch wf-041 --note "the rule that now exists, unproven"
    wall.py retire wf-041 --verification "what was checked, and how it held"
    wall.py list                      # what is still up
    wall.py init SEED.jsonl           # first run only: install the seed wall

The rules are Aria's and they are enforced, not advised:

  * a line goes up the day the failure happens — `add` defaults the date
    to today and refuses nothing but an empty line
  * a line comes down ONLY fixed AND verified — `retire` refuses to run
    without --verification, and the reader (`sources/failure_wall.py`)
    independently keeps any unverified retirement up, so a hand-edited
    file cannot take a line down either
  * a repeat is marked — `--repeat` on the new line; a repeat of a line
    that is still up is still its own line (the count is the point)

The wall file is append-only JSONL, one record per state change, the
last record per id carrying the current state. It lives at
$OFFICE_FAILURE_WALL, else $OFFICE_RUNTIME_ROOT/_meta/wall-of-failure.jsonl.
Run from the repo root; the reader module is imported from client/.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

from sources import failure_wall  # noqa: E402


def _today() -> str:
    return datetime.date.today().isoformat()


def _append(path: pathlib.Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _current(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    lines, _problems = failure_wall.load(path)
    return lines


def _next_id(lines: list[dict]) -> str:
    top = 0
    for ln in lines:
        tail = ln["id"].rsplit("-", 1)[-1]
        if tail.isdigit():
            top = max(top, int(tail))
    return f"wf-{top + 1:03d}"


def _find(lines: list[dict], rid: str) -> dict:
    for ln in lines:
        if ln["id"] == rid:
            return ln
    raise SystemExit(f"wall: no line with id {rid}")


def _path_or_die() -> pathlib.Path:
    path = failure_wall.wall_path()
    if path is None:
        raise SystemExit("wall: set OFFICE_FAILURE_WALL or OFFICE_RUNTIME_ROOT "
                         "so the wall file has a home")
    return path


def cmd_add(args) -> int:
    line = args.line.strip()
    if not line:
        raise SystemExit("wall: the line is the failure, in plain words — it cannot be empty")
    path = _path_or_die()
    lines = _current(path)
    record = {
        "id": _next_id(lines),
        "date": args.date or _today(),
        "line": line,
        "detail": args.detail or "",
        "repeat": bool(args.repeat),
        "status": "open",
        "source": args.source or "",
    }
    _append(path, record)
    print(f"{record['id']} up: {line}")
    return 0


def cmd_watch(args) -> int:
    path = _path_or_die()
    cur = _find(_current(path), args.id)
    if cur["status"] == "fixed":
        raise SystemExit(f"wall: {args.id} is already down (fixed + verified)")
    _append(path, {**cur, "status": "watched",
                   "note": args.note or cur.get("note", "")})
    print(f"{args.id} watched: {cur['line']}")
    return 0


def cmd_retire(args) -> int:
    verification = (args.verification or "").strip()
    if not verification:
        raise SystemExit(
            "wall: a line comes down only when fixed AND verified. "
            "Say what was checked: --verification \"...\"")
    path = _path_or_die()
    cur = _find(_current(path), args.id)
    if cur["status"] == "fixed":
        raise SystemExit(f"wall: {args.id} is already down (fixed + verified)")
    _append(path, {**cur, "status": "fixed",
                   "fixed_on": args.date or _today(),
                   "verification": verification})
    print(f"{args.id} down: {cur['line']}")
    return 0


def cmd_list(args) -> int:
    path = _path_or_die()
    lines, problems = failure_wall.load(path) if path.exists() else ([], [])
    up = [ln for ln in lines if ln["status"] != "fixed"]
    down = [ln for ln in lines if ln["status"] == "fixed"]
    for ln in up:
        mark = "REPEAT " if ln["repeat"] else ""
        print(f"{ln['id']}  {ln['date']}  [{ln['status']}]  {mark}{ln['line']}")
    print(f"-- {len(up)} up, {len(down)} down (fixed + verified)")
    for problem in problems:
        print(f"!! {problem}")
    return 0


def cmd_init(args) -> int:
    path = _path_or_die()
    if path.exists():
        raise SystemExit(f"wall: {path} already exists — init is for the first run only")
    seed = pathlib.Path(args.seed)
    records = [json.loads(raw) for raw in seed.read_text(encoding="utf-8").splitlines()
               if raw.strip()]
    lines, problems = failure_wall.fold(records)
    if problems:
        raise SystemExit(f"wall: seed does not fold clean: {problems}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
    up = sum(1 for ln in lines if ln["status"] != "fixed")
    print(f"wall installed at {path}: {len(lines)} lines, {up} up")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="wall.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="put a failure up, the day it happens")
    p.add_argument("line", help="the failure, in plain words")
    p.add_argument("--detail", default="", help="the slightly longer plain version")
    p.add_argument("--date", default="", help="the day it happened (default: today)")
    p.add_argument("--repeat", action="store_true", help="this kind has happened before")
    p.add_argument("--source", default="", help="where the record lives")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("watch", help="mark a line watched: a rule exists, unproven")
    p.add_argument("id")
    p.add_argument("--note", default="")
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("retire", help="take a line down: fixed AND verified, or not at all")
    p.add_argument("id")
    p.add_argument("--verification", default="",
                   help="what was checked, and how it held (required)")
    p.add_argument("--date", default="", help="the day the fix verified (default: today)")
    p.set_defaults(fn=cmd_retire)

    p = sub.add_parser("list", help="what is still up")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("init", help="install a seed wall (first run only)")
    p.add_argument("seed")
    p.set_defaults(fn=cmd_init)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

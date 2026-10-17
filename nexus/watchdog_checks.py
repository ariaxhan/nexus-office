"""The four office watchdogs' deterministic checks, as one program (#304, pd-05).

Checks are tissue: thresholds read by code from local files, commands and
loopback endpoints on the Studio. Probes are judgment: each run files ONE
adversarial probe as a dispatch record (#301) for a worker; the program never
judges it. Results land as `watchdog.checked` events plus #300 fault records,
so a stale ledger becomes a fault with no model anywhere in detection.

Check and signature names are the Muse bodies' own, so a shadow run compares
verdict for verdict (`compare`). VM-only surfaces stay with the VM body:
the calendar-mirror poster hook, SSH arbitration through the MacBook, and the
health-watch state when its mirror is not readable here.

    evaluate_<wd>(facts, now) -> [check]      pure
    observe_<wd>(io, now)     -> facts        bounded local reads
    run(ledger, wd, io, now)  -> state        one occurrence, idempotent
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

from . import dispatch, faults
from .ledger import loads

HOME = os.path.expanduser("~")
JOBS = os.path.join(HOME, "Library/Application Support/nexus-jobs")
RECEIPTS = os.path.join(JOBS, "state/receipts.jsonl")
LIVENESS = os.path.join(JOBS, "state/liveness.jsonl")
JOB_LOGS = os.path.join(JOBS, "logs")
VAULT_REGISTRY = os.path.join(HOME, "Developer/Vaults/_meta/services/jobs/registry.jsonl")
TOWER_LEDGER = os.path.join(HOME, "Library/Application Support/nexus/ledger.sqlite")
LOOPBACK = "http://127.0.0.1:8790/api/health"
PHONE = "https://office.tail4f309a.ts.net/api/health"
OLLAMA = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen2.5:3b"
PROBE_QUEUE = "watchdog-probes"
PROBE_KIND = "watchdog.probe"

REQUIRED_DAEMONS = ("com.nexus.tower", "com.aria.office-ask", "com.aria.ollama-serve", "com.nexus.stay-awake")
BOTH_CYCLES_ONLY = ("com.aria.tradition-dashboard",)  # recovered 2026-10-14; health-watch owns it
COMMS_JOBS = ("com.nexus.morning-briefing", "com.nexus.midday-pulse", "com.nexus.research-digest",
              "com.nexus.evening-reflection", "com.aria.granola-sync", "com.nexus.calendar-mirror")
CANARY = "com.nexus.canary-wedge-d"
TRACEBACK = re.compile(r"Traceback \(most recent call last\)|\b(\w+(?:Error|Exception))\b:")
STAY_AWAKE = re.compile(r"pid \d+\(caffeinate\): .*PreventUserIdleSystemSleep")

WATCHDOGS = {
    "door": {"cadence_s": 1800, "probes": (
        "real content sections: fetch 2-3 rendered section payloads through the phone path; each must parse and be fresh",
        "snapshot internals: a section flagged in error under an overall ok:true",
        "retired name https://arias-mac-studio.tail4f309a.ts.net/api/health must not serve the Office",
        "untrusted Host header against the tailnet IP must be refused",
        "one real ask turn, marked as a watchdog self-test, completes end-to-end",
        "trusted-hosts and Serve binding match OFFICE-PHONE-ACCESS.md (security: record and fix, never alert)")},
    "dispatch": {"cadence_s": 1800, "probes": (
        "proxy-success: one ok:true job's expected output exists and is as fresh as its cadence",
        "dangling runs: starts with no completion past timeout + margin",
        "registry copy agreement: local and vault enabled-job id sets",
        "credential shapes in the newest job-log tails (line number and shape only, never the value)",
        "git history vs claims for one estate repo (read-only git log)")},
    "daemons": {"cadence_s": 3600, "probes": (
        "Tower work product: the newest ledger entries show decisions within cadence",
        "one real ask micro-turn, marked as a watchdog self-test, completes end-to-end",
        "Ollama tier truth beyond the check: latency and model under a second prompt",
        "stay-awake story: caffeinate pid age agrees with the wrapper's own start",
        "unexpected tailnet listeners: one bounded connect on one rotated port")},
    "comms": {"cadence_s": 3600, "probes": (
        "briefing arrival: the newest ok briefing is findable in the inbox",
        "mirror read-back: Muse - Live calendar block count equals the engine's claim",
        "repeat failure: two newest failures with the same non-transient signature",
        "Gmail credential custody unchanged from STAGE1.md (security: record and fix, never alert)")},
}


def _iso(text):
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.timestamp() if stamp.tzinfo else None


def _check(name, signatures=(), evidence=None, verdict=None):
    sigs = sorted(set(signatures))
    return {"check": name, "verdict": verdict or ("fault" if sigs else "pass"),
            "signatures": sigs, "evidence": evidence or {}}


def _skip(name, why):
    return _check(name, evidence={"why": why}, verdict="skip")


def _age(now, ts):
    return None if ts is None else round(now - ts)


# ---- pure evaluators -------------------------------------------------------


def _latest(receipts, job):
    rows = [r for r in receipts if r.get("job") == job and _iso(r.get("started")) is not None]
    return sorted(rows, key=lambda r: _iso(r["started"]))


def evaluate_dispatch(f, now):
    receipts = f.get("receipts") or []
    out = []
    stamps = [_iso(r.get("started")) for r in receipts]
    stamps = [s for s in stamps if s is not None]
    newest = max(stamps, default=None)
    hour = [r for r in receipts if (_iso(r.get("started")) or 0) >= now - 3600]
    rc78 = sum(1 for r in hour if r.get("rc") == 78)
    rc124 = sum(1 for r in hour if r.get("rc") == 124)
    sigs = []
    if newest is None or now - newest > 20 * 60:
        sigs.append("receipts-stale")
    if rc78 >= 5:
        sigs.append("rc78-cluster")
    if rc124 >= 5:
        sigs.append("timeouts-cluster")
    out.append(_check("dispatch_breathing", sigs, {"newest_age_s": _age(now, newest),
                                                   "rc78_last_hour": rc78, "rc124_last_hour": rc124}))

    canary = _latest(f.get("receipts_wide") or receipts, CANARY)
    if not canary:
        out.append(_check("wedge_canary", ["canary-wedge-d-fault"], {"newest": None}))
    else:
        starts = [_iso(r["started"]) for r in canary]
        cadence = starts[-1] - starts[-2] if len(starts) > 1 else 21600
        age = now - starts[-1]
        bad = canary[-1].get("rc") != 0 or age > 2 * cadence + 1800
        out.append(_check("wedge_canary", ["canary-wedge-d-fault"] if bad else [],
                          {"rc": canary[-1].get("rc"), "age_s": round(age), "observed_cadence_s": round(cadence)}))

    reg = f.get("vault_registry")
    if reg is None:
        out.append(_skip("vault_registry_readability", "not read"))
    else:
        ok = reg.get("rc") == 0 and reg.get("bytes", 0) > 0
        out.append(_check("vault_registry_readability", [] if ok else ["vault-registry-unreadable"], reg))

    health = f.get("health_state")
    if health is None:
        out.append(_skip("health_watch_meta", "health-watch state is VM-local; pass it with --health-state"))
    else:
        last = _iso(health.get("last_check"))
        stale = last is None or now - last > 75 * 60
        out.append(_check("health_watch_meta", ["health-watch-stale"] if stale else [],
                          {"last_check_age_s": _age(now, last)}))
    return out


def _cycles(rows):
    by = {}
    for row in rows:
        stamp = str(row.get("at", ""))[:16]  # one cycle = one minute of liveness rows
        if stamp:
            by.setdefault(stamp, []).append(row)
    return [by[k] for k in sorted(by)[-2:]]


def evaluate_daemons(f, now):
    out = []
    cycles = _cycles(f.get("liveness") or [])
    sigs, bad = [], {}
    newest = _iso(cycles[-1][0].get("at")) if cycles else None
    if len(cycles) < 2 or newest is None or now - newest > 45 * 60:
        sigs.append("liveness-stale")
    for job in REQUIRED_DAEMONS + BOTH_CYCLES_ONLY:
        wedged = [any(r.get("job") == job and (r.get("rc") != 0 or r.get("state") != "alive") for r in c)
                  for c in cycles]
        if (job in BOTH_CYCLES_ONLY and wedged and all(wedged)) or (job not in BOTH_CYCLES_ONLY and any(wedged)):
            sigs.append(f"daemon-wedged:{job}")
            bad[job] = wedged
    out.append(_check("liveness_depth", sigs, {"cycles": [c[0].get("at") for c in cycles], "wedged": bad}))

    tower_alive = bool(cycles) and any(r.get("job") == "com.nexus.tower" and r.get("state") == "alive"
                                       and r.get("rc") == 0 for r in cycles[-1])
    mtime = f.get("ledger_mtime")
    stale = tower_alive and (mtime is None or now - mtime > 15 * 60)
    out.append(_check("tower_ledger_freshness", ["tower-ledger-stale"] if stale else [],
                      {"tower_alive": tower_alive, "ledger_age_s": _age(now, mtime)}))

    ask = f.get("ask_check")
    if ask is None:
        out.append(_skip("office_ask_self_check", "no office-ask command in liveness"))
    else:
        out.append(_check("office_ask_self_check", [] if ask.get("rc") == 0 else ["office-ask-check-fail"], ask))

    ollama = f.get("ollama") or {}
    sigs = []
    if not ollama.get("ps_ok"):
        sigs.append("ollama-down")
    elif not ollama.get("completed"):
        sigs.append("ollama-inference-fail")
    elif ollama.get("model") != OLLAMA_MODEL:
        sigs.append("ollama-wrong-model")
    out.append(_check("ollama_real_completion", sigs, ollama))

    held = bool(STAY_AWAKE.search(f.get("assertions") or ""))
    out.append(_check("stay_awake", [] if held else ["stay-awake-lapsed"], {"assertion_held": held}))
    return out


def classify_failure(receipt, log_tail=""):
    """vault-fault / code-bug:<Error> / transient: by what the failure shows, not by guess."""
    text = f"{receipt.get('stderr_tail') or ''}\n{log_tail or ''}"
    if receipt.get("rc") == 78 or re.search(r"preflight|volume", text, re.I):
        return "vault-fault"
    match = TRACEBACK.search(text)
    if match:
        names = re.findall(r"\b(\w+(?:Error|Exception))\b:", text)
        return f"code-bug:{names[-1] if names else 'Traceback'}"
    return "transient"


def evaluate_comms(f, now):
    receipts = f.get("receipts") or []
    out = []
    mirror = _latest(receipts, "com.nexus.calendar-mirror")
    newest = mirror[-1] if mirror else None
    fresh = bool(newest) and newest.get("ok") is True and now - _iso(newest["started"]) <= 15 * 60
    out.append(_check("calendar_mirror_lag", [] if fresh else ["mirror-engine-stale"],
                      {"newest_age_s": _age(now, _iso(newest["started"])) if newest else None,
                       "newest_ok": newest.get("ok") if newest else None,
                       "poster_bridge": "VM-side hook; the VM body keeps it"}))

    granola = _latest(receipts, "com.aria.granola-sync")
    oks = [_iso(r["started"]) for r in granola if r.get("ok") is True]
    two_failed = len(granola) >= 2 and not granola[-1].get("ok") and not granola[-2].get("ok")
    stale = not oks or now - oks[-1] > 26 * 3600 or two_failed
    out.append(_check("granola_sync_freshness", ["granola-sync-stale"] if stale else [],
                      {"newest_ok_age_s": _age(now, oks[-1]) if oks else None, "two_newest_failed": two_failed}))

    sigs, classes = [], {}
    for job in COMMS_JOBS:
        rows = _latest(receipts, job)
        if not rows or rows[-1].get("ok") is not False:
            continue
        kind = classify_failure(rows[-1], (f.get("log_tails") or {}).get(job, ""))
        classes[job] = kind
        if kind.startswith("code-bug:"):
            sigs.append(f"{kind}:{job}")
    out.append(_check("code_bug_sweep", sigs, {"failing": classes}))
    return out


def evaluate_door(f, now):
    out = [_check("ssh_reachability", evidence={"why": "the program ran on the Studio; the VM body owns transport"})]
    loop = f.get("loopback") or {}
    snap = _iso(loop.get("snapshot_at"))
    ok = loop.get("ok") is True and snap is not None and now - snap <= 20 * 60
    out.append(_check("door_loopback", [] if ok else ["door_loopback_failed"],
                      {**loop, "snapshot_age_s": _age(now, snap)}))
    phone = f.get("phone") or {}
    sigs = [] if phone.get("status") == 200 else ["phone_path_failed"]
    if not sigs and ok and phone.get("revision") and phone.get("revision") != loop.get("revision"):
        sigs = ["revision_mismatch"]
    out.append(_check("phone_path", sigs, phone))
    return out


EVALUATE = {"door": evaluate_door, "dispatch": evaluate_dispatch,
            "daemons": evaluate_daemons, "comms": evaluate_comms}


# ---- observation (bounded local reads) --------------------------------------


class LocalIO:
    """The only side of the program that touches the machine. Tests replace it."""

    def tail(self, path, nbytes):
        try:
            with open(path, "rb") as fh:
                fh.seek(max(0, os.path.getsize(path) - nbytes))
                return fh.read(nbytes).decode("utf-8", "replace")
        except OSError:
            return None

    def mtime(self, *paths):
        stamps = [os.path.getmtime(p) for p in paths if os.path.exists(p)]
        return max(stamps, default=None)

    def sh(self, argv, timeout, shell=False):
        try:
            done = subprocess.run(argv, shell=shell, capture_output=True, text=True, timeout=timeout)
            return done.returncode, (done.stdout or "") + (done.stderr or "")
        except subprocess.TimeoutExpired:
            return 124, "timeout"
        except OSError as exc:
            return 127, type(exc).__name__

    def http(self, url, body=None, timeout=10):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status, raw = resp.status, resp.read(65536)
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = None
            return status, payload, round(time.monotonic() - started, 3)
        except Exception as exc:  # any failure to answer is the observation
            return None, {"error": f"{type(exc).__name__}: {str(exc)[:80]}"}, round(time.monotonic() - started, 3)


def _jsonl(text):
    rows = []
    for line in (text or "").splitlines():  # a tail's torn first line fails to parse and drops out
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def observe_dispatch(io, now):
    rc, said = io.sh(["/usr/bin/timeout", "20", "head", "-c", "4096", VAULT_REGISTRY], 25) \
        if os.path.exists("/usr/bin/timeout") else io.sh(["head", "-c", "4096", VAULT_REGISTRY], 20)
    return {"receipts": _jsonl(io.tail(RECEIPTS, 200_000)), "receipts_wide": _jsonl(io.tail(RECEIPTS, 2_000_000)),
            "vault_registry": {"rc": rc, "bytes": len(said) if rc == 0 else 0, "path": VAULT_REGISTRY},
            "health_state": None}


def observe_daemons(io, now):
    liveness = _jsonl(io.tail(LIVENESS, 65536))
    ask_cmd = next((r.get("cmd") for r in reversed(liveness) if r.get("job") == "com.aria.office-ask"), None)
    ask = None
    if ask_cmd:
        if "--check" not in ask_cmd:
            ask_cmd += " --check"
        rc, said = io.sh(ask_cmd, 60, shell=True)
        ask = {"rc": rc, "tail": said[-200:]}
    status, _, _ = io.http(f"{OLLAMA}/api/ps", timeout=20)
    ollama = {"ps_ok": status == 200}
    if ollama["ps_ok"]:
        status, payload, latency = io.http(f"{OLLAMA}/api/generate", {
            "model": OLLAMA_MODEL, "prompt": "ping", "stream": False, "options": {"num_predict": 4}}, timeout=150)
        ollama.update(completed=status == 200 and isinstance(payload, dict) and "response" in payload,
                      model=(payload or {}).get("model"), latency_s=latency)
    _, assertions = io.sh(["pmset", "-g", "assertions"], 20)
    return {"liveness": liveness, "ledger_mtime": io.mtime(TOWER_LEDGER, TOWER_LEDGER + "-wal"),
            "ask_check": ask, "ollama": ollama, "assertions": assertions}


def observe_comms(io, now):
    receipts = _jsonl(io.tail(RECEIPTS, 2_000_000))
    tails = {}
    for job in COMMS_JOBS:
        rows = _latest(receipts, job)
        if rows and rows[-1].get("ok") is False:
            tails[job] = io.tail(os.path.join(JOB_LOGS, f"{job}.log"), 8192) or ""
    return {"receipts": receipts, "log_tails": tails}


def _health(io, url):
    status, payload, latency = io.http(url, timeout=20)
    payload = payload if isinstance(payload, dict) else {}
    return {"status": status, "ok": payload.get("ok"), "snapshot_at": payload.get("snapshot_at"),
            "revision": payload.get("revision"), "error": payload.get("error"), "latency_s": latency}


def observe_door(io, now):
    return {"loopback": _health(io, LOOPBACK), "phone": _health(io, PHONE)}


OBSERVE = {"door": observe_door, "dispatch": observe_dispatch,
           "daemons": observe_daemons, "comms": observe_comms}


# ---- records ---------------------------------------------------------------


def source(watchdog):
    return f"watchdog:office-{watchdog}"


def occurrence(watchdog, now):
    cadence = WATCHDOGS[watchdog]["cadence_s"]
    start = datetime.fromtimestamp(now - now % cadence, timezone.utc)
    return f"office-{watchdog}/{start.strftime('%Y-%m-%dT%H:%MZ')}"


def _history(ledger, watchdog):
    return [loads(e["payload"], {}) or {} for e in ledger.events(kind="watchdog.checked")
            if (loads(e["payload"], {}) or {}).get("source") == source(watchdog)]


def run(ledger, watchdog, io=None, now=None, probes=True, given=None):
    """One occurrence: observe, evaluate, record faults, file one probe. Idempotent per occurrence.

    `given` supplies facts only the caller can see (the VM's health-watch state)."""
    if watchdog not in WATCHDOGS:
        raise ValueError(f"watchdog must be one of {sorted(WATCHDOGS)}")
    now = time.time() if now is None else now
    occ = occurrence(watchdog, now)
    prior = ledger.events(kind="watchdog.checked", subject=occ)
    if prior:
        return {**(loads(prior[0]["payload"], {}) or {}), "duplicate": True}
    facts = {**OBSERVE[watchdog](io or LocalIO(), now), **(given or {})}
    checks = EVALUATE[watchdog](facts, now)
    return record(ledger, watchdog, checks, now, probes=probes)


def record(ledger, watchdog, checks, now, probes=True):
    """Write one occurrence's checks as structured state + fault records. No prose anywhere."""
    src, occ = source(watchdog), occurrence(watchdog, now)
    dispatch.ensure(ledger)
    present = {s for c in checks for s in c["signatures"]}
    history = _history(ledger, watchdog)
    state = {"watchdog": watchdog, "occurrence_id": occ, "source": src, "checked_at": now,
             "checks": checks, "faults": {}, "probe": None}
    with ledger.tx() as c:
        if ledger.events(kind="watchdog.checked", subject=occ):
            return {**(loads(ledger.events(kind="watchdog.checked", subject=occ)[0]["payload"], {}) or {}),
                    "duplicate": True}
        for sig in sorted(present):
            streak = 1
            for past in reversed(history):
                if sig not in {s for chk in past.get("checks", []) for s in chk["signatures"]}:
                    break
                streak += 1
            state["faults"][sig] = {"fault_id": faults._detect_in_tx(ledger, sig, src, None, now),
                                    "consecutive_checks": streak}
        for rec in faults.records(ledger, state="detected"):
            if rec["source"] == src and rec["signature"] not in present:
                ledger._event("watchdog.recovered", rec["fault_id"], {"surface": occ}, src, now)
        if probes:
            state["probe"] = _file_probe(ledger, c, watchdog, len(history), occ, now)
        ledger._event("watchdog.checked", occ, state, src, now)
    state["escalated"] = [r for r in faults.escalated(ledger) if r["source"] == src]
    return state


def _file_probe(ledger, c, watchdog, run_count, occ, now):
    """Exactly one adversarial probe per run, by rotation; a judgment task, not a check."""
    if any(r["payload"].get("watchdog") == watchdog
           for r in dispatch.records(ledger, states=("filed", "claimed", "receipt"), queue=PROBE_QUEUE)):
        return {"skipped": "previous probe still open"}
    menu = WATCHDOGS[watchdog]["probes"]
    index = run_count % len(menu)
    payload = {"watchdog": watchdog, "occurrence_id": occ, "probe": index, "act": menu[index],
               "surface": "a finding recorded with evidence, or 'held'; confirmed defects go to FINDINGS -> FIX",
               # A probe's product is judgment; its receipt names the finding. Nothing for code to re-read.
               "verify": ["/usr/bin/true"]}
    return {"record_id": dispatch.file_in(ledger, c, PROBE_QUEUE, PROBE_KIND, payload, now), "index": index}


# ---- shadow comparison ------------------------------------------------------

_FAULTY = re.compile(r"\b(fault|fail|stale|lapsed|wedged|down|unreadable)\b", re.I)


def muse_verdict(entry):
    """A Muse state check, whatever its prose shape, reduced to pass/fault."""
    if not isinstance(entry, dict):
        return "fault" if _FAULTY.search(str(entry)) else "pass"
    sigs = entry.get("signatures_triggered") or entry.get("signatures_present")
    if sigs:
        return "fault"
    if isinstance(entry.get("ok"), bool):
        return "pass" if entry["ok"] else "fault"
    word = str(entry.get("status") or entry.get("result") or "")
    if sigs is not None and not sigs and not word.lower().startswith(("fault", "fail", "stale")):
        return "pass"
    return "fault" if _FAULTY.search(word) and not re.search(r"no (code bugs|signature)", word, re.I) else "pass"


def compare(state, muse):
    """Per check: program verdict vs Muse's, for one same-window pair. `match` is every comparable check."""
    rows = []
    for chk in state["checks"]:
        theirs = (muse.get("checks") or {}).get(chk["check"])
        if chk["verdict"] == "skip" or theirs is None:
            rows.append({"check": chk["check"], "program": chk["verdict"],
                         "muse": None if theirs is None else muse_verdict(theirs), "match": None})
            continue
        verdict = muse_verdict(theirs)
        rows.append({"check": chk["check"], "program": chk["verdict"], "muse": verdict,
                     "match": verdict == chk["verdict"]})
    compared = [r for r in rows if r["match"] is not None]
    return {"watchdog": state["watchdog"], "occurrence_id": state["occurrence_id"],
            "muse_last_run": muse.get("last_run"), "rows": rows,
            "match": bool(compared) and all(r["match"] for r in compared),
            "uncompared": [r["check"] for r in rows if r["match"] is None]}

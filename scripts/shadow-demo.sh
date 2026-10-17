#!/bin/sh
# #310 acceptance: the real producers on a sandbox ledger, dual-written to the live nexus-shadow,
# judged there, compared, read back. The Studio ledger is never touched.
set -eu
cd "$(dirname "$0")/.."
SANDBOX=$(mktemp -d)
trap 'rm -rf "$SANDBOX"' EXIT
NEXUS_LEDGER="$SANDBOX/ledger.sqlite" PYTHONPATH="$PWD" python3 - <<'PY'
import json, time, urllib.request
from nexus import dispatch, faults, shadow
from nexus.ledger import Ledger

class Proc:
    returncode, stdout, stderr = 0, "", ""

led = Ledger()
shadow.push(led)                     # cursor starts at now: only records born below are copied
rid = dispatch.file_control_request(led, "broker-studio", "shadow-demo", "click Allow", "no shell route",
                                    "grant reads allowed", ["/usr/bin/true"])
workers = {"broker-studio": {"argv": ["/usr/bin/true"], "bound_s": 60}}
dispatch.poll(led, workers=workers, launch=lambda *a: 4242)          # simulated launch: nothing fires
dispatch.submit_receipt(led, rid, {"outcome": "done", "artifacts": ["file:/usr/bin/true"],
                        "verification": "/usr/bin/true", "evidence": ["rc 0"], "next": "none"})
dispatch.poll(led, workers=workers, run=lambda *a, **k: Proc(), stop=lambda r: True)
fid = faults.report(led, f"shadow-demo {time.time()}", "shadow-demo", "detected")["fault_id"]
faults.report(led, "x", "shadow-demo", "repairing", fault_id=fid, action="restart")
faults.report(led, "x", "shadow-demo", "verified_fixed", fault_id=fid, surface="GET /health")
print("push", json.dumps(shadow.push(led)))
local = shadow.canonical(dispatch.get(led, rid))
token = shadow._token()
req = urllib.request.Request(shadow.URL + "/compare", method="POST", headers={"authorization": f"Bearer {token}", "user-agent": shadow.UA})
time.sleep(5)
print("compare", urllib.request.urlopen(req, timeout=20).read().decode())
out = shadow.readout(record_id=rid)
print("d1 byte-identical:", out["record"]["record_json"] == local, out["record"]["event_id"])
for p in out["pairs"]:
    print(f"  seq={p['seq']} {p['from_state']}>{p['to_state']} studio={p['studio_outcome']}"
          f" shadow={p['shadow_outcome']} verdicts={p['studio_verdict']}/{p['shadow_verdict']}"
          f" {'MATCH' if p['match'] else 'MISMATCH'}")
for s in out["simulated"]:
    print(f"  simulated {s['decision']} queue={s['queue']} fired={s['fired']}")
fo = shadow.readout(record_id=fid)
for p in fo["pairs"]:
    print(f"  fault {fid} {p['from_state']}>{p['to_state']} {'MATCH' if p['match'] else 'MISMATCH'}")
print("RECORD", rid, out["record"]["revision"])
PY

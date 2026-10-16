#!/bin/sh
# #308 acceptance: one new transcript through the real Granola processor harness into dispatch
# records, then the real poller advancing them from their surfaces. Sandbox vault + ledger; the
# Codex worker is replaced by a fixed receipt (the extraction is the LLM's; the chain after it is
# what this proves). GitHub is read only: ariaxhan/nexus-office#349 is a closed issue.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SANDBOX=$(mktemp -d)
export NEXUS_LEDGER="$SANDBOX/ledger.sqlite" VAULT_ROOT="$SANDBOX/vault" SANDBOX ROOT
mkdir -p "$VAULT_ROOT/_meta/granola/raw" "$VAULT_ROOT/_meta/services" "$VAULT_ROOT/CodingVault"
ln -s "$ROOT" "$VAULT_ROOT/CodingVault/nexus-office"
SERVICES="$HOME/Developer/Vaults/_meta/services"
python3 - "$SERVICES" <<'PY'
import importlib.util, json, os, sys
sys.path.insert(0, sys.argv[1])
spec = importlib.util.spec_from_file_location("gp", os.path.join(sys.argv[1], "granola_processor.py"))
gp = importlib.util.module_from_spec(spec); spec.loader.exec_module(gp)
vault, mid = os.environ["VAULT_ROOT"], "demo3080-0000-0000-0000-000000000000"
note = os.path.join(vault, "_meta", "research", "demo-note.md")
open(os.path.join(vault, "_meta/granola/raw", f"{mid}.transcript.txt"), "w").write(
    "Aria: I'll write the note today.\nJess: I will close the phone proof issue.\n")
def worker(brief, events_path):
    os.makedirs(os.path.dirname(note)); open(note, "w").write("done\n")  # the worker executes Aria's item
    receipt = {"meeting_id": mid, "status": "processed", "directives": [],
               "issues": [{"repo": "ariaxhan/nexus-office", "number": 349, "url": "u", "created": False,
                           "kind": "work", "owner": "jess", "title": "close the phone proof issue"}],
               "build_orders": [], "aria_items": [{"summary": "write the note", "disposition": "executed",
                                                   "evidence": note}],
               "needs_aria": [], "plan_changes": [], "notes": ""}
    path = brief.split("write this exact JSON schema to ")[1].split(" :")[0]
    json.dump(receipt, open(path, "w")); events_path.write_text(""); return 0
gp.run_worker = worker
state = gp.load_state()
out = gp.process_one(state, {}, {}, {"id": mid, "meta": {"title": "Demo", "date": "2026-10-16", "path": "x.md"},
                                      "kind": "new"}, "demo")
print("processor:", out["outcome"], "records:", state["processed"][mid]["records"])
print("processor receipt:", open(gp.RECEIPTS_PATH).read().strip())
PY
cd "$ROOT"
python3 -m nexus dispatch poll >/dev/null   # filed -> claimed (owner); surfaces read
python3 -m nexus dispatch list | python3 -c '
import json, sys, sqlite3, os
db = sqlite3.connect(os.environ["NEXUS_LEDGER"])
for r in json.load(sys.stdin):
    walk = [json.loads(p)["to"] for (p,) in db.execute(
        "SELECT payload FROM events WHERE kind=? AND subject=? ORDER BY id", ("dispatch.state", r["record_id"]))]
    print(r["record_id"], r["payload"]["owner"], r["state"], r["payload"]["surface"], "walk:", " ".join(walk))'
grep -rn "coordinator-inbox\|inbox_id" "$VAULT_ROOT" 2>/dev/null && { echo "prose handoff found"; exit 1; } || echo "no inbox/prose handoff in the chain"

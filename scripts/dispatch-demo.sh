#!/bin/bash
# #301 acceptance, on a sandbox ledger: real poller, real detached workers, no chat, no reader.
#   1. a broker record is dispatched to its worker and closed on the surface read
#   2. a starved record re-dispatches on age alone, then escalates as data
#   3. the store itself refuses a state advance with no receipt
set -u
cd "$(dirname "$0")/.."
D=$(mktemp -d /tmp/nexus-dispatch-demo.XXXX); L=$D/ledger.sqlite
nx() { python3 -m nexus --ledger "$L" "$@"; }
cat > "$D/holder.sh" <<W
#!/bin/sh
touch "$D/act-done"
cd "$PWD" && python3 -m nexus --ledger "$L" dispatch receipt "\$NEXUS_DISPATCH_RECORD_ID" --outcome done \
  --artifact "file:$D/act-done" --verification "test -f $D/act-done" --evidence "act-done exists"
W
chmod +x "$D/holder.sh"
printf '{"broker-studio": {"argv": ["%s"], "bound_s": 1}, "broker-macbook": {"argv": ["/usr/bin/true"], "bound_s": 1}}' \
  "$D/holder.sh" > "$D/workers.json"
export NEXUS_DISPATCH_WORKERS=$D/workers.json
A=$(nx dispatch file --queue broker-studio --requester demo --act "touch act-done" --shell-check demo \
  --surface "act-done exists" --verify "[\"test\",\"-f\",\"$D/act-done\"]")
B=$(nx dispatch file --queue broker-macbook --requester demo --act "starved act" --shell-check demo \
  --surface none --verify '["false"]')
for _ in 1 2 3 4 5; do nx dispatch poll; sleep 2; done
echo "== store"
nx dispatch list | python3 -c '
import json, sys
for r in json.load(sys.stdin):
    print(r["record_id"], r["queue"], r["state"], "attempts=%d" % r["attempts"], r["dispatched_to"],
          (r["verification"] or {}).get("result"), r["escalation"])'
echo "== walk of $A"
sqlite3 "$L" "SELECT json_extract(payload,'\$.to') FROM events WHERE kind='dispatch.state' AND subject='$A' ORDER BY id" | paste -sd' ' -
echo "== manual advance with no receipt"
C=$(nx dispatch file --queue broker-macbook --requester demo --act x --shell-check d --surface s --verify '["true"]')
sqlite3 "$L" "UPDATE dispatch_records SET state='claimed', attempts=1 WHERE record_id='$C'"
sqlite3 "$L" "UPDATE dispatch_records SET state='receipt' WHERE record_id='$C'"
sqlite3 "$L" "UPDATE dispatch_records SET state='verified' WHERE record_id='$C'"
sqlite3 "$L" "SELECT record_id, state FROM dispatch_records WHERE record_id='$C'"

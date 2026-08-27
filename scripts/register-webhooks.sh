#!/usr/bin/env bash
# Point every desk's repo at this office's front door, once, and again safely.
#
#   scripts/register-webhooks.sh --dry-run https://<host>:8443/webhook
#   scripts/register-webhooks.sh           https://<host>:8443/webhook
#   scripts/register-webhooks.sh --repo owner/name https://<host>:8443/webhook
#
# The repo list is not a list anybody maintains. It is read from the office door
# at 127.0.0.1:8790, which is the only thing that knows which desks are real: a
# station that is `hidden` is one nobody wants events from, and a station without
# `access` is one no account here can push to, so a hook on it would deliver
# events the pipeline is not allowed to act on. Same set the office polls, by
# construction, which is the point: no hand-maintained list.
#
# GitHub has no user-level webhooks, only repository and organization ones, so
# this is one hook per repo and there is no shortcut.
#
# The shared secret lives in the keychain (service `nexus-office`, account
# `webhook-secret`) and never in a file, a log, or a command line. The JSON body
# that carries it reaches `gh` on stdin, so it is not in `ps` either. GitHub
# needs it in the create body: that is the only way it can sign a delivery, and
# it never gives it back on a read.
set -euo pipefail

SERVICE="nexus-office"
ACCOUNT="webhook-secret"
DOOR="${OFFICE_DOOR_URL:-http://127.0.0.1:8790}"
EVENTS='["issues","issue_comment","pull_request"]'

DRY=0
ONLY=""
HOOK_URL=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --repo) ONLY="${2:-}"; [ -n "$ONLY" ] || { echo "--repo needs owner/name" >&2; exit 2; }; shift 2 ;;
    -h|--help) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *) HOOK_URL="$1"; shift ;;
  esac
done

[ -n "$HOOK_URL" ] || { echo "usage: $0 [--dry-run] [--repo owner/name] <hook-url>" >&2; exit 2; }
case "$HOOK_URL" in
  https://*) ;;
  *) echo "the hook url must be https: GitHub will not sign a delivery over http" >&2; exit 2 ;;
esac
command -v gh >/dev/null || { echo "gh is not installed" >&2; exit 2; }

# ── the secret ───────────────────────────────────────────────────────────────
# Read once into a shell variable and never printed. On a real run a missing item
# is created here, because a hook created without one is an unauthenticated
# endpoint on the public internet. A dry run creates nothing and says so.

SECRET="$(security find-generic-password -s "$SERVICE" -a "$ACCOUNT" -w 2>/dev/null || true)"
if [ -z "$SECRET" ]; then
  if [ "$DRY" = "1" ]; then
    echo "secret: absent. A real run would create keychain item $SERVICE/$ACCOUNT."
  else
    SECRET="$(openssl rand -hex 32)"
    security add-generic-password -U -s "$SERVICE" -a "$ACCOUNT" -w "$SECRET" >/dev/null
    echo "secret: created keychain item $SERVICE/$ACCOUNT (32 bytes, value not printed)."
  fi
else
  echo "secret: keychain item $SERVICE/$ACCOUNT is present."
fi

[ "$DRY" = "1" ] && echo "dry run: reads only, nothing is created or changed."

# ── the desks ────────────────────────────────────────────────────────────────

WORLD="$(curl -s --max-time 15 "$DOOR/api/world" || true)"
[ -n "$WORLD" ] || { echo "the office door at $DOOR did not answer" >&2; exit 1; }

DESKS="$(ONLY="$ONLY" python3 -c '
import json, os, sys
try:
    doc = json.loads(sys.stdin.read())
except ValueError:
    sys.exit("the door did not answer with JSON")
world = doc.get("world", doc)
only = os.environ.get("ONLY", "").strip()
rows = []
for s in world.get("stations", []) or []:
    repo = str(s.get("repo") or "").strip()
    if not repo or s.get("hidden") or not s.get("access"):
        continue
    if only and repo != only:
        continue
    rows.append(repo + "\t" + str(s.get("identity") or "").strip())
if not rows:
    sys.exit("no desk matched")
print("\n".join(sorted(rows)))
' <<<"$WORLD")"

TOTAL="$(printf '%s\n' "$DESKS" | wc -l | tr -d ' ')"
echo "desks: $TOTAL from $DOOR"
echo

created=0; updated=0; unchanged=0; skipped=0

say() { printf '%-46s %s\n' "$1" "$2"; }

while IFS=$'\t' read -r NWO LOGIN; do
  [ -n "$NWO" ] || continue

  if [ -z "$LOGIN" ]; then
    say "$NWO" "skipped: the door names no account for this desk"
    skipped=$((skipped + 1)); continue
  fi

  # The same lookup client/office-sync.py does: the account the door says holds
  # push here is the account whose token may create the hook.
  TOKEN="$(gh auth token --user "$LOGIN" 2>/dev/null || true)"
  if [ -z "$TOKEN" ]; then
    say "$NWO" "skipped: gh holds no token for $LOGIN"
    skipped=$((skipped + 1)); continue
  fi

  # What is already there. A hook is ours when its config.url is this exact URL.
  # The exit code is the check, not the output: listing hooks without admin
  # answers 403 with a perfectly valid JSON body, and reading that body as a
  # hook list is how a repo silently gets a second hook on the next run.
  if ! EXISTING="$(GH_TOKEN="$TOKEN" gh api "repos/$NWO/hooks" --paginate 2>/dev/null)"; then
    say "$NWO" "skipped: could not list hooks ($LOGIN needs admin here)"
    skipped=$((skipped + 1)); continue
  fi

  # "<id> same" or "<id> differs", empty when nothing points here. GitHub never
  # returns the secret, so only the events and the active flag can be compared.
  MATCH="$(HOOK_URL="$HOOK_URL" EVENTS="$EVENTS" python3 -c '
import json, os, sys
want = set(json.loads(os.environ["EVENTS"]))
url = os.environ["HOOK_URL"]
try:
    hooks = json.loads(sys.stdin.read() or "[]")
except ValueError:
    hooks = []
if not isinstance(hooks, list):
    hooks = []
for h in hooks:
    if not isinstance(h, dict):
        continue
    if (h.get("config") or {}).get("url") != url:
        continue
    same = set(h.get("events") or []) == want and bool(h.get("active"))
    print(h.get("id"), "same" if same else "differs")
    break
' <<<"$EXISTING")"

  HOOK_ID="${MATCH%% *}"
  STATE="${MATCH##* }"

  if [ -n "$HOOK_ID" ] && [ "$STATE" = "same" ]; then
    say "$NWO" "unchanged"
    unchanged=$((unchanged + 1)); continue
  fi

  if [ -n "$HOOK_ID" ]; then
    if [ "$DRY" = "1" ]; then
      say "$NWO" "would update (hook $HOOK_ID has the wrong events, or is inactive)"
    else
      EVENTS="$EVENTS" python3 -c '
import json, os
print(json.dumps({"active": True, "events": json.loads(os.environ["EVENTS"])}))
' | GH_TOKEN="$TOKEN" gh api -X PATCH "repos/$NWO/hooks/$HOOK_ID" --input - >/dev/null
      say "$NWO" "updated"
      sleep 1
    fi
    updated=$((updated + 1)); continue
  fi

  if [ "$DRY" = "1" ]; then
    say "$NWO" "would create"
    created=$((created + 1)); continue
  fi

  # The secret reaches gh on stdin and nowhere else: not argv, not the
  # environment, not a temp file.
  printf '%s' "$SECRET" | HOOK_URL="$HOOK_URL" EVENTS="$EVENTS" python3 -c '
import json, os, sys
print(json.dumps({
    "name": "web",
    "active": True,
    "events": json.loads(os.environ["EVENTS"]),
    "config": {
        "url": os.environ["HOOK_URL"],
        # Both stated on purpose. content_type defaults to `form`, which the
        # door does not read and which breaks the signature check. insecure_ssl
        # defaults are not worth guessing: "0" means the certificate is verified.
        "content_type": "json",
        "secret": sys.stdin.read(),
        "insecure_ssl": "0",
    },
}))
' | GH_TOKEN="$TOKEN" gh api -X POST "repos/$NWO/hooks" --input - >/dev/null
  say "$NWO" "created"
  created=$((created + 1))
  # GitHub's binding limit here is the secondary one: no more than 80
  # content-generating requests a minute. One a second stays well under it.
  sleep 1
done <<<"$DESKS"

echo
if [ "$DRY" = "1" ]; then
  echo "would create $created, would update $updated, unchanged $unchanged, skipped $skipped"
else
  echo "created $created, updated $updated, unchanged $unchanged, skipped $skipped"
fi

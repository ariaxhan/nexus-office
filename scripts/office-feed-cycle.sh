#!/bin/sh
# Bounded source refresh and local editorial pass. No model stays resident.
set -eu

VAULT=/Users/slowember/Developer/Vaults
OFFICE="$VAULT/CodingVault/nexus-office"
INPUTS="$VAULT/_meta/services/editorial-inputs.py"
INPUT_PY="$VAULT/_meta/services/.venv/bin/python3"
TRADITION="$VAULT/CodingVault/the-tradition-harness"
STATE="$HOME/.local/state/nexus-office"

mkdir -p "$STATE"
chmod 700 "$STATE"
"$INPUT_PY" "$INPUTS" --vault "$VAULT" --refresh --output "$STATE/editorial-refresh.json" >/dev/null
/opt/homebrew/bin/uv run --project "$TRADITION" python "$OFFICE/client/office_feed_runner.py" --limit 6

#!/usr/bin/env bash
# Stand up your own office, from nothing, in one go.
#
#   ./scripts/setup.sh
#
# Creates the D1 database, writes wrangler.jsonc, applies the schema, mints the
# two internal tokens, asks you for a password, builds, and deploys. Every secret
# goes to the macOS keychain and to a Worker secret; none of them is ever written
# to a file or passed as a command argument.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v npx >/dev/null || { echo "need node + npx" >&2; exit 1; }

if [ ! -f wrangler.jsonc ]; then
  echo "==> creating the D1 database"
  # The id is the only thing that comes back that we cannot guess, so parse it
  # out rather than asking you to copy it between two terminals.
  OUT="$(npx wrangler d1 create nexus-office 2>&1 || true)"
  ID="$(printf '%s' "$OUT" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)"
  if [ -z "$ID" ]; then
    echo "$OUT" >&2
    echo "could not read a database id out of that. Create it by hand, then copy" >&2
    echo "wrangler.example.jsonc to wrangler.jsonc and paste the id in." >&2
    exit 1
  fi
  sed "s/REPLACE_WITH_YOUR_D1_ID/$ID/" wrangler.example.jsonc > wrangler.jsonc
  echo "    wrote wrangler.jsonc"
else
  echo "==> wrangler.jsonc already exists, leaving it alone"
fi

echo "==> applying the schema"
for f in migrations/*.sql; do
  npx wrangler d1 execute nexus-office --remote --file "$f" -y >/dev/null
  echo "    $f"
done

echo "==> minting the internal tokens"
./scripts/mint-tokens.sh both

echo "==> setting your password"
./scripts/set-password.sh

echo "==> building and deploying"
npm install --silent
npm run build >/dev/null
npx wrangler deploy | tail -3

cat <<'DONE'

Your office is up. Two things left:

  1. Point the client at it and push a first snapshot:

       export OFFICE_URL=https://<your-worker-url>
       export OFFICE_OWNERS=your-github-username
       python3 client/office-sync.py --push

  2. Open it and type the password you just set:

       python3 client/office-sync.py --open

Run the client on a schedule (cron, launchd, systemd) and the room stays live.
DONE

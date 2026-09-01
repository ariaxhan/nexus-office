#!/bin/sh
# Read-only; source, installed app, and listener must prove one immutable revision.
set -eu
cd "$(dirname "$0")/.."
exec python3 scripts/runtime_identity.py

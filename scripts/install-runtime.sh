#!/bin/sh
# The conversation adapters use a private, pinned interpreter; no global pip.
set -eu
cd "$(dirname "$0")/.."
command -v uv >/dev/null 2>&1 || { echo 'Office runtime requires uv' >&2; exit 1; }
[ -x .venv/bin/python ] || uv venv --python 3.12 .venv
uv pip sync --python .venv/bin/python requirements-runtime.txt

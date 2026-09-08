#!/bin/bash
# Copyright (c) 2026 Aria Han; MIT. See licenses/kernel-complexity-MIT.txt.
# complexity.sh [options] <repo-dir> [base-ref]
# AST-aware JS/TS when the project has ESLint; lizard for other supported languages.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/complexity.py" "$@"

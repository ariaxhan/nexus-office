#!/bin/sh
#
# Routine contracts; pass --release to include native Swift rules.
#
# The Xcode project is generated from app/project.yml and is not in the repo, so
# a fresh clone has no .xcodeproj to test. The release gate generates it.

set -eu

cd "$(dirname "$0")/.."

python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/pwa_probe.test.mjs tests/markdown.test.mjs

if [ "${1:-}" != "--release" ]; then
  exit 0
fi

if [ ! -d app/Office.xcodeproj ]; then
  command -v xcodegen >/dev/null 2>&1 || {
    echo "test: app/Office.xcodeproj is missing and xcodegen is not installed (brew install xcodegen)" >&2
    exit 1
  }
  ( cd app && xcodegen generate --quiet )
fi

xcodebuild test -project app/Office.xcodeproj -scheme OfficeTests -quiet

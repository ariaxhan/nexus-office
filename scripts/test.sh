#!/bin/sh
#
# Both suites: the python door and the Swift rules.
#
# The Xcode project is generated from app/project.yml and is not in the repo, so
# a fresh clone has no .xcodeproj to test. Generating it here means `npm test`
# works on the first try instead of failing with a path nobody has seen yet.

set -eu

cd "$(dirname "$0")/.."

python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/pwa_probe.test.mjs

if [ ! -d app/Office.xcodeproj ]; then
  command -v xcodegen >/dev/null 2>&1 || {
    echo "test: app/Office.xcodeproj is missing and xcodegen is not installed (brew install xcodegen)" >&2
    exit 1
  }
  ( cd app && xcodegen generate --quiet )
fi

xcodebuild test -project app/Office.xcodeproj -scheme OfficeTests -quiet

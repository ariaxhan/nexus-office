#!/bin/sh
#
# Both suites: the python door and the Swift rules.
#
# The Xcode project is generated from app/project.yml and is not in the repo, so
# a fresh clone has no .xcodeproj to test. Generating it here means `npm test`
# works on the first try instead of failing with a path nobody has seen yet.

set -eu

cd "$(dirname "$0")/.."

# Tests use Python 3.12 syntax; Python 3.14 changed process/fork behavior in
# the flight tests. Pin the gate interpreter instead of following Homebrew's
# rolling `python3`. The installed service has its own runtime.
PYTHON=${OFFICE_TEST_PYTHON:-python3.12}
# Several suites replace process-global environment or module state. Give each
# file a fresh interpreter so one fixture cannot corrupt later flight tests.
for test_file in $(find tests -type f -name 'test_*.py' | sort); do
  # Shared fixture module; it contains no unittest cases.
  [ "$test_file" = tests/test_ledger_fixture.py ] && continue
  PYTHONPATH="$PWD/client:$PWD${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON" -m unittest discover -s "$(dirname "$test_file")" -p "$(basename "$test_file")"
done
node --test tests/pwa_probe.test.mjs tests/markdown.test.mjs

if [ ! -d app/Office.xcodeproj ]; then
  command -v xcodegen >/dev/null 2>&1 || {
    echo "test: app/Office.xcodeproj is missing and xcodegen is not installed (brew install xcodegen)" >&2
    exit 1
  }
  ( cd app && xcodegen generate --quiet )
fi

xcodebuild test -project app/Office.xcodeproj -scheme OfficeTests -quiet

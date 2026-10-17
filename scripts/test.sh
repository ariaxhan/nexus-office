#!/bin/sh
#
# Both suites: the python door and the Swift rules.
#
# The Xcode project is generated from app/project.yml and is not in the repo, so
# a fresh clone has no .xcodeproj to test. Generating it here means `npm test`
# works on the first try instead of failing with a path nobody has seen yet.

set -eu

cd "$(dirname "$0")/.."
# The python and node suites are the regression gate and need no Xcode; only
# the Swift leg at the bottom does. A missing full Xcode therefore skips that
# leg loudly instead of failing every suite on a CLT-only host (the Studio,
# 2026-10-15: four receipt-grammar flights HELD on this gate, nexus-office#291).
if . "$PWD/scripts/use-local-xcode.sh"; then
  XCODE_OK=1
else
  XCODE_OK=0
  echo "test: no full Xcode on this host - Swift suite will be SKIPPED below; python + node suites still run." >&2
fi

# Tests use Python 3.12 syntax; Python 3.14 changed process/fork behavior in
# the flight tests. Pin the gate interpreter instead of following Homebrew's
# rolling `python3`. The installed service has its own runtime.
PYTHON=${OFFICE_TEST_PYTHON:-python3.12}
TEST_STATE="$(mktemp -d)"
trap 'rm -rf "$TEST_STATE"' EXIT
export OFFICE_HUMAN_ASKS_DB="$TEST_STATE/human-asks.sqlite"
# Fixture repos inherit the host's global git config. A signing key behind a
# passphrase fails every fixture commit (rc 128) or hangs on the prompt.
export GIT_CONFIG_COUNT=2 \
  GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false \
  GIT_CONFIG_KEY_1=tag.gpgsign GIT_CONFIG_VALUE_1=false
# Several suites replace process-global environment or module state. Give each
# file a fresh interpreter so one fixture cannot corrupt later flight tests.
for test_file in $(find tests -type f -name 'test_*.py' | sort); do
  # Shared fixture module; it contains no unittest cases.
  [ "$test_file" = tests/test_ledger_fixture.py ] && continue
  PYTHONPATH="$PWD/client:$PWD${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON" -m unittest discover -s "$(dirname "$test_file")" -p "$(basename "$test_file")"
done
node --test tests/pwa_probe.test.mjs tests/markdown.test.mjs tests/active.test.mjs tests/selection.test.mjs tests/questions.test.mjs tests/office_doc.test.mjs

if [ "$XCODE_OK" = 1 ]; then
  if [ ! -d app/Office.xcodeproj ]; then
    command -v xcodegen >/dev/null 2>&1 || {
      echo "test: app/Office.xcodeproj is missing and xcodegen is not installed (brew install xcodegen)" >&2
      exit 1
    }
    ( cd app && xcodegen generate --quiet )
  fi

  xcodebuild test -project app/Office.xcodeproj -scheme OfficeTests -quiet
else
  echo "test: SKIPPED the Swift suite (xcodebuild unavailable on this host)." >&2
fi

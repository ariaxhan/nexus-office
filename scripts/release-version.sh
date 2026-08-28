#!/bin/bash
# release-version - the one place this app's version lives, and the changelog that proves it.
#
#   ./scripts/release-version.sh current       what version are we
#   ./scripts/release-version.sh check         every version field agrees (exit 1 if not)
#   ./scripts/release-version.sh changelog     redraft Unreleased from commits since the last tag
#   ./scripts/release-version.sh bump patch|minor|major
#   ./scripts/release-version.sh release       freeze Unreleased as the current version, and tag it
#
# TWO AXES, deliberately not conflated:
#
#   version                  semver, human-facing, hand-decided.        1.0.0
#   CURRENT_PROJECT_VERSION  a monotonic integer macOS wants for a
#                            bundle. It is not the version and never
#                            matches it; `release` increments it.
#
# THE CHANGELOG IS A DRAFT, NOT A PRODUCT. `changelog` reads the commits since
# the last tag and writes them under Unreleased. 7 of the last 40 commits here
# are conventional, so generating a finished changelog the way matra does would
# put the other 33 in an "Other" pile, and a changelog that is mostly junk
# drawer is one nobody trusts. Conventional commits are sorted into Added,
# Fixed and Changed; everything else is listed verbatim under "Needs a human"
# so it is impossible to ship a release without having read it. Nothing is ever
# dropped: a silent omission is the one failure a changelog cannot survive.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

PKG="package.json"
PROJ="app/project.yml"
CHANGELOG="CHANGELOG.md"
die() { echo "error: $*" >&2; exit 1; }

pkg_version() { python3 -c "import json;print(json.load(open('$PKG'))['version'])"; }
proj_version() { grep -E '^\s*MARKETING_VERSION:' "$PROJ" | head -1 | sed 's/.*"\(.*\)".*/\1/'; }
build_number() { grep -E '^\s*CURRENT_PROJECT_VERSION:' "$PROJ" | head -1 | sed 's/.*"\(.*\)".*/\1/'; }

set_version() { # set_version <semver>
  python3 - "$1" <<'PY'
import json, re, sys, pathlib
new = sys.argv[1]
p = pathlib.Path('package.json'); d = json.loads(p.read_text())
d['version'] = new
p.write_text(json.dumps(d, indent=2) + "\n")
q = pathlib.Path('app/project.yml'); t = q.read_text()
t2, n = re.subn(r'(MARKETING_VERSION:\s*")[0-9]+\.[0-9]+\.[0-9]+(")', rf'\g<1>{new}\g<2>', t)
if n != 1:
    raise SystemExit("FAIL app/project.yml: MARKETING_VERSION not found exactly once")
q.write_text(t2)
print(f"  package.json and app/project.yml -> {new}")
PY
}

cmd_current() { echo "$(pkg_version)  (build $(build_number))"; }

cmd_check() {
  local a b bad=0
  a="$(pkg_version)"; b="$(proj_version)"
  [ "$a" = "$b" ] || { echo "✗ package.json $a != app/project.yml MARKETING_VERSION $b"; bad=1; }
  [[ "$a" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "✗ '$a' is not semver X.Y.Z"; bad=1; }
  grep -q "^## \[$a\]" "$CHANGELOG" 2>/dev/null || { echo "✗ CHANGELOG.md has no released entry for $a"; bad=1; }
  [ "$bad" -eq 0 ] && echo "✓ $a everywhere, and the changelog has its entry"
  return "$bad"
}

cmd_changelog() {
  local since
  since="$(git describe --tags --abbrev=0 2>/dev/null || true)"
  local range="${since:+$since..}HEAD"
  [ -n "$since" ] && echo "commits since $since" || echo "no tags yet; reading the whole history"
  python3 - "$range" "${since:-the beginning}" <<'PY'
import re, subprocess, sys, pathlib
rng, since = sys.argv[1], sys.argv[2]
out = subprocess.run(['git','log','--format=%H%x1f%s','--no-merges',rng],
                     capture_output=True, text=True).stdout.strip()
rows = [l.split('\x1f') for l in out.splitlines() if '\x1f' in l]
buckets = {'Added': [], 'Fixed': [], 'Changed': [], 'Needs a human': []}
kind = {'feat':'Added','fix':'Fixed','perf':'Changed','refactor':'Changed',
        'docs':'Changed','build':'Changed','ci':'Changed','test':'Changed','chore':'Changed'}
pat = re.compile(r'^(?P<t>[a-z]+)(?:\((?P<s>[^)]+)\))?!?:\s*(?P<rest>.+)$')
for sha, subj in rows:
    m = pat.match(subj)
    line = f"- {subj} · [`{sha[:7]}`](https://github.com/ariaxhan/nexus-office/commit/{sha})"
    if m and m['t'] in kind:
        scope = f"**{m['s']}**: " if m['s'] else ""
        buckets[kind[m['t']]].append(
            f"- {scope}{m['rest']} · [`{sha[:7]}`](https://github.com/ariaxhan/nexus-office/commit/{sha})")
    else:
        buckets['Needs a human'].append(line)

body = [f"## [Unreleased]", "",
        f"_Draft, generated from {len(rows)} commit(s) since {since}. "
        f"Rewrite it before releasing: this is a list of what changed, not yet an account of it._", ""]
for name in ('Added','Fixed','Changed','Needs a human'):
    if buckets[name]:
        body.append(f"### {name}")
        body.extend(buckets[name]); body.append("")

p = pathlib.Path('CHANGELOG.md'); text = p.read_text()
start = text.find('## [Unreleased]')
if start == -1:
    head_end = text.find('\n## [')
    head_end = len(text) if head_end == -1 else head_end + 1
    p.write_text(text[:head_end] + "\n".join(body) + "\n" + text[head_end:])
else:
    nxt = text.find('\n## [', start + 1)
    nxt = len(text) if nxt == -1 else nxt + 1
    p.write_text(text[:start] + "\n".join(body) + "\n" + text[nxt:])
print(f"  Unreleased redrafted from {len(rows)} commit(s); "
      f"{len(buckets['Needs a human'])} need a human")
PY
}

cmd_bump() { # cmd_bump patch|minor|major
  local part="${1:?usage: bump patch|minor|major}" cur new
  cur="$(pkg_version)"
  new="$(python3 -c "
import sys
maj,mi,pa = (int(x) for x in '$cur'.split('.'))
p='$part'
print({'major':f'{maj+1}.0.0','minor':f'{maj}.{mi+1}.0','patch':f'{maj}.{mi}.{pa+1}'}[p])")"
  echo "bump $cur -> $new"
  set_version "$new"
  echo "now: ./scripts/release-version.sh changelog, edit CHANGELOG.md, then release"
}

cmd_release() {
  local v; v="$(pkg_version)"
  git diff --quiet || die "working tree is dirty; commit first"
  grep -q "^## \[Unreleased\]" "$CHANGELOG" || die "CHANGELOG.md has no Unreleased section"
  # The generator's own warning must be gone, which is how "edit it by hand"
  # stops being a suggestion.
  if sed -n "/^## \[Unreleased\]/,/^## \[/p" "$CHANGELOG" | grep -q '_Draft, generated'; then
    die "the Unreleased section is still the generated draft. Rewrite it, then release."
  fi
  python3 - "$v" <<'PY'
import datetime, pathlib, sys
v = sys.argv[1]
p = pathlib.Path('CHANGELOG.md'); t = p.read_text()
today = datetime.date.today().isoformat()
p.write_text(t.replace('## [Unreleased]', f'## [{v}] - {today}', 1))
q = pathlib.Path('app/project.yml'); s = q.read_text()
import re
m = re.search(r'(CURRENT_PROJECT_VERSION:\s*")(\d+)(")', s)
if m:
    q.write_text(s[:m.start(2)] + str(int(m.group(2)) + 1) + s[m.end(2):])
    print(f"  build number -> {int(m.group(2)) + 1}")
print(f"  CHANGELOG.md: Unreleased frozen as {v} ({today})")
PY
  git add "$CHANGELOG" "$PROJ"
  git commit -qm "release $v"
  git tag -a "v$v" -m "$v"
  echo "tagged v$v. Push with: git push origin main --follow-tags"
}

case "${1:-current}" in
  current)   cmd_current ;;
  check)     cmd_check ;;
  changelog) cmd_changelog ;;
  bump)      cmd_bump "${2:-}" ;;
  release)   cmd_release ;;
  *) die "unknown command '$1' (current|check|changelog|bump|release)" ;;
esac

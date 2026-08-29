# Changelog

Notable changes to the Office. Entries start as a draft from the commits
(`./scripts/release-version.sh changelog`) and are then rewritten by hand: this
repo's commit messages are prose, not conventional, so generation alone would
produce a list nobody trusts.

Versions are [semver](https://semver.org). **The bundle's build number is a
different number**: a monotonic integer macOS wants, incremented by `release`,
which never matches the version.

| | means |
|---|---|
| patch | fixes only, nothing a person would notice as new |
| minor | new behaviour, nothing existing moved |
| major | something you relied on is gone or works differently |

## [Unreleased]

### Fixed
- Desks show every open pull request. Human and dependency-update branches stay
  read-only; only pipeline branches offer the Office merge control.

## [1.0.0] - 2026-08-28

The room can be trusted about itself.

Everything in this release is one theme: the office now tells the truth about
what is running, what is being worked, and what it has actually looked at.

### Added
- A desk hands back its own Markdown: its README and everything under `_meta`,
  through an allow-list with a containment proof rather than a browser with
  exclusions bolted on. Symlinks are never followed, and a read is answered by
  matching the index rather than re-deriving safety, so an encoding cannot trick it.
- `./scripts/shoot.sh --offscreen` photographs the room without taking the desk:
  the window is ordered to the back, never made key, and the cursor is never
  moved. Sixteen framings with no terminal and no consent, which is what lets an
  unattended lane look at its own work.
- The wall shows which issues are being worked right now and which are waiting
  for a lane, with the quiet ones drawn as quiet rather than as late.
- The Library lists every carried learning by type, and the Clock puts the jobs
  needing work first with their schedule, owner and command.
- Desks sort by owner, recency, name, open issues or open PRs. A sort reorders
  and drops nothing, so a raised hand survives every one of them.
- `./scripts/whats-running.sh` answers whether the thing you are testing is the
  thing you built, and `./scripts/install.sh` makes `/Applications/Office.app`
  the only copy that exists.

### Fixed
- A pinned desk can be dragged. The gesture always worked; dropping one pin onto
  another put it *before* that row, which is where it already was, so the most
  natural gesture produced no visible change. Down now lands after, up before.
- The automation page hands the detail pane back on any click.
- The app icon is drawn from the one installed bundle rather than whichever of
  eight LaunchServices registrations macOS found first.

### Changed
- Lanes run to completion. Every time limit in the pipeline existed because one
  global lock meant one lane anywhere across every repo; lanes are now detached,
  five at a time and one per repo, with no wall-clock kill. A lane is stopped
  only after two hours of complete silence, which is wedged rather than slow.
- No real person, machine or client is named anywhere in this repository, in the
  working tree or in its history.

# MacBook Office fallback candidate

The MacBook's `nexus-office-build` source was 49 commits behind GitHub main at
the 2026-10-16 read-only preflight. Its existing 8447 service is a different
program and must stay untouched. This change adds a separate candidate:

`python client/serve.py --read-only-fallback --port 9443 --snapshot /path/to/verified-office-snapshot.json`

The fallback entry point exits before the normal Office runtime imports. It
starts no polling, notification watcher, webhook, GitHub client, task runner,
or write route. Only `/`, `/api/health`, and `/api/world` answer GET. All write
methods return 405, including `/webhook`. It binds to 127.0.0.1 only. The
snapshot is an explicitly supplied, size-bounded local file; requests only
read it. A stale or missing snapshot returns health 503 and is visibly labeled.

For a future Tailscale Serve route, `--trusted-host` requires `--login`. When
configured, every request requires that exact Host and Tailscale identity,
even if the proxy connects from localhost. Tailscale supplies TLS; port 9443
here is loopback HTTP. Neither a route nor a snapshot copy is configured by
this PR. A phone fallback label requires separate host identity, authenticated
denial, and physical phone readback. The 8447 listener remains unchanged.

## Signed snapshot delivery

`scripts/fallback_snapshot_publish.py` reads the Studio's loopback `/api/world`,
rejects data over 2 MiB or older than five minutes, and signs the serialized
world with a Studio-only Ed25519 SSH key under the `office-fallback` namespace.
It uploads a unique pair of files over host-key-checked SSH. On the MacBook,
`scripts/fallback_snapshot_verify.py` checks the signature against an
`allowed_signers` file, validates the world and ten-minute age limit, refuses
timestamps older than or equal to the installed snapshot, and atomically
replaces `verified-world.json`. The destination directory must be owner-only.
The private key is never copied to the MacBook; provision its public key as
`office-fallback <ssh-ed25519 public key>` in `allowed_signers`. The publisher
requires the exact versioned remote verifier path on each invocation. The
fallback server reads only `verified-world.json` and becomes unhealthy after
15 minutes without a fresh delivery. A failed delivery never changes the last
verified snapshot.

The Studio also runs `scripts/fallback_snapshot_watchdog.py` as a separate
two-minute launchd job. It reads MacBook HTTPS `/api/health` directly, so a
publisher timer that simply never starts can still be detected. At 11 minutes
since the last signed snapshot, or after two consecutive unavailable probes,
it comments once on Office #269. It comments once on recovery below seven
minutes, retaining local dedupe state if GitHub is unavailable. This monitor
never launches the publisher or changes the MacBook snapshot. It shares the
Studio's availability boundary; a real Studio outage and phone reachability
remain separate acceptance tests.

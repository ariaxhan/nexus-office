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

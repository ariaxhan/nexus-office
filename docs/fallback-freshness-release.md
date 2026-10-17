# External fallback freshness: finite release plan

Status: source-only. Fleet PR #29 merged as `12111b1`; the Worker has not been
deployed, D1 has not been provisioned, and no new key has been generated.

## Read-only inventory, 2026-10-17 UTC

- Cloudflare account `aria`, Workers subdomain `ariaxhan`, zone `ariaxhan.com`.
- Access API lists **zero applications**. No verified Access boundary exists for
  a new Worker. Dedicated fallback-freshness D1 is absent. Existing D1 databases
  include fleet-schedules and aria-office-door-observer; neither is reused.
- Office phone is served behind Tailscale Serve, with login checked at the
  Studio door. MacBook fallback 9443 is a distinct read-only Tailscale service.
- Cloudflare cannot call tailnet 9443 directly; MacBook must push its bounded
  signed timestamp outbound. Existing Studio watchdog shares Studio/launchd.
- Fleet GitHub health sentinel already has a five-minute scheduled poll and
  `issues:write`, but no fallback-freshness receiver is armed. GitHub scheduled
  events can delay or drop, so that receiver is a backup, not a hard clock.

## Boundary and order

1. Reserve `fallback-health.ariaxhan.com` as the only Worker route and disable
   `workers.dev` **and preview URLs** in Wrangler. Keep unknown routes 404 and
   action methods absent. Put a self-hosted Access application on the **whole
   hostname**, with Service Auth policies for three distinct tokens: Office
   reader, GitHub sentinel, MacBook sender. Verify policy bindings by readback.
   The `/api/heartbeat` path requires sender Access plus its separate HMAC and
   contains only a boolean and timestamp. Verify no-token GET/POST are denied
   on the custom host and any alternate host; malformed authenticated POST is
   401/422. Protect the host with a bounded WAF rate rule. The current Worker
   sender and Wrangler example need a source patch for this before deployment.
2. Provision a separate D1 and migrate only `fallback-freshness/schema.sql`.
   Export/read back schema and test a local restore. Record a Time Travel
   bookmark before release. Generate a dedicated random 32-byte hex HMAC key;
   store it only in the MacBook 0600 sender configuration and Worker secret.
   Do not copy Studio's Ed25519 private signing key.
3. Deploy isolated Worker with no sender initially. Confirm `/api/health` is
   503 until a Cron tick and a valid heartbeat. Verify the minute cursor with
   at least two natural ticks and observe missing-tick fail-closed in a test
   environment. Check D1 read/write usage and alerts against actual account
   plan before enabling indefinite operation.
4. Extend the **existing** fleet GitHub five-minute health sentinel with an
   armed-by-default-false fallback job, using a dedicated Access read token.
   Do not create a second five-minute workflow: 288 extra private-repo runs
   per day can bill roughly 8,640 runner minutes per month. The receiver tests
   JSON identity/age/cursor and owns one deduplicated incident. Prove manual
   failure/recovery and two natural runs, plus a real notification delivered
   to the phone. GitHub scheduled events can be delayed or dropped, so an
   additional outside uptime receiver or a separately accepted latency limit
   remains a gate. The Studio watchdog keeps repair ownership during pilot;
   GitHub owns only the external-absence incident.
5. Install MacBook outbound sender as a distinct **60-second** launchd job.
   A sleeping/offline MacBook counts as an incident; no silent exception.
   Prove a healthy
   signed snapshot advances the D1 timestamp, a repeated snapshot cannot
   freshen source age, a local 503 downgrades health immediately, and Studio
   absence yields a stale cloud state. Keep 9443 read-only. Do not restart or
   replay Office work from this monitor.
6. Configure the Office server-side reader with the protected exact hostname
   and a separate Access read token. The Watch card shows only fresh/stale/unavailable
   and snapshot age; credentials stay server-side. Verify that no configured
   URL means no request or card. Verify the phone sees the card on its actual
   Tailscale route, including Access rejection and Cloudflare outage states.
   This Studio-hosted Watch card disappears when Studio itself is down. Build
   MacBook fallback's own read-only status projection **before** the outage
   acceptance drill; it must fetch Cloudflare with its own narrowly scoped
   reader credential or display "external state unavailable." Do not infer
   failover health from a cached Studio Watch page.
7. Controlled Studio-offline drill from a physical phone: MacBook fallback
   stays reachable with its last verified snapshot, Cloudflare turns stale by
   the 11-minute bound, and the independent receiver notifies once. Restore
   Studio, verify a newer signed snapshot and one recovery notification. Do
   not claim uninterrupted freshness; Office content must age out visibly.

Pilot ends after seven consecutive days with natural MacBook and Cloudflare
receipts, two natural GitHub receiver runs, one tested alert/recovery, and the
physical-phone Studio-offline drill. Any missing receipt, unauthorized route,
credential leak, or false-green state stops promotion. On failure, disable
sender and receiver, retain D1 evidence for 30 days, then decide whether to
retire the prototype. Cloudflare usage and GitHub minutes get checked daily
during the first week against the account's actual plan.

## Rollback and owner

Keep the current Studio watchdog and Office #269 as incident owner throughout
the pilot. If the new route or alert is wrong, disable the MacBook sender and
Office reader, then disable the Worker trigger; preserve D1 evidence and the
last verified 9443 snapshot. Never delete D1 or rotate keys as an automatic
repair. One alert owner is selected only after the phone and outage drills.

No component can guarantee zero failure. The design detects missing progress
across host boundaries and stops displaying old data as fresh; it does not
automatically repair expired credentials, Cloudflare outages, or ambiguous
business effects.

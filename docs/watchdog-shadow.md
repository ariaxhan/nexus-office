# Office watchdog source conversion (#304)

The first source is `office-watchdog-door`. The existing Studio job writes an
`office-door-shadow/v1` effect for each UTC half hour and reads it back after an
atomic publish. `python3 -m nexus watchdog-door-shadow --receipt PATH` now
validates that receipt and appends an idempotent `watchdog.observed` event to the
Tower ledger. A failed check also opens a `detected` fault record. A later
healthy observation marks a detected-only fault `recovered` without claiming
a repair. Late and out-of-order receipts are retained, but old evidence cannot
change current fault state. The hash checks accidental alteration, not source
authentication; the CLI trusts its local caller and must only receive receipts
from the protected Studio job state. It does not infer a repair or escalate a
technical defect to a person.

This is a source adapter, not a new owner. The Studio probe cannot prove its own
absence, arbitrate a Studio outage through MacBook, or inspect the phone path
from the VM. Muse's door body still owns detection, its one kickstart, and
surfacing. The source job's command and Muse cron are unchanged by this PR.

Before connecting the Studio job to the adapter: verify two same-window
comparisons against the Muse door state, including loopback and phone outcomes;
inject loopback refusal and phone-only failure; then prove fault records are
read from the Office destination. The external Cloudflare absence detector and
one-owner cutover are separate gates. The other three sources (dispatch,
daemons, comms) retain their current routes until this pilot has parity.

The active authority stays bounded: a later owner may make at most one
`com.aria.office-serve` kickstart for a failed loopback/stale snapshot, and only
after SSH plus MacBook path arbitration. Phone-only failure has no restart
authority. It must verify live health after an attempted repair before recording
`verified_fixed`; an unfixed technical fault stays in the machine record.

## The checker program (pd-05)

`nexus/watchdog_checks.py` holds all four watchdogs' deterministic checks as one
program. Check and signature names match the Muse bodies, so the two can be compared
verdict for verdict.

```sh
python3 -m nexus watchdog-check --watchdog door|dispatch|daemons|comms \
  [--health-state PATH|-] [--no-probe] [--compare state-<wd>.json]
```

| watchdog | checks (signatures) |
| --- | --- |
| door | ssh_reachability (passes by running), door_loopback (`door_loopback_failed`, snapshot ≤20 min), phone_path (`phone_path_failed`, `revision_mismatch`) |
| dispatch | dispatch_breathing (`receipts-stale` >20 min, `rc78-cluster`/`timeouts-cluster` ≥5/h), wedge_canary (`canary-wedge-d-fault`: rc≠0 or age >2×observed cadence+30 min), vault_registry_readability (`vault-registry-unreadable`, one `timeout 20 head -c 4096`), health_watch_meta (`health-watch-stale` >75 min; skipped unless the VM passes `--health-state`) |
| daemons | liveness_depth (`daemon-wedged:<job>`, `liveness-stale`), tower_ledger_freshness (`tower-ledger-stale`: tower alive, ledger+WAL mtime >15 min), office_ask_self_check (`office-ask-check-fail`), ollama_real_completion (`ollama-down`, `ollama-inference-fail`, `ollama-wrong-model`), stay_awake (`stay-awake-lapsed`) |
| comms | calendar_mirror_lag (`mirror-engine-stale` >15 min), granola_sync_freshness (`granola-sync-stale`: no ok in 26 h or two newest failed), code_bug_sweep (`code-bug:<Error>:<job>`; rc 78/preflight = vault fault, else transient) |

A run is one occurrence per cadence slot, and running it again in the same slot is a no-op. It writes:

- one `watchdog.checked` event with every check, its evidence, and the consecutive-check streak per signature;
- a #300 `detected` fault record per present signature (source `watchdog:office-<wd>`). A signature that is
  absent on a later run marks that fault `recovered`. If it recurs, it opens a new generation of the record;
- exactly one adversarial probe as a #301 dispatch record (queue `watchdog-probes`, kind `watchdog.probe`,
  rotated by run count). Probes are judgment, so a worker does them and the program never does. A new
  probe is not filed while the previous one for that watchdog is still open. The queue needs a worker
  in `~/.nexus/dispatch-workers.json`; without one, the record escalates `config` after 600 s.

The output's `escalated` field holds the records whose state is `escalated`. Only those leave the machine.

Stays on the VM: the calendar-mirror poster hook, the MacBook SSH arbitration, and the health-watch
state file. The Studio's mirror of that file lags by hours, so it would read as a stale watcher. The
VM pipes the file in with `--health-state -`.

### Shrunk VM bodies (after parity)

```
timeout 300 ~/workspace/scripts/studio-ssh.sh \
  'cd ~/Developer/Vaults/CodingVault/.nexus-office-current && .venv/bin/python -m nexus watchdog-check --watchdog <wd>' \
  [< nexus-health-state.json, with --health-state - for dispatch]
SSH fails on both paths (direct + MacBook hop): surface "Studio unreachable".
Non-empty .escalated: route each record's act_needed. Otherwise: silent.
```

### Proposed jobrun rows (not installed)

Same cadences as Muse: door and dispatch every 30 min, daemons and comms every 60 min. Command:
`<.nexus-office-current>/.venv/bin/python -m nexus watchdog-check --watchdog <wd>` with
`PYTHONPATH=<.nexus-office-current>`, `host: arias-Mac-Studio`, timeout 300 s (daemons can spend
up to 60 s on ask and 150 s on Ollama).

### Shadow evidence, 2026-10-17 05:36Z (Studio, scratch ledger, `--no-probe`)

Every check on all four watchdogs ran and returned rc 0. All verdicts were pass except
health_watch_meta, which read the lagging mirror; that input is now explicit. The check was compared
against Muse's newest mirrored state, which is 36 h old (2026-10-15 ~17:20Z), so this is **not** a
same-window comparison:

- door 3/3 match; daemons 5/5 match.
- comms: granola_sync_freshness reads program pass against Muse fault. The receipts confirm that
  granola has returned ok:true every 2 h since 2026-10-16 21:00Z, so the fault cleared after Muse's state was written.
- dispatch: wedge_canary reads program pass against Muse fault. Canary rc=0 at 2026-10-17 01:00Z
  is within the observed cadence; Muse saw a 14 h-old receipt.

The open acceptance gates are two consecutive same-window cycles (Muse VM run and program in the
same slot), the jobrun install, and the body switch.

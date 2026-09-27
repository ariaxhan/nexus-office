# Decision-plane benchmark (#187)

Run 2026-09-26. Research only: no coordinator change, no Jev dependency, no training.

## Verdict

- **No model tier merits an experiment yet.** In the current ledger, 7,172 `task.state` events
  attribute the decision to `tower policy`; 2,742 have no attribution, so this does not prove all
  routing is deterministic. Measured selection → execution start is under 1 s at p99. Execution
  takes minutes; person waits and backlog can take days.
- The only decision a model could plausibly take over, needs-human triage of untriaged intake, is
  not separable from a majority guess at this sample size: best candidate (Haiku via `claude -p`)
  0.68 [0.52, 0.82] vs base rate 0.59, at 20 s and $0.055 per decision.
- Fix the data before the model: 12 meeting asks whose own intake body says "⛔ Blocked on you"
  (talk to a person, send a document) carry the `ready` label. The deterministic banner is right
  and the label is wrong, so any label-trained or label-scored tier inherits that error.
- Revisit when: independently adjudicated needs-human cases ≥ 200, Jev is accessible, and a
  measured decision stage (not a guess) is on the critical path.

## Harness

`scripts/decision_bench.py` (tests: `tests/test_decision_bench.py`). Ledger opened read-only.
Frozen case file SHA-256: `d39d2fd6c4830a40558806a3dd907f853f0d56667ff5dbd77ac2ebec4ddb9d4c`.
Re-extraction on 2026-09-26 matched byte for byte. Keep case and result files outside the repo
because they contain private issue text; retain them together for an exact rerun.

```sh
S=~/.local/state/nexus-office/decision-bench     # outside the repo: cases hold private issue text
python3.12 scripts/decision_bench.py extract --out $S/cases.jsonl
python3.12 scripts/decision_bench.py run --cases $S/cases.jsonl --candidate rules --out $S/rules.jsonl
python3.12 scripts/decision_bench.py run --cases $S/cases.jsonl --candidate ollama:qwen2.5:3b --out $S/qwen3b.jsonl
python3.12 scripts/decision_bench.py run --cases $S/cases.jsonl --candidate claude:claude-haiku-4-5-20251001 --out $S/haiku.jsonl
python3.12 scripts/decision_bench.py run --cases $S/cases.jsonl --candidate 'cmd:<jev wrapper>' --out $S/jev.jsonl
python3.12 scripts/decision_bench.py score --cases $S/cases.jsonl $S/*.jsonl
python3.12 scripts/decision_bench.py overhead
```

- Same prompt, same parser (`{"p": float}`), same scoring and exact case IDs for every candidate.
  A failed or unparseable call is scored wrong, never skipped. Labels are withheld from candidate
  text. Out-of-range probabilities fail rather than silently becoming 0 or 1.
- Local models go through the Ollama HTTP API: `ollama run --format json` makes gpt-oss abort on
  token repeat (53 of 56 calls failed that way before the switch).
- `cmd:` takes the prompt on stdin and prints `{"p": ..., "cost_usd": ...}`. Jev plugs in there.
- The measured remote path is `claude -p` with Haiku. Codex was not run, so the result does not
  estimate Codex accuracy, latency, or cost.

## Dataset (56 cases, frozen from the Nexus ledger)

| task | source | gold | n (pos) |
| --- | --- | --- | --- |
| needs_human | latest `work.issue` per issue, triaged only | `waiting on human` label, or intake "Blocked on you" banner | 44 (26) |
| lane_held | first `work.lane` attempt per issue | first attempt `HELD` vs `CLOSED` | 12 (3) |

Dropped: 232 issues with no human label (no ground truth). These are historical issue snapshots,
not independent adjudications; labels and intake banners can conflict, and later edits may contain
outcome information. The `lane_held` label is an observed attempt result, not proof the initial
routing decision was correct. These limitations prevent a production accuracy claim.

Requested dimensions without defensible gold in this ledger: urgency, provider/worker routing,
retry/fallback choice, routine versus exceptional, feed-worthiness, and verification level. The
ledger also lacks a complete correction history, so correction rate is **not measurable**. A later
dataset needs pre-decision snapshots, independent labels for each dimension, and linked corrections;
do not treat absence of a correction event as correctness.

## Results

Accuracy with bootstrap 95% CI. "Confident" = p outside (0.2, 0.8), i.e. would not escalate.
Latency is wall clock on this Mac (Ollama warm; p99 includes model load).

**needs_human** (n=44, base rate 0.59)

| candidate | acc [95% CI] | Brier | confident share / acc | p50 / p95 / p99 s | $ total |
| --- | --- | --- | --- | --- | --- |
| rules (banner regex) | 0.61 [0.48, 0.75] | 0.32 | 1.00 / 0.61 | 0 / 0 / 0 | 0 |
| qwen2.5:3b | 0.52 [0.39, 0.66] | 0.30 | 0.34 / 0.40 | 0.13 / 0.15 / 3.8 | 0 |
| qwen2.5:7b | 0.55 [0.39, 0.68] | 0.31 | 0.82 / 0.61 | 0.24 / 0.28 / 8.9 | 0 |
| gpt-oss:20b (think low) | 0.61 [0.48, 0.75] | 0.27 | 0.86 / 0.66 | 0.68 / 0.93 / 1.3 | 0 |
| Haiku 4.5 via `claude -p` | 0.68 [0.52, 0.82] | 0.21 | 0.70 / 0.77 | 19.5 / 32.0 / 34.2 | 2.40 |
| Jev | not run: no key available in this environment | | | unmeasured | unmeasured |

**lane_held** (n=12, base rate 0.75 not-held): every candidate ≤ 0.58, CIs span 0 to 0.83. No
candidate predicts a lane outcome from issue text. Haiku put every case inside the escalation band.

Failure behaviour:

- rules: 3 FP, 14 FN. Misses every needs-human issue without the banner (device passes, store
  screenshots, product decisions).
- gpt-oss: 13 FP, 4 FN; says "human" for most things, confidently.
- Haiku: 7 FP, 7 FN; the only candidate whose confident answers beat its overall accuracy.
- The `claude -p` cost ($0.055/decision) is the harness path, with project context loaded, not
  raw API token price. That is the path Nexus actually uses.
- Local `$0` is API spend only; electricity, hardware, and Ollama startup are excluded. The
  observed local p99 sometimes includes model load, so it should not be compared as warm-only.

## Where coordination time goes (`overhead`)

| stage | n | p50 | p95 | p99 |
| --- | --- | --- | --- | --- |
| selected → execution started | 38 | 0.5 s | 0.7 s | 0.8 s |
| flight queued → started | 2459 | 0 s | 0 s | 0 s |
| execution started → attempt finished | 36 | 7.4 min | 19.7 min | 25.2 min |
| verification started → finished | 35 | 1.8 s | 6.9 s | 4.9 min |
| flight started → ended, failed | 15 | 16 min | 3.2 h | 24 h |
| flight started → ended, cancelled | 54 | 11 min | 3.3 h | 3.4 h |
| issue created → first noticed | 259 | 12 d | 29 d | 176 d |

- "created → noticed" is left-censored: most issues predate Tower. It measures backlog, not a
  detector.
- Recovery: 9 `stranded_no_change`, 4 `dead_owner_claim_released`. The 24 h p99 failed flight is a
  lease expiry, not a slow decision.
- Lifecycle instrumentation covers 5 work items end to end; "work starts → useful output" and
  "useful output → verified done" are not consistently marked. Failure detection timestamps are
  also missing, so failure → detection → recovery cannot be estimated. The recoveries above are
  counts, not recovery latency. Instrument those boundaries before claiming an end-to-end speedup.

## Escalation hierarchy (proposed rule, not built)

1. **Deterministic procedure** owns anything a rule already decides (labels, banners, policy). Today:
   all routing. Escalate only when no rule applies.
2. **Local model**: not justified. No local candidate beat the base rate on needs-human; none was
   calibrated enough that its confident band is trustworthy.
3. **Fast remote decision model (Jev)**: the one untested tier with a plausible cost/latency shape.
   Run it through `cmd:` when a key exists; adopt only if confident-band accuracy ≥ 0.9 on ≥ 200 cases.
4. **Frontier** (Haiku via `claude -p`): best measured, still wrong 1 in 4 when confident; too slow
   and costly per decision to sit in a loop.
5. **Human**: remains the answer for needs-human, and the source of the labels every tier above
   depends on.

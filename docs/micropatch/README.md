# #235 micro-patch benchmark: can tradition turn a frontier plan into verified code locally?

Frozen evidence for nexus-office #235 and subsequent screening. Scores come from the JSON/JSONL receipts beside this README; cancelled-run observations are labelled separately.

2026-10-01 extension commission: `Vaults/_meta/commissions/active/2026-10-01-find-small-local-models-that-pass-our-frozen-tra.md`.

## Setup

- **Cases.** 69 real landed nexus-office commits since 2026-08-01 (`tests/fixtures/micropatch/cases.json`). Each changes one source file, with at most 40 changed lines, and its own test fails before the fix and passes after. The sample is every third case: 23 cases, 11 lifecycle and 12 non-lifecycle (`classes.json`).
- **Verifier, the same for every arm.** The commit's real test runs in a throwaway `git archive` snapshot. Nothing counts unless that test passes and only the allowed file changed.
- **Model.** `qwen3.8:27b` (Ollama, Q4, about 19 GB resident) is used in every arm. Its recommended sampling is used, with no thinking.
- **Arms.**
  - **raw + SEARCH/REPLACE** and **raw + whole-function**: one completion, then one repair from the test output. *Not cold.* The worker was handed the 80-line region around the historical change, or its enclosing function, plus the test diff. That is an oracle for where to edit, so these are an optimistic ceiling for one-shot generation.
  - **tradition + cold**: tradition's `ProviderAgentExecutor` (read/search/edit/run_tests loop, 16 steps, `allowed_paths` set to the one file). It gets the commit message and the failing test output. It must find the code itself.
  - **tradition + frontier contract**: as above, but instead of the commit message it gets a planner-written contract (`contracts.json`, written by Sonnet from the parent source). A contract names the target symbol, the mechanism, the required behavior, the constraints and the test. It never contains a line of the historical fix; this is checked mechanically.

## Results (23 cases each)

| arm | first-pass verified | final verified | lifecycle | non-lifecycle | median wall s | median model tokens | median steps | test failures caught | out-of-scope attempts (all refused) | malformed | leaks | peak resident |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| raw + SEARCH/REPLACE | 9 | **12 (52%)** | 4/11 | 8/12 | 23 | 5,069 | 2 | 25 | 0 | 0 | 0 | 18.2 GB |
| raw + whole-function | 5 | **6 (26%)** | 1/11 | 5/12 | 20 | 2,240 | 2 | 16 | 1 | 8 | 0 | 19.6 GB |
| tradition + cold | 8 | **10 (43%)** | 2/11 | 8/12 | 101 | 54,653 | 16 | 1 | 1 | 2 | 0 | 19.6 GB |
| tradition + frontier contract | 9 | **14 (61%)** | 4/11 | **10/12 (83%)** | 80 | 39,649 | 13 | 3 | 2 | 5 | 0 | 19.6 GB |

By class (final verified / cases):

| arm | known_bugfix | lifecycle | local_logic | mechanical | test_change | wiring |
|---|---|---|---|---|---|---|
| raw + SEARCH/REPLACE | 0/1 | 4/11 | 4/5 | 1/1 | 1/1 | 2/4 |
| raw + whole-function | 0/1 | 1/11 | 2/5 | 1/1 | 0/1 | 2/4 |
| tradition + cold | 1/1 | 2/11 | 4/5 | 1/1 | 0/1 | 2/4 |
| tradition + frontier contract | 1/1 | 4/11 | 5/5 | 1/1 | 1/1 | 2/4 |

How to read the metric columns:

- **First-pass for tradition** means verified with one edit and no failing test run before it.
- **Test failures caught** counts wrong patches the verifier stopped: repair inputs for the raw arms, failing `run_tests` inside the loop for tradition.
- **Out-of-scope attempts** were refused by `allowed_paths`. No out-of-scope write landed in any arm.
- **Leaks: 0.** The per-row `leaked` field in the tradition files lists other live snapshots at the time of sampling. One of them came from a run I killed before cancellation cleanup existed; it was removed. Cancellation is now proven: SIGTERM cleans up, and `kill -9` is reclaimed by the next run.

**Why tradition + contract failed:** 8 of its 9 escalations made **no edit at all**. The model spent the 16-step budget paging through a large file with `read_file`. That is a step-budget and read-efficiency limit, not wrong code. The budget was held fixed for comparability.

**Across all four arms:** 18/23 cases were verified by at least one arm.

## Answers

1. **Does tradition improve the same model?** Not on its own. Cold tradition (10/23) is below the raw arm (12/23). It also costs 4× the wall time and about 10× the tokens.
   - The raw arm was given the exact location; tradition had to find it. So location, not editing, is what a cold local worker lacks.
   - Tradition's value is that it reaches the location safely: bounded tools, `allowed_paths`, a real test before finishing, zero malformed-patch failures that matter.
2. **Does frontier decomposition improve tradition + the same model?** Yes: 10 → **14/23**, and non-lifecycle 8 → **10/12**. It also used fewer steps (13 vs 16) and fewer tokens (40k vs 55k).
3. **What share of these patches finish without frontier implementation work?**
   - **61% overall (14/23) and 83% of non-lifecycle cases (10/12)**, with every failure caught and escalated.
   - The best lifecycle result is 4/11; cold Tradition achieved 2/11 and whole-function replacement 1/11. These results do not support delegating lifecycle work without stronger verification.

## Economics (per patch; the frontier side is an estimate)

- **Local:** about 80 s median wall and about 40k local tokens at about 52 tok/s, with about 19 GB resident while loaded.
- **Frontier planning:** the 23 contracts cost the Sonnet planner about 129k tokens in all, about 5.6k per contract.
- **Frontier implementation:** having the frontier model implement the patch itself, with repo reads, edit, test run and repair, is estimated at 50–150k tokens per patch.
- **Net:** for a delegable non-lifecycle patch, frontier work falls from implementation to one contract plus integration.

## Model roster after #235

| model | verdict | why |
|---|---|---|
| qwen3.8:27b | **keep** | the only model with 0 format failures; 52–58 tok/s; about 19 GB |
| devstral (2025, 24B) | delete | 7/23 raw; dominated by qwen3.8 |
| qwen3:8b | delete | 2/23 raw; lower bound no longer needed |
| qwen2.5-coder:7b, :3b | deleted | 0/23 |

Provenance for every model tried is in `models.jsonl`.

## Small-model screening, 2026-10-01

**Outcome:** three new local candidates were exercised. All passed some workflow
checks; none passed the whole suite. Qwen3-Coder is the fastest measured workflow
candidate here, LFM2.5 has the smallest memory footprint, and Devstral Small 2 has
the highest mean score. No stronger autonomous coding worker was established;
Qwen3.8's historical 14/23 remains the completed coding baseline.

Aria requested existing small local models that actually pass our tests. Same frozen
#235 sample and `packet` worker: frontier contract plus named code, 16 steps,
4,096 output tokens per turn, single allowed file, real test verification. Runs
are sequential on Mac Studio M5 Max / 64 GB, Ollama 0.34.2, 32,768 context.
Nexus revision `d339f91`; Tradition `1bb13f5`; llm-bench `8050b41`.
No production model selection changes. Downloaded weights remain installed;
benchmark model residency was released after the runs.

| model | size / quantization | Tradition packet result | interpretation |
|---|---|---|---|
| Qwen3.8 27B | 27.3B / Q4_K_M | 14/23, including 10/12 non-lifecycle | Historical 2026-09-28 baseline; same frozen cases. |
| LFM2.5 8B | 8.5B / Q4_K_M | 1/5 selected completed cases | Initial four escalated without edits; a separately selected mechanical case passed the final verifier in 95.6 s. Not a full 23-case score. An additional in-progress case was cancelled. |
| Devstral Small 2 24B | 24B / Q4_K_M | unscored: two attempts cancelled | First lifecycle case and first known-bugfix case each ran about six minutes without producing a completed result. Not counted as failed cases. Distinct from the 2025 `devstral:latest` above. |
| Qwen3-Coder 30B-A3B | 30.5B / Q4_K_M; about 21 GB resident | unscored: one attempt cancelled | First known-bugfix case exceeded six minutes without an edit or completed result; not counted as a failed case. |

Supplemental `llm-bench` standard workflow results (11 tests, default per-test
output budgets, temperature 0):

| model | mean score | scores >= 0.5 | scores = 1.0 | wall seconds |
|---|---|---|---|---|
| LFM2.5 8B | 0.5650 | 7/11 | 2/11 | 46.8 |
| Devstral Small 2 24B | 0.8552 | 10/11 | 7/11 | 103.1 |

LFM's successful mechanical case (`74d0762e`) changed an em dash to a hyphen.
The final benchmark verifier passed and only the allowed file changed. However,
`executor_ok=false` and `test_runs=0`: the worker itself never invoked `run_tests`.
This establishes a correct bounded edit with external verification, not autonomous
completion of the requested edit-and-test procedure.

### Comparable full workflow runs

Same 42 test IDs in the same order, temperature 0, 4,096 output tokens per test,
Q4_K_M weights on this Mac. All 126 result rows were checked for matching IDs,
recomputed means, and provider errors (none). Generated-code assertion and syntax
failures remain failures. Each task ran once per model.

| model | mean score | scores = 1.0 | scores >= 0.5 | total wall s | observed resident memory |
|---|---|---|---|---|---|
| LFM2.5 8B | 0.7380 | 21/42 | 33/42 | 356.4 | 5.7 GB |
| Qwen3-Coder 30B-A3B | 0.7702 | 23/42 | 33/42 | 161.6 | about 21 GB |
| Devstral Small 2 24B | 0.7917 | 22/42 | 36/42 | 363.0 | about 20 GB |

| group | LFM2.5 | Qwen3-Coder | Devstral Small 2 |
|---|---|---|---|
| standard (11) | 0.7332 | 0.8512 | 0.8556 |
| hard (10) | 0.6700 | 0.6150 | 0.7233 |
| agentic scenarios (7) | 0.7643 | 0.7143 | 0.8286 |
| adversarial (7) | 0.7619 | 0.7647 | 0.6378 |
| messy (7) | 0.7925 | 0.9262 | 0.9058 |

**Use these as candidates for bounded, independently checked calls:**

- Qwen3-Coder: strongest observed speed/score tradeoff; full scores on JSON repair,
  mixed-format extraction, typo instructions, bug detection, and several recovery
  scenarios. Failed scope judgment, numeric reasoning, and hallucination bait.
- LFM2.5: smallest footprint; full scores on schema transformation, numeric
  reasoning, JSON repair, and context handoff. Failed self-correction, noisy
  extraction, prompt-injection resistance, and code generation.
- Devstral Small 2: highest average and strongest hard/agentic group averages in
  this run; substantially slower than Qwen3-Coder. Failed hallucination bait,
  sycophancy, numeric reasoning, and code generation.

These are screening observations, not validated production routing rules. Group
scores combine different checks and cannot be read as task success probabilities.
The “agentic” group consists of single-turn scenarios, not actual tool execution.
None of the three passed the standalone frontmatter code-generation test.

Timing is observational: LFM's full run overlapped the Qwen3-Coder weight download,
although inference requests were sequential. The machine also runs other services.
The CLI saves scores and verifier diagnostics, not full model responses, so the
workflow JSON files are scoring receipts rather than complete response archives.

Receipts:
`workflow.lfm25-8b-full-4096-20261001.json`,
`workflow.qwen3-coder-30b-full-4096-20261001.json`,
`workflow.devstral-small-2-24b-full-4096-20261001.json`.

### Output-budget diagnostic

LFM2.5's standard 11-test subset rose from 0.5650 (7/11 >= 0.5) at the
default budgets to 0.7332 (9/11 >= 0.5) at 4,096 output tokens. Code generation
still failed a real assertion. These are explicitly different configurations.

These workflow “passes” use the runner's permissive 0.5 threshold; they do not
mean complete correctness. Even 1.0 is the verifier's score, not a human audit.
Do not interpret the runner's “haiku-class” / “sonnet-class” labels as measured
equivalence to those models. LFM produced four empty answers at the default
budget; the larger-budget run recovered output on collision detection, email,
and code generation, but code still failed verification. Devstral generated code but failed
the missing-closing-frontmatter edge case. Workflow tests are single-turn
completion checks, not Tradition agent executions.

Receipts: `tradition.lfm2.5_8b.packet.jsonl`,
`workflow.lfm25-8b-standard-20261001.json`,
`workflow.devstral-small-2-24b-standard-20261001.json`.
Candidate sources: [LFM2.5](https://ollama.com/library/lfm2.5),
[Devstral Small 2](https://ollama.com/library/devstral-small-2),
[Qwen3-Coder](https://ollama.com/library/qwen3-coder).

Reproduction (from the respective repository roots):

```sh
# llm-bench; the source installation was refreshed after its old moved-path
# console entrypoint failed to find its interpreter/package.
.venv/bin/llm-bench run lfm2.5:8b --full --max-tokens 4096 -o results/lfm25-8b-full-4096-20261001.json
.venv/bin/llm-bench run qwen3-coder:30b --full --max-tokens 4096 -o results/qwen3-coder-30b-full-4096-20261001.json
.venv/bin/llm-bench run devstral-small-2:24b --full --max-tokens 4096 -o results/devstral-small-2-24b-full-4096-20261001.json

# nexus-office; appends missing cases, so preserve existing receipts when rerunning.
CLASSES=mechanical "$HOME/Library/Application Support/tradition-harness/imessage-venv/bin/python" scripts/micropatch_tradition.py lfm2.5:8b packet
```

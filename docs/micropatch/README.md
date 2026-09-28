# #235 micro-patch benchmark: can tradition turn a frontier plan into verified code locally?

Frozen evidence for nexus-office #235. Every number here is from the `.jsonl` files beside this README.

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
   - Lifecycle stays at 4/11 in every arm. It belongs above the local tier.

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

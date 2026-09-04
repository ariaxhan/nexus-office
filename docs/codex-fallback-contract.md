# Codex fallback

Source: issue 88.

## Goal

A Claude spend or usage limit reruns the same bounded lane through the supervised Codex lane.

## Done

- The fallback uses the existing checkout, prompt, output contract, and acceptance contract.
- The fallback enters the supervised Codex lane without raw `codex exec`.
- A successful fallback records the lane result through the existing completion path.
- Failure from both executors remains explicit and retains both failure records.

# Human asks

Office Needs You reads `~/.local/state/nexus-office/human-asks.sqlite`. Each row has a stable ID, authoritative source and reference, owner, exact action, creation and observation times, state, resolution evidence, and last source verification. `ask_events` preserves transitions. A failed source read marks an open row stale; it does not remove it.

Sources publish requests with `human_asks.observe(db, item)`. GitHub sources can publish a typed declaration at the start of an issue body or in a comment:

```text
<!-- office-human-ask
{"key":"policy-choice","owner":"aria","action":"Choose the policy before release."}
-->
```

The key is stable within the issue. Office ingests the declaration regardless of the last commenter or `bot_last`. Unstructured prose, an escalation to Tim, and a failed automated pass do not create an Aria ask. Existing free-form issues were individually reviewed and registered in `client/human_asks_sources.json`; that file does not decide their current state.

Office task permissions are ingested from the Nexus ledger's `office.permission` events and closed only by matching `office.permission_closed` events. Harness gates are ingested from the runtime gate files; a successful gate answer through Office supplies their closure receipt. Losing a flight, a gate file, or a source connection leaves the request visible as stale until a source-backed outcome is recorded. The existing permission controls remain available while their execution owner is live.

GitHub issue closure supplies a terminal receipt. A source can record an earlier resolution, dismissal, reassignment, or supersession in a comment:

```text
<!-- office-human-ask-outcome
{"id":"github:owner/repo#42:policy-choice","state":"superseded","evidence":"Link to the later ruling"}
-->
```

For reassignment, include `"owner":"tim"`. Other source systems call `human_asks.transition` with an exact matching `source_ref` and evidence from that source. A possible supersession without such evidence stays open. Office never settles a request from a cached issue row, a missing message in a bounded window, or a process exiting.

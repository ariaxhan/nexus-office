# Office coordinator

Tower's `office-code-work` plan owns Office issue execution on this Studio. It
uses the existing `github-work` task dedupe key and issue claim, so no separate
Office issue worker is installed. Tower records each flight's start, end,
failure and disposition in its SQLite ledger. `code-work` serves other repos.
Office release, task failure and stabilization requests enter as Office issues
and use that same claim and proof path; the native release and creative checks
remain with their existing plans and acceptance rules. Vaults-level routing
may select a project, but does not execute an Office issue a second time.

The Office Watch entry reads that ledger. `running` means a recent flight is
active; `idle` means a recent completed cycle; `failing` means the latest
failure has no later successful cycle; `stalled` means the plan is disabled,
quarantined or has not run in 15 minutes. A process alone cannot establish
health. Inspect Office opens System, where Tower plans, flights and logs live.

Messages in Watch are durable ledger events. A Tower Office work cycle writes
separate read and acted receipts. `prioritize #N` moves an already received
Office issue ahead in the existing issue queue. It does not lift a hold or
change an issue's permissions. Other text is retained with an explicit
unsupported disposition; use the existing Work task conversation for open
ended requests.

Check `scripts/nexus status` and the Office Watch row before recovery. Restart
with `vaults services restart com.nexus.tower`. Tower reuses its ledger after
restart, and the issue claim and inbox receipts prevent a second executor or
a repeated priority action. #180 is already closed; #182 remains p0 and held
pending its permission policy resolution. Keep both ahead of optional Office
work without bypassing that hold.

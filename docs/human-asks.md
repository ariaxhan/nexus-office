# Human input

Execution has three ownership states: OFFICE_OWNED, HUMAN_INPUT_REQUIRED,
and DONE. CI, PR, deployment, rollback, tool failures, and provider turns are
execution progress. They do not transfer ownership.

Each Office task stores its parent goal and terminal condition in its task
specification. A provider turn ending without verification triggers another
Office turn. After three unchanged completions, the phase is
`office_owned_blocked`; the persisted task is still Office-owned across a
restart. A worker records concrete target verification with
`client/report_outcome.py --evidence RECEIPT`, which closes the parent task.
Failed Office flights retry three times. Exhaustion records an Office-owned
blocker without abandoning the parent task or creating a human request.

client/human_asks.py::request_human_input is the only creation path for
HUMAN_INPUT_REQUIRED. It accepts inaccessible authentication, physical action,
new unresolved judgment, an action outside the original authorization, or
unrecoverable missing information. The caller supplies the exact minimum input,
why Office cannot provide it, the authorization gap, and what resumes afterward.
Ordinary technical actions and duplicate approval requests are rejected.

Needs You reads unresolved records from this store. Native gate surfaces read
the same records. Issue text, labels, provider output, old gate files, progress
checks, and recap text cannot create a request. An answer is sent to the owning
task or source issue before the record resolves, so a failed delivery leaves the
request open for retry.

Schema v2 keeps historical v1 rows. Migration reclassifies unvalidated open
legacy asks as Office-owned; the reviewed active asks are resubmitted through
request_human_input during the release. tests/test_human_input_static.py
rejects new direct writers and old producer APIs.

# Standing gates (nexus-office#306, pd-11)

Checkable standing rules enforced in code. Judgment rules (tone, doctrine) stay prose.

## Built

| rule | gate | refuses |
| --- | --- | --- |
| attachment freshness | `nexus/standing_gates.check_fresh`, called by `nexus/mail.send` before any Gmail call | a filename (case-insensitive, per recipient) already sent; ledger `~/.local/state/nexus-mail/sent-attachments.json`, written only after Gmail accepts |
| raw-draft body sends | `nexus/mail.py` (`python3 -m nexus.mail --to --subject --body-file [--attach]`) | no reply/quote/inline-body route exists; test greps tooling for `gmail +reply` / `messages().send(` |
| no proactive security alerts | topics as data in `nexus/standing_topics.json`; `standing_gates.partition` / `security_class`; `client/buzz.notify` drops matches | sign-in, OAuth, password reset, vulnerability/Dependabot, 2FA codes, credential exposure. `gate_raised` exempt: a gate is never hidden |

Proof: `tests/test_standing_gates.py` (stale name refused with zero Gmail calls; `--reply` rejected by argparse; seeded run drops 4 of 6, only the 2 non-security taps reach the relay).

## Proposed, not built (item 4: filed on the issue first)

1. **Decks 18pt minimum** (AGENTS.md:7). Gate in the deck export path: walk every text run of the rendered pptx; any size < 18pt refuses export, naming slide + run. Mechanical; bilingual half stays prose (a language detector is a guess).
2. **Care files belong to Tim** (MEMORY.md:28). Path-ownership list as data; coordinator-repair staging refuses any path under the care globs (care-bench, care-bugbench, care_packet, care_actions). Mechanical.
3. **Drain recovery is never a blind restart** (AGENTS.md:38). Restart of a lane carrying the restart-drain signature refuses unless its checkpoint file was read this attempt (recorded checkpoint hash in the restart request).
4. **Lesson freeze only on exact-hash approval** (MEMORY.md:142). Freeze refuses a head hash with no matching approval record from Aria; approval for hash A does not freeze hash B.

# Standing gates (nexus-office#306, pd-11)

Checkable standing rules enforced in code. Judgment rules (tone, doctrine) stay prose.

## Built

| rule | gate | refuses |
| --- | --- | --- |
| attachment freshness | `nexus/mail.send` checks the local sent-name ledger, then queries Gmail Sent history before creating a draft | a filename already sent to a recipient, including sends older than this route; ledger `~/.local/state/nexus-mail/sent-attachments.json` records new sends only after Gmail accepts |
| raw-draft body sends | `nexus/mail.py` (`python3 -m nexus.mail --to --subject --body-file [--attach]`) | no reply/quote/inline-body route exists; test greps tooling for `gmail +reply` / `messages().send(` |
| no proactive security alerts | topics as data in `nexus/standing_topics.json`; `client/office_feed_runner.run` filters source rows before bundling and rejects generated copy before publishing; `client/buzz.notify` drops matching taps | sign-in, OAuth, password reset, vulnerability/Dependabot, 2FA codes, credential exposure. `gate_raised` is an operational permission request and is exempt |

Proof: `tests/test_standing_gates.py` (a locally recorded stale name causes zero Gmail calls; a historical Gmail match refuses before draft creation; `--reply` is rejected by argparse; the seeded evaluator removes a security source before bundling; only two of six seeded taps reach the relay).

## Proposed, not built (item 4: filed on the issue first)

1. **Decks 18pt minimum** (AGENTS.md:7). Gate in the deck export path: walk every text run of the rendered pptx; any size < 18pt refuses export, naming slide + run. Mechanical; bilingual half stays prose (a language detector is a guess).
2. **Care files belong to Tim** (MEMORY.md:28). Path-ownership list as data; coordinator-repair staging refuses any path under the care globs (care-bench, care-bugbench, care_packet, care_actions). Mechanical.
3. **Drain recovery is never a blind restart** (AGENTS.md:38). Restart of a lane carrying the restart-drain signature refuses unless its checkpoint file was read this attempt (recorded checkpoint hash in the restart request).
4. **Lesson freeze only on exact-hash approval** (MEMORY.md:142). Freeze refuses a head hash with no matching approval record from Aria; approval for hash A does not freeze hash B.

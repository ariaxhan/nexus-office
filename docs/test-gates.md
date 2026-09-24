# Office test gates

`npm test` is the routine stability gate. It runs the retained Python contracts and the two Node probes. `npm run test:release` adds `OfficeTests` through Xcode. `npm run verify` also builds the phone bundle and checks the complexity baseline. The native screenshot proof remains `npm run shot` with inspection of the resulting images; a unit test cannot certify the rendered room. Research and creative acceptance still use their real artifacts and review, with the retained section, care grader, podcast publish and quality tests guarding their basic contracts.

| Risk | Retained contract files | What a failure means |
| --- | --- | --- |
| Data durability and recovery | `test_ledger`, `test_ledger_process_visibility`, `test_private_state`, `test_tower_contract`, `test_work` | Accepted work, events, private state or recovery evidence is lost or replayed. |
| Authorization and GitHub writes | `test_webhook`, `test_merge`, `test_landing_modes`, `test_office_github`, `test_office_github_actions`, `test_office_github_stage`, `test_gate_post_contract` | A write can bypass an owner, gate or delivery proof. |
| Provider isolation | `test_coordinator_chat`, `test_office_objects`, `test_office_user_state`, `test_office_controls`, `test_sync` | Provider or user state leaks across a boundary. |
| Task and Ask ordering | `test_office_tasks`, `test_office_ask`, `test_work`, `test_lanes` | A task is skipped, duplicated or shown in the wrong order. |
| User and release flows | `test_journeys`, `test_flows`, `test_sections`, `test_office_uploads`, `test_office_feed_runner`, `test_shoot_safety`, Node probes, `OfficeTests` | A visible flow, upload, rendering contract or native state rule breaks. |
| Research and creative outputs | `test_care_grader`, `test_podcast_publish`, `test_podcast_quality`, `test_office_media_provenance` | A content artifact loses its required structure, attribution or publication guard. |

The 59 removed Python modules covered internal projections, snapshots, search and cache variants, old Tower paths, and implementation details already exercised by the contracts above. The retained suite is intentionally a stability signal, not an exhaustive mirror of every branch. Add tests for a real regression boundary, preferably at the persisted or user-facing surface. Keep release proof in the native app and actual generated artifacts.

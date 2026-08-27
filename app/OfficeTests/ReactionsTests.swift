import XCTest

/// What a reaction has to survive.
///
/// The interesting cases are all about the key. A reaction that renders is easy
/// and a screenshot would catch it; a reaction that quietly moves to a different
/// sentence between two polls is exactly the kind of defect this project keeps
/// finding on screen instead of in a suite, so it is pinned here.
@MainActor
final class ReactionsTests: XCTestCase {

    private func turn(_ role: String, _ content: String, at: String? = "2026-08-27T18:00:00Z")
        -> ChatTurn {
        ChatTurn(role: role, content: content, at: at)
    }

    /// In memory, so a test never writes to the machine running it.
    private func store() -> Reactions { Reactions(defaults: nil) }

    // MARK: - the key

    func test_a_mark_stays_on_its_own_sentence_when_the_thread_grows() {
        let marks = store()
        let asked = turn("user", "is the funnel up?")
        let answered = turn("assistant", "no, the DNS record is unpublished", at: "2026-08-27T18:01:00Z")
        marks.toggle(.flagged, thread: "chief", turn: answered)

        // The transcript is refetched and a new turn has landed at the top of
        // it. Under an index key the mark would now be on a different line.
        let later = [turn("user", "morning"), asked, answered]
        XCTAssertEqual(marks.reaction(thread: "chief", turn: later[2]), .flagged)
        XCTAssertNil(marks.reaction(thread: "chief", turn: later[0]))
        XCTAssertNil(marks.reaction(thread: "chief", turn: later[1]))
    }

    func test_the_same_words_in_two_threads_are_two_turns() {
        let marks = store()
        let said = turn("assistant", "landed")
        marks.toggle(.agreed, thread: "chief", turn: said)

        XCTAssertEqual(marks.reaction(thread: "chief", turn: said), .agreed)
        XCTAssertNil(marks.reaction(thread: "release", turn: said),
                     "a mark belongs to one conversation")
    }

    func test_the_same_words_from_each_side_are_two_turns() {
        let marks = store()
        marks.toggle(.loved, thread: "chief", turn: turn("user", "ok"))
        XCTAssertNil(marks.reaction(thread: "chief", turn: turn("assistant", "ok")))
    }

    func test_two_turns_a_minute_apart_are_two_turns() {
        let marks = store()
        let first = turn("assistant", "working on it", at: "2026-08-27T18:00:00Z")
        let second = turn("assistant", "working on it", at: "2026-08-27T18:01:00Z")
        marks.toggle(.unclear, thread: "chief", turn: second)

        XCTAssertNil(marks.reaction(thread: "chief", turn: first))
        XCTAssertEqual(marks.reaction(thread: "chief", turn: second), .unclear)
    }

    /// The admitted collision, written down so it is a decision and not a
    /// surprise: no timestamps, same side, same words, one mark between them.
    func test_identical_untimestamped_turns_share_a_mark_and_that_is_known() {
        let marks = store()
        let a = turn("user", "ok", at: nil)
        let b = turn("user", "ok", at: nil)
        marks.toggle(.agreed, thread: "chief", turn: a)
        XCTAssertEqual(marks.reaction(thread: "chief", turn: b), .agreed)
    }

    /// The hash must be the same number tomorrow. `Hasher` is not.
    func test_the_fingerprint_is_stable_and_not_the_seeded_hasher() {
        XCTAssertEqual(Reactions.fingerprint("the funnel is up"),
                       Reactions.fingerprint("the funnel is up"))
        XCTAssertNotEqual(Reactions.fingerprint("up"), Reactions.fingerprint("down"))
        XCTAssertFalse(Reactions.fingerprint("up").isEmpty)
    }

    // MARK: - putting one on and taking it off

    func test_the_same_mark_twice_takes_it_off() {
        let marks = store()
        let said = turn("assistant", "merged")
        marks.toggle(.loved, thread: "chief", turn: said)
        marks.toggle(.loved, thread: "chief", turn: said)
        XCTAssertNil(marks.reaction(thread: "chief", turn: said))
        XCTAssertEqual(marks.count, 0, "taking a mark off leaves nothing behind")
    }

    func test_a_different_mark_replaces_rather_than_stacks() {
        let marks = store()
        let said = turn("assistant", "merged")
        marks.toggle(.agreed, thread: "chief", turn: said)
        marks.toggle(.refused, thread: "chief", turn: said)
        XCTAssertEqual(marks.reaction(thread: "chief", turn: said), .refused)
        XCTAssertEqual(marks.count, 1, "one turn carries one mark")
    }

    func test_clear_removes_it() {
        let marks = store()
        let said = turn("assistant", "merged")
        marks.toggle(.agreed, thread: "chief", turn: said)
        marks.clear(thread: "chief", turn: said)
        XCTAssertNil(marks.reaction(thread: "chief", turn: said))
    }

    // MARK: - the demo floor

    func test_a_seed_fills_an_empty_store() {
        let marks = store()
        let said = turn("assistant", "merged")
        marks.seed([Reactions.key(thread: "chief", turn: said): .loved])
        XCTAssertEqual(marks.reaction(thread: "chief", turn: said), .loved)
    }

    func test_a_seed_never_eats_a_real_mark() {
        let marks = store()
        let said = turn("assistant", "merged")
        marks.toggle(.refused, thread: "chief", turn: said)
        marks.seed([Reactions.key(thread: "chief", turn: said): .loved])
        XCTAssertEqual(marks.reaction(thread: "chief", turn: said), .refused,
                       "the fixture is a floor to photograph, never an edit")
    }

    // MARK: - disk

    func test_marks_survive_a_relaunch() {
        let suite = "reactions.test.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }

        let said = turn("assistant", "merged")
        Reactions(defaults: defaults).toggle(.flagged, thread: "chief", turn: said)

        // A second store is the next launch: same disk, nothing carried over in
        // memory. This is the case the process-seeded `Hasher` would break.
        XCTAssertEqual(Reactions(defaults: defaults).reaction(thread: "chief", turn: said),
                       .flagged)
    }

    func test_a_mark_this_version_does_not_know_is_dropped_not_guessed() {
        let suite = "reactions.test.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }

        let said = turn("assistant", "merged")
        let key = Reactions.key(thread: "chief", turn: said)
        defaults.set([key: "sparkles"], forKey: "reactions.v1")

        let marks = Reactions(defaults: defaults)
        XCTAssertNil(marks.reaction(thread: "chief", turn: said))
        XCTAssertEqual(marks.count, 0)
    }

    func test_nil_defaults_never_writes_anything_down() {
        let said = turn("assistant", "merged")
        let marks = Reactions(defaults: nil)
        marks.toggle(.loved, thread: "chief", turn: said)
        XCTAssertEqual(marks.count, 1, "it still works in memory")

        // The claim is that nothing outlived it. Asserted against a second
        // in-memory store rather than against `UserDefaults.standard`, whose
        // contents are ambient: a machine that had once run the app would make
        // that version of this test fail for a reason that is not this code.
        XCTAssertEqual(Reactions(defaults: nil).count, 0,
                       "an in-memory store leaves nothing behind for the next one")
    }

    // MARK: - the marks themselves

    func test_no_reaction_is_a_face_or_a_hand() {
        // The house rule, as a test rather than as a comment somebody edits
        // past: `GateMark` rejected an SF Symbol hand because it reads as an
        // emoji, and that reasoning binds anything new drawn on this surface.
        let banned = ["face", "hand", "thumb", "person"]
        for reaction in Reaction.allCases {
            for word in banned {
                XCTAssertFalse(reaction.symbol.contains(word),
                               "\(reaction.rawValue) draws \(reaction.symbol), which reads as an emoji")
            }
        }
    }

    func test_every_mark_has_a_symbol_and_a_label() {
        for reaction in Reaction.allCases {
            XCTAssertFalse(reaction.symbol.isEmpty)
            XCTAssertFalse(reaction.label.isEmpty)
            XCTAssertEqual(reaction.label, reaction.label.lowercased(),
                           "these are states, not headlines")
        }
    }
}

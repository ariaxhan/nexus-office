import XCTest

/// One id, one button.
///
/// A verifier drove the real store against a local server and watched it post
/// `q-BBBB` while the screen said `q-AAAA`: the gate poll had moved on and the
/// thread was still drawing the world snapshot's copy. The answer had a live
/// question's id on it and a dead question's text above it, which is exactly
/// how a person approves `rm -rf` believing they approved a push.
///
/// So the id is decided here, from the id that was drawn and the gate that is
/// live, and nothing else gets a vote.
final class GateAnswerTests: XCTestCase {

    func testAMatchPostsTheIdThatWasOnScreen() {
        let live = gate("q-AAAA")
        XCTAssertEqual(GateAnswer.decide(displayedId: "q-AAAA", liveGate: live), .post("q-AAAA"))
    }

    func testAMismatchPostsNothing() {
        let live = gate("q-BBBB", target: "rm -rf ~/Documents/Vaults")
        XCTAssertEqual(GateAnswer.decide(displayedId: "q-AAAA", liveGate: live), .movedOn,
                       "the click was aimed at a question that is gone; it must not land on this one")
    }

    func testNoLiveGatePostsNothing() {
        XCTAssertEqual(GateAnswer.decide(displayedId: "q-AAAA", liveGate: nil), .movedOn)
        XCTAssertEqual(GateAnswer.decide(displayedId: "q-AAAA", liveGate: .clear), .movedOn)
        XCTAssertEqual(GateAnswer.decide(displayedId: "q-AAAA", liveGate: gate("q-AAAA", state: "error")),
                       .movedOn, "a gate that is not pending is not a question anyone can answer")
    }

    func testAnEmptyIdIsNeverPosted() {
        XCTAssertEqual(GateAnswer.decide(displayedId: "", liveGate: gate("")), .movedOn,
                       "a blank id would answer whatever the door happens to have open")
    }

    // MARK: - two hands up at once

    /// The floor is a room, not a queue of one. A person can read the second
    /// question and answer it while the first is still waiting, so "is this
    /// still the question on the floor" is a question about the whole room.
    func testTheSecondHandsIdIsPostedWhileTheFirstIsStillWaiting() {
        let room = [gate("q-AAAA"), gate("q-BBBB", target: "rm -rf node_modules")]
        XCTAssertEqual(GateAnswer.decide(displayedId: "q-BBBB", liveGates: room), .post("q-BBBB"))
        XCTAssertEqual(GateAnswer.decide(displayedId: "q-AAAA", liveGates: room), .post("q-AAAA"))
    }

    func testAnIdNobodyInTheRoomIsAskingIsNeverPosted() {
        let room = [gate("q-AAAA"), gate("q-BBBB")]
        XCTAssertEqual(GateAnswer.decide(displayedId: "q-CCCC", liveGates: room), .movedOn,
                       "a click aimed at a question that has left must not land on either of these")
        XCTAssertEqual(GateAnswer.decide(displayedId: "", liveGates: room), .movedOn)
        XCTAssertEqual(GateAnswer.decide(displayedId: "q-AAAA", liveGates: []), .movedOn)
    }

    /// The exact drift, with company. The oldest question is answered and gone,
    /// the second moves up, and the click that was already on its way down was
    /// aimed at the one that left.
    func testAQuestionThatLeftTakesItsClickWithItRatherThanHandingItToTheNextOne() {
        let after = [gate("q-BBBB", target: "rm -rf ~/Documents/Vaults")]
        XCTAssertEqual(GateAnswer.decide(displayedId: "q-AAAA", liveGates: after), .movedOn)
    }

    func testOnlyAPendingHandCountsAsAQuestion() {
        let room = [gate("q-AAAA", state: "error"), gate("q-BBBB")]
        XCTAssertEqual(GateAnswer.decide(displayedId: "q-AAAA", liveGates: room), .movedOn)
        XCTAssertEqual(GateAnswer.decide(displayedId: "q-BBBB", liveGates: room), .post("q-BBBB"))
    }

    // MARK: - helpers

    private func gate(_ id: String, state: String = "pending",
                      target: String = "git push origin main") -> Gate {
        Gate(state: state, id: id, permission: "run_bash", target: target)
    }
}

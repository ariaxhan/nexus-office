import XCTest

/// What the door's list of raised hands is allowed to do to this app.
///
/// `GET /api/gates` is the only thing that can tell the office somebody is
/// waiting, so the one failure it must never have is going quiet. A key that is
/// not there, an entry written wrong, a list that is not a list: each of those
/// is one fewer hand at worst, never every hand, and never a crash.
final class GatesResponseTests: XCTestCase {

    func testTwoHandsArriveInTheOrderTheyWentUp() throws {
        let answer = try read(#"""
        {"at": "2026-08-27T09:00:00Z",
         "gates": [
           {"state": "pending", "id": "q-first", "permission": "run_bash",
            "target": "git push origin main", "waiting_s": 47, "bot": "release"},
           {"state": "pending", "id": "q-second", "permission": "run_bash",
            "target": "rm -rf node_modules", "waiting_s": 12, "bot": "chief"}
         ]}
        """#)
        XCTAssertEqual(answer.gates.map(\.id), ["q-first", "q-second"])
        XCTAssertEqual(answer.gates.first?.bot, "release")
        XCTAssertEqual(answer.gates.last?.waitingS, 12)
        XCTAssertEqual(answer.at, "2026-08-27T09:00:00Z")
        XCTAssertFalse(answer.quiet.isPending)
    }

    func testAnEmptyFloorIsAnEmptyListAndNotAnError() throws {
        let answer = try read(#"{"at": "2026-08-27T09:00:00Z", "gates": []}"#)
        XCTAssertTrue(answer.gates.isEmpty)
        XCTAssertEqual(answer.quiet.state, "clear")
    }

    func testAHarnessThatIsDownSaysSoWithoutInventingAQuestion() throws {
        let answer = try read(#"{"at": "x", "gates": [], "state": "down"}"#)
        XCTAssertTrue(answer.gates.isEmpty)
        XCTAssertEqual(answer.quiet.state, "down")
        XCTAssertFalse(answer.quiet.isPending, "no state but pending may ever raise a hand")
    }

    func testAMissingGatesKeyIsAQuietFloorRatherThanACrash() throws {
        XCTAssertTrue(try read(#"{"at": "2026-08-27T09:00:00Z"}"#).gates.isEmpty)
        XCTAssertTrue(try read(#"{}"#).gates.isEmpty)
        XCTAssertTrue(try read(#"{"at": "x", "gates": {"not": "a list"}}"#).gates.isEmpty,
                      "a gates key that is not a list is no list at all, not a thrown answer")
    }

    /// The whole reason `Lenient` exists. One record written wrong must cost
    /// that record, not every other person standing there with a hand up.
    func testOneMalformedEntryDropsItselfAndNobodyElse() throws {
        let answer = try read(#"""
        {"at": "x",
         "gates": [
           "this is not a gate",
           {"state": "pending", "id": "q-good", "permission": "run_bash", "target": "ls"},
           null,
           17
         ]}
        """#)
        XCTAssertEqual(answer.gates.map(\.id), ["q-good"])
    }

    /// A gate whose own fields are missing is still a gate: `Gate` fills them
    /// in rather than throwing, so a door that forgot `detail` does not silently
    /// stop showing the question.
    func testAThinEntryIsKeptRatherThanDropped() throws {
        let answer = try read(#"{"at": "x", "gates": [{"state": "pending", "id": "q1"}]}"#)
        XCTAssertEqual(answer.gates.count, 1)
        XCTAssertTrue(answer.gates[0].isPending)
        XCTAssertEqual(answer.gates[0].detail, "")
    }

    /// The literal shape `serve.py._gates` writes, nulls included. `runtime._shape`
    /// sends `bot` and `waiting_s` as null when it does not know them, and a
    /// decoder that treats a null it was not expecting as a broken record would
    /// drop a real raised hand for a field nobody reads.
    func testTheDoorsOwnShapeDecodesNullsAndAll() throws {
        let answer = try read(#"""
        {"at": "2026-08-27T00:41:03Z",
         "gates": [
           {"state": "pending", "id": "a1b2c3d4e5f60718", "permission": "run_bash",
            "target": "git push origin main --follow-tags", "detail": "",
            "asked_at": 1787011200.0, "waiting_s": 47, "bot": "release"},
           {"state": "pending", "id": "f7e6d5c4b3a29180", "permission": "run_bash",
            "target": "rm -rf node_modules", "detail": "",
            "asked_at": null, "waiting_s": null, "bot": null}
         ]}
        """#)
        XCTAssertEqual(answer.gates.count, 2)
        XCTAssertEqual(answer.gates[0].askedAt, 1787011200.0)
        XCTAssertNil(answer.gates[1].bot)
        XCTAssertNil(answer.gates[1].waitingS)
        XCTAssertTrue(answer.gates[1].isPending, "a hand with no name on it is still a hand")
    }

    /// The door says `unconfigured`, `missing-root`, `error` or `unreadable`
    /// rather than one word for trouble. Whatever it says, it makes the floor
    /// quieter and never louder.
    func testAnyWordForTroubleIsStillNobodyWaiting() throws {
        for word in ["down", "unconfigured", "missing-root", "error", "unreadable"] {
            let answer = try read(#"{"at": "x", "gates": [], "state": "\#(word)"}"#)
            XCTAssertEqual(answer.quiet.state, word)
            XCTAssertFalse(answer.quiet.isPending)
        }
    }

    private func read(_ json: String) throws -> GatesResponse {
        try JSONDecoder().decode(GatesResponse.self, from: Data(json.utf8))
    }
}

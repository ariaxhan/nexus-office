import XCTest

/// What an issue carries once the runner has stated a question, and what it
/// carries when it has not.
///
/// The two keys are optional on the wire on purpose: the server emits
/// `decision` only when a bot comment really is the contract, and `landed_pr`
/// only when there is a PR to merge. So absence is the ordinary case and has to
/// decode to nothing rather than to an empty question with no way out of it.
final class IssueDecisionTests: XCTestCase {

    private func decode(_ json: String) throws -> Issue {
        try JSONDecoder().decode(Issue.self, from: Data(json.utf8))
    }

    func testAStatedQuestionArrivesWithItsOptions() throws {
        let issue = try decode("""
        {"number": 402, "title": "Rate limiting returns 500 instead of 429",
         "bot_last": true,
         "decision": {"question": "Which behaviour do we ship?",
                      "options": [
                        {"n": 1, "label": "Return 429", "consequence": "clients stop retrying",
                         "recommended": false},
                        {"n": 2, "label": "Opt in by header", "consequence": "unchanged for everyone else",
                         "recommended": true}]}}
        """)
        XCTAssertTrue(issue.hasDecision)
        XCTAssertEqual(issue.decision?.question, "Which behaviour do we ship?")
        XCTAssertEqual(issue.decision?.options.count, 2)
        XCTAssertEqual(issue.decision?.options.last?.consequence, "unchanged for everyone else")
        XCTAssertNil(issue.landedPr)
    }

    /// The recommended option is read first and answered by its own number. An
    /// office that sorted the list and then sent a position would approve the
    /// option nobody clicked.
    func testTheRecommendedOptionIsFirstAndKeepsItsOwnNumber() throws {
        let issue = try decode("""
        {"number": 1, "title": "t",
         "decision": {"question": "q", "options": [
            {"n": 1, "label": "one"},
            {"n": 2, "label": "two", "recommended": true},
            {"n": 3, "label": "three"},
            {"n": 4, "label": "four"}]}}
        """)
        let ordered = try XCTUnwrap(issue.decision).ordered
        XCTAssertEqual(ordered.map(\.n), [2, 1, 3, 4])
        XCTAssertEqual(ordered.first?.label, "two")
        XCTAssertTrue(try XCTUnwrap(ordered.first).recommended)
    }

    func testAnIssueWithNeitherKeyIsStillAnIssue() throws {
        let issue = try decode("""
        {"number": 4, "title": "Should this repo exist at all?", "bot_last": true,
         "last_word": "I am not going to delete a repo on my own initiative."}
        """)
        XCTAssertNil(issue.decision)
        XCTAssertNil(issue.landedPr)
        XCTAssertFalse(issue.hasDecision)
        XCTAssertEqual(issue.number, 4)
    }

    func testALandedPullRequestNumberArrives() throws {
        let issue = try decode("""
        {"number": 214, "title": "Checkout drops the host name", "bot_last": true,
         "landed_pr": 215}
        """)
        XCTAssertEqual(issue.landedPr, 215)
        XCTAssertFalse(issue.hasDecision)
    }

    /// A block that decoded to nothing must not draw as a question with no
    /// buttons under it, and a malformed one must not take the desk down.
    func testAQuestionWithNoOptionsIsNotADecisionToDraw() throws {
        let issue = try decode("""
        {"number": 9, "title": "t", "decision": {"question": "q", "options": []}}
        """)
        XCTAssertFalse(issue.hasDecision)
        XCTAssertEqual(issue.decision?.options.count, 0)
    }

    func testANullDecisionIsAnIssueWithoutOne() throws {
        let issue = try decode("""
        {"number": 9, "title": "t", "decision": null, "landed_pr": null}
        """)
        XCTAssertNil(issue.decision)
        XCTAssertNil(issue.landedPr)
    }

    func testADecisionOfTheWrongShapeDoesNotFailTheWholeIssue() throws {
        let issue = try decode("""
        {"number": 9, "title": "keeps its title", "decision": "not an object", "landed_pr": "215"}
        """)
        XCTAssertEqual(issue.title, "keeps its title")
        XCTAssertNil(issue.decision)
        XCTAssertNil(issue.landedPr)
    }

    // MARK: - the queue this feeds

    func testTheQueuePutsQuestionsFirstThenMergesThenParks() {
        let asked = Issue(number: 3, title: "asked", updatedAt: "2026-08-26T10:00:00Z",
                          botLast: true,
                          decision: Decision(question: "q",
                                             options: [DecisionOption(n: 1, label: "a"),
                                                       DecisionOption(n: 2, label: "b")]))
        let fixed = Issue(number: 2, title: "fixed", updatedAt: "2026-08-26T12:00:00Z",
                          botLast: true, landedPr: 215)
        let parked = Issue(number: 1, title: "parked", updatedAt: "2026-08-26T13:00:00Z",
                           botLast: true)
        let quiet = Issue(number: 0, title: "quiet", botLast: false,
                          landedPr: 900)
        let queue = StateRules.needsQueue([
            Station(repo: "acme/one", issues: [parked, quiet]),
            Station(repo: "acme/two", issues: [fixed, asked]),
        ])
        XCTAssertEqual(queue.decisions.map(\.issue.number), [3])
        XCTAssertEqual(queue.landed.map(\.issue.number), [2])
        XCTAssertEqual(queue.parks.map(\.issue.number), [1])
        XCTAssertEqual(queue.count, 3)
        // Nobody had the last word on it, so it is nobody's problem yet, even
        // though it carries a PR number.
        XCTAssertFalse(queue.all.contains { $0.issue.number == 0 })
        XCTAssertEqual(queue.parksByDesk.map(\.repo), ["acme/one"])
        XCTAssertEqual(queue.parksByDesk.first?.count, 1)
    }

    /// An empty stamp is an install that has never opened the home. There is no
    /// window to count over, so nothing is counted rather than everything.
    func testCatchUpCountsNothingWithoutAStamp() {
        XCTAssertEqual(StateRules.catchUp(demoAutomation(), since: nil), StateRules.CatchUp())
    }

    func testCatchUpCountsOnlyWhatHappenedAfterTheStamp() {
        let since = try! XCTUnwrap(StateRules.date("2026-08-26T18:00:00Z"))
        let got = StateRules.catchUp(demoAutomation(), since: since)
        XCTAssertEqual(got.worked, 2)
        XCTAssertEqual(got.landed, 1)
        XCTAssertEqual(got.asked, 1)
    }

    private func demoAutomation() -> Automation {
        let json = """
        {"activity": [
          {"at": "2026-08-26T18:12:09Z", "repo": "a/b", "issue": "84", "outcome": "landed"},
          {"at": "2026-08-26T18:06:44Z", "repo": "a/c", "issue": "31", "outcome": "parked"},
          {"at": "2026-08-26T17:10:00Z", "repo": "a/d", "issue": "12", "outcome": "landed"}]}
        """
        return try! JSONDecoder().decode(Automation.self, from: Data(json.utf8))
    }

    // MARK: - the demo floor has to contain what the framings photograph

    func testTheDemoFloorCarriesAFourOptionQuestionAndAMergeButton() throws {
        let url = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // OfficeTests
            .deletingLastPathComponent()   // app
            .appendingPathComponent("Demo/demo.json")
        let world = try JSONDecoder().decode(WorldEnvelope.self, from: Data(contentsOf: url)).world
        let queue = StateRules.needsQueue(world.stations)
        XCTAssertTrue(queue.decisions.contains { $0.issue.decision?.options.count == 4 },
                      "no four-option question on the demo floor to photograph")
        XCTAssertTrue(queue.decisions.contains { $0.issue.decision?.options.count == 2 },
                      "no two-option question on the demo floor")
        XCTAssertTrue(queue.decisions.allSatisfy { row in
            row.issue.decision?.options.contains { $0.recommended } == true
        }, "a question with no recommended option photographs the untinted case only")
        XCTAssertFalse(queue.landed.isEmpty, "nothing on the demo floor draws a merge button")
        XCTAssertFalse(queue.parks.isEmpty, "no park on the demo floor, so the count row is empty")
    }

    private struct WorldEnvelope: Decodable {
        let world: World
    }
}

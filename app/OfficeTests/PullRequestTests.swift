import XCTest

/// What a PR carries as far as the desk pane.
///
/// The body is the note from whoever did the work, and on this floor that is
/// usually the runner rather than a person. Reading it is most of deciding
/// whether to press the one button on that card that cannot be taken back, so
/// it has to survive the decode rather than be quietly dropped on the way in.
final class PullRequestTests: XCTestCase {

    private func decode(_ json: String) throws -> PullRequest {
        try JSONDecoder().decode(PullRequest.self, from: Data(json.utf8))
    }

    func testTheBodyArrivesWithTheRest() throws {
        let pr = try decode("""
        {"number": 215, "title": "stop dropping host_name",
         "head": "pipeline/auto-issue-213", "base": "main",
         "mergeable": "MERGEABLE", "closes": [213],
         "updatedAt": "2026-08-26T18:19:00Z",
         "body": "The guard now runs before the early return.\\n\\nCloses #213."}
        """)
        XCTAssertEqual(pr.number, 215)
        XCTAssertTrue(pr.body.hasPrefix("The guard now runs"))
        XCTAssertEqual(pr.closes, [213])
        XCTAssertTrue(pr.canMerge)
    }

    func testAHumanPullRequestIsVisibleButCannotMergeFromTheOffice() throws {
        let pr = try decode("""
        {"number": 216, "title": "human review",
         "head": "aria/my-own-work", "base": "main",
         "pipeline": false, "mergeable": "MERGEABLE"}
        """)
        XCTAssertFalse(pr.pipeline)
        XCTAssertTrue(pr.isMergeable)
        XCTAssertFalse(pr.canMerge)
    }

    func testAnOlderSnapshotStillTreatsItsPipelineOnlyRowsAsPipelineWork() throws {
        let pr = try decode("""
        {"number": 215, "title": "older snapshot",
         "head": "pipeline/auto-issue-213", "mergeable": "MERGEABLE"}
        """)
        XCTAssertTrue(pr.pipeline)
        XCTAssertTrue(pr.canMerge)
    }

    /// A PR opened with no description is the ordinary case and is not an
    /// error. The card draws nothing rather than an empty panel.
    func testAMissingBodyIsAnEmptyStringAndNotAFailedDecode() throws {
        let pr = try decode("""
        {"number": 33, "title": "guard the cold-start cache read", "draft": true}
        """)
        XCTAssertEqual(pr.body, "")
        XCTAssertFalse(pr.canMerge)
    }

    /// A `null` from a door that says the field exists but has nothing in it
    /// must not take the whole PR down with it.
    func testANullBodyIsStillAPullRequest() throws {
        let pr = try decode("""
        {"number": 9, "title": "t", "body": null, "mergeable": "CONFLICTING"}
        """)
        XCTAssertEqual(pr.body, "")
        XCTAssertEqual(pr.mergeable, "CONFLICTING")
    }

    /// The demo floor has to actually contain the thing the framings are
    /// supposed to photograph. A fixture that lost its bodies is a picture that
    /// proves the empty case renders.
    func testTheDemoFloorCarriesAPullRequestBodyToPhotograph() throws {
        let url = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // OfficeTests
            .deletingLastPathComponent()   // app
            .appendingPathComponent("Demo/demo.json")
        let data = try Data(contentsOf: url)
        let world = try JSONDecoder().decode(WorldEnvelope.self, from: data)
        let bodies = world.world.stations.flatMap { $0.prs }.map(\.body).filter { !$0.isEmpty }
        XCTAssertFalse(bodies.isEmpty, "no PR on the demo floor says anything about itself")
        XCTAssertTrue(bodies.contains { $0.contains("\n\n") },
                      "no PR body has a second paragraph, so 'more' photographs nothing")
        XCTAssertTrue(world.world.stations.contains { station in
            station.prs.contains { pr in
                pr.closes.contains { closed in station.issues.contains { $0.number == closed } }
            }
        }, "no PR closes an issue that has a card on the same desk")
    }

    private struct WorldEnvelope: Decodable {
        let world: World
    }
}

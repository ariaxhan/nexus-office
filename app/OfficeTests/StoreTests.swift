import XCTest

/// What the office remembers while you move around it.
///
/// Two things this file exists to hold down. A half-written message is work a
/// person did, and it must survive clicking somebody else's name: it used to be
/// `@State` inside the composer, so SwiftUI threw it away with the view and two
/// sentences of typing went with it. And putting a desk away has to move the row
/// on the click rather than on the next ten second poll, without ever being able
/// to swallow a raised hand.
///
/// Runs against the demo floor, so there is no server, no network and no
/// account: a check that needs credentials is a check that stops running.
@MainActor
final class StoreTests: XCTestCase {

    // MARK: - a draft belongs to the office, not to the view

    func testAMessageHalfWrittenSurvivesGoingToLookAtSomethingElse() async throws {
        let store = try floor()
        await store.refreshBots()

        store.select(.bot("chief"))
        store.drafts[Store.draftKey(bot: "chief")] = "What is actually blocking the"

        // Off to check something, and back. This is the exact click that used
        // to empty the box.
        store.select(.bot("inbox"))
        XCTAssertEqual(store.drafts[Store.draftKey(bot: "inbox")] ?? "", "",
                       "somebody else's composer starts empty")
        store.select(.bot("chief"))
        XCTAssertEqual(store.drafts[Store.draftKey(bot: "chief")],
                       "What is actually blocking the")

        // A desk in between is no different.
        store.select(.desk("acme/storefront"))
        store.select(.bot("chief"))
        XCTAssertEqual(store.drafts[Store.draftKey(bot: "chief")],
                       "What is actually blocking the")
    }

    func testTwoDraftsNeverOverwriteEachOther() throws {
        let office = try floor()
        office.drafts[Store.draftKey(bot: "chief")] = "to chief"
        office.drafts[Store.draftKey(bot: "release")] = "to release"
        office.drafts[Store.draftKey(repo: "acme/storefront", issue: 214)] = "on 214"
        office.drafts[Store.draftKey(repo: "acme/storefront", issue: 211)] = "on 211"

        XCTAssertEqual(office.drafts.count, 4)
        XCTAssertEqual(office.drafts[Store.draftKey(repo: "acme/storefront", issue: 214)], "on 214")
        XCTAssertEqual(office.drafts[Store.draftKey(repo: "acme/storefront", issue: 211)], "on 211")
        XCTAssertNotEqual(Store.draftKey(bot: "chief"),
                          Store.draftKey(repo: "chief", issue: 0),
                          "a bot called chief and a repo called chief are different boxes")
    }

    func testSendingClearsItsOwnBoxAndNobodyElses() async throws {
        let store = try floor()
        await store.refreshBots()
        store.drafts[Store.draftKey(bot: "chief")] = "cut the release"
        store.drafts[Store.draftKey(bot: "release")] = "still writing this one"

        await store.send(to: "chief", message: "cut the release")

        XCTAssertNil(store.drafts[Store.draftKey(bot: "chief")])
        XCTAssertEqual(store.drafts[Store.draftKey(bot: "release")], "still writing this one")
        // The demo floor answers as well, so the sent line is in the transcript
        // rather than at the end of it. That it is there at all is the point:
        // the box emptied because the message moved, not because it vanished.
        XCTAssertTrue(store.chats["chief"]?.contains {
            $0.isUser && $0.content == "cut the release"
        } == true)
    }

    func testAnEmptyBoxSendsNothingAtAll() async throws {
        let store = try floor()
        await store.refreshBots()
        let before = store.chats["chief"]?.count ?? 0
        await store.send(to: "chief", message: "   \n  ")
        XCTAssertEqual(store.chats["chief"]?.count ?? 0, before)
    }

    // MARK: - put away, and brought back

    func testPuttingADeskAwayMovesTheRowOnTheClick() async throws {
        let store = try floor()
        await store.refreshWorld()
        XCTAssertTrue(store.visibleDesks.contains { $0.repo == "acme/website" })

        await store.setDesk(repo: "acme/website", hidden: true)
        XCTAssertFalse(store.visibleDesks.contains { $0.repo == "acme/website" })
        XCTAssertTrue(store.putAwayDesks.contains { $0.repo == "acme/website" })

        await store.setDesk(repo: "acme/website", hidden: false)
        XCTAssertTrue(store.visibleDesks.contains { $0.repo == "acme/website" })
        XCTAssertFalse(store.putAwayDesks.contains { $0.repo == "acme/website" })
    }

    func testTheFixtureArrivesWithDesksAlreadyPutAway() async throws {
        let store = try floor()
        await store.refreshWorld()
        XCTAssertEqual(store.putAwayDesks.map(\.repo), ["acme/legacy-import"])
        XCTAssertEqual(store.polledLine, "2 of 3 polled")
        XCTAssertEqual(store.putAwayHeadline, "put away (1)")
        XCTAssertFalse(store.putAwayNeedsSomeone)
    }

    func testTheWorldPollCarriesFreshnessAndTheBudget() async throws {
        let store = try floor()
        await store.refreshWorld()
        // The demo floor slides every stamp forward so the newest reads as now,
        // preserving the offsets exactly. So the literal string is not asserted
        // here; the gap it encodes is, which is the thing the header draws.
        XCTAssertFalse(store.worldGenerated.isEmpty)
        XCTAssertEqual(store.github?.isPaused, true)

        let stale = try XCTUnwrap(store.station("acme/website"))
        XCTAssertTrue(StateRules.isStale(station: stale, generated: store.worldGenerated))
        let notice = try XCTUnwrap(StateRules.staleNotice(station: stale, github: store.github))
        XCTAssertTrue(notice.hasPrefix("GitHub is out of budget until "))
        XCTAssertTrue(notice.contains("showing what we had at "))
    }

    // MARK: - the wall

    func testTheWallArrivesWithTheWorldPollInAStableOrder() async throws {
        let store = try floor()
        XCTAssertTrue(store.sections.isEmpty, "nothing on the wall before the first poll")

        await store.refreshWorld()
        XCTAssertEqual(store.sections.map(\.id), ["clock", "cost", "pipeline"])
        XCTAssertEqual(store.section("clock")?.needs, 5)
        XCTAssertNil(store.section("nothing-called-this"))

        // Polling again must not shuffle it, however the object came off the wire.
        await store.refreshWorld()
        XCTAssertEqual(store.sections.map(\.id), ["clock", "cost", "pipeline"])
    }

    /// A source saying five things want a person is the same claim as a desk
    /// waiting on you, so it lights the same dot. Anything else means a person
    /// has to open the window to find out, which is the one job the dot has.
    func testTheWallLightsTheMenuBarDot() async throws {
        let store = try floor()
        await store.refreshBots()
        await store.refreshWorld()

        XCTAssertEqual(store.wallNeeds, 5)
        XCTAssertEqual(store.wallLine, "the wall needs 5")
        XCTAssertEqual(store.dot, .needsYou)
    }

    func testTheNeedsFilterAndTheSearchReachTheWall() async throws {
        let store = try floor()
        await store.refreshWorld()

        // Ordering first: what wants a person, then what is broken, then quiet.
        XCTAssertEqual(store.visibleSections.map(\.id), ["clock", "pipeline", "cost"])

        store.needsOnly = true
        XCTAssertEqual(store.visibleSections.map(\.id), ["clock"])

        store.needsOnly = false
        store.query = "ledger"
        XCTAssertEqual(store.visibleSections.map(\.id), ["cost"],
                       "the sentence under the row is searchable")
        store.query = ""
        XCTAssertEqual(store.visibleSections.count, 3)
    }

    func testASectionCanBeSelectedAndIsNotADesk() async throws {
        let store = try floor()
        await store.refreshWorld()

        store.select(.section("clock"))
        XCTAssertEqual(store.selection, .section("clock"))
        XCTAssertNotEqual(store.selection, .desk("clock"),
                          "a source and a repo with the same name are different rows")
        XCTAssertEqual(store.section("clock")?.card.facts.first?.label, "ok")
    }

    // MARK: - the floor

    /// A three desk office on disk, small enough to assert against exactly.
    /// Dates are far enough in the past that the fixture loader leaves them
    /// alone only if it wants to; the assertions here never read a clock.
    private func floor() throws -> Store {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("office-store-tests-\(UUID().uuidString).json")
        try Data(Self.fixture.utf8).write(to: url)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return Store(api: Api(source: .demo(url)))
    }

    private static let fixture = #"""
    {
      "bots": {
        "runtime": "up",
        "bots": [
          {"id": "chief", "name": "Chief", "purpose": "What is running."},
          {"id": "inbox", "name": "Inbox", "purpose": "What arrived."},
          {"id": "release", "name": "Release", "purpose": "What shipped."}
        ]
      },
      "chats": {"chief": []},
      "gate": {"state": "clear"},
      "world": {
        "generated": "2026-08-26T18:40:00Z",
        "killed": false,
        "github": {"limit": 5000, "remaining": 0, "reset_at": "2026-08-26T18:48:00Z",
                   "cost": 4983, "paused_until": "2026-08-26T18:48:00Z", "error": "spent"},
        "sections": {
          "cost": {"state": "ok", "rows": 91,
                   "card": {"title": "Cost", "headline": "the ledger says $18.42 today",
                            "needs": 0, "as_of": "2026-08-26T18:38:00Z",
                            "facts": [{"label": "today", "value": "$18.42", "tone": ""},
                                      {"label": "estimated (not measured)", "value": "$2.10",
                                       "tone": "warn"}]}},
          "clock": {"state": "ok",
                    "card": {"title": "Clock", "headline": "5 jobs need a look",
                             "needs": 5, "as_of": "2026-08-26T18:39:00Z",
                             "facts": [{"label": "ok", "value": "32", "tone": "ok"},
                                       {"label": "failing", "value": "3", "tone": "bad"}]}},
          "pipeline": {"state": "unconfigured", "detail": "nothing is installed here"}
        },
        "stations": [
          {"repo": "acme/storefront", "access": true, "at": "2026-08-26T18:18:00Z",
           "fetched_at": "2026-08-26T18:18:00Z", "hidden": false,
           "issues": [], "prs": [], "issues_error": null, "prs_error": null},
          {"repo": "acme/website", "access": true, "at": "2026-08-26T18:25:00Z",
           "fetched_at": "2026-08-26T15:10:00Z", "hidden": false,
           "issues": [], "prs": [],
           "issues_error": "GitHub answered 403", "prs_error": "GitHub answered 403"},
          {"repo": "acme/legacy-import", "access": true, "at": "2026-08-26T16:40:00Z",
           "fetched_at": "2026-08-26T09:15:00Z", "hidden": true,
           "issues": [], "prs": [], "issues_error": null, "prs_error": null}
        ]
      }
    }
    """#
}

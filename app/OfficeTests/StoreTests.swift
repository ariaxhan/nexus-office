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

    // MARK: - a picture riding with the message

    /// A picture picked and not yet sent is work in the same way a half-written
    /// sentence is, and it is kept under the same key for the same reason: going
    /// to look at something else and coming back to an empty composer is the
    /// draft bug wearing a different hat.
    func testAPickedPictureSurvivesGoingToLookAtSomethingElse() throws {
        let store = try floor()
        let key = Store.draftKey(bot: "chief")
        store.drafts[key] = "does this look right"
        store.pendingAttachments[key] = Self.picture

        store.select(.bot("release"))
        XCTAssertNil(store.pendingAttachments[Store.draftKey(bot: "release")])
        store.select(.bot("chief"))
        XCTAssertEqual(store.pendingAttachments[key], Self.picture)
        XCTAssertEqual(store.drafts[key], "does this look right")
    }

    func testSendingCarriesThePictureAndMarksTheTurnItRodeWith() async throws {
        let store = try floor()
        await store.refreshBots()
        let key = Store.draftKey(bot: "chief")
        store.drafts[key] = "does this look right"
        store.pendingAttachments[key] = Self.picture

        await store.send(to: "chief", message: "does this look right",
                         attachment: Self.picture)

        // The composer is empty of both, and only this composer.
        XCTAssertNil(store.drafts[key])
        XCTAssertNil(store.pendingAttachments[key])

        let sent = try XCTUnwrap(store.chats["chief"]?.first {
            $0.isUser && $0.content == "does this look right"
        })
        XCTAssertTrue(sent.hasPhoto, "the turn says a photo went with it")
    }

    /// The demo floor's transcript is re-read after a send, exactly as the live
    /// one is. A mark that only exists until the next poll is a mark nobody sees.
    func testTheMarkSurvivesTheTranscriptBeingReadBackFromTheFloor() async throws {
        let store = try floor()
        await store.refreshBots()
        await store.send(to: "chief", message: "look at this", attachment: Self.picture)
        await store.loadChat("chief")

        let sent = try XCTUnwrap(store.chats["chief"]?.first {
            $0.isUser && $0.content == "look at this"
        })
        XCTAssertTrue(sent.hasPhoto)
        XCTAssertFalse(store.chats["chief"]?.contains { !$0.isUser && $0.hasPhoto } == true,
                       "nothing marks a reply nobody attached anything to")
    }

    /// A picture on its own is a whole thing to say, so the empty-box rule does
    /// not apply to it. What the door on the other end makes of that is the
    /// door's business, and it says so in its own words.
    func testAPictureWithNoWordsIsStillSomethingToSend() async throws {
        let store = try floor()
        await store.refreshBots()
        let before = store.chats["chief"]?.count ?? 0
        await store.send(to: "chief", message: "", attachment: Self.picture)
        XCTAssertGreaterThan(store.chats["chief"]?.count ?? 0, before)
        XCTAssertTrue(store.chats["chief"]?.contains { $0.isUser && $0.hasPhoto } == true)
    }

    private static let picture = PreparedImage(
        name: "desk.jpg", mimeType: "image/jpeg", base64: "QUJD",
        bytes: 3, width: 1200, height: 900)

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

    func testPinningMovesTheRowOnTheClickAndSurvivesThePoll() async throws {
        let store = try floor()
        await store.refreshWorld()
        XCTAssertEqual(store.pins, ["acme/website"], "the fixture arrives with one pin")
        XCTAssertEqual(store.roster.first?.header, StateRules.pinnedHeader)

        await store.setPin(repo: "acme/storefront", pinned: true)
        XCTAssertEqual(store.pinOrder, ["acme/website", "acme/storefront"])
        XCTAssertEqual(store.roster.first?.desks.map(\.repo), ["acme/website", "acme/storefront"])

        await store.movePin(repo: "acme/storefront", before: "acme/website")
        XCTAssertEqual(store.roster.first?.desks.map(\.repo), ["acme/storefront", "acme/website"])
        await store.refreshWorld()
        XCTAssertEqual(store.pins, ["acme/storefront", "acme/website"], "the floor kept the order")

        await store.setPin(repo: "acme/website", pinned: false)
        await store.setPin(repo: "acme/storefront", pinned: false)
        XCTAssertNotEqual(store.roster.first?.header, StateRules.pinnedHeader)
        XCTAssertEqual(store.roster.map(\.header), ["acme"])
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

    // MARK: - the automation page gives the detail pane back

    /// The automation page is a whole screen, so while it is open it owns the
    /// detail pane and every click in the roster underneath it landed on a
    /// selection nobody could see. The room read as frozen: the only way out
    /// was to find the close button on the page itself.
    ///
    /// Choosing something IS choosing to leave the page. Merely opening it is
    /// not choosing anything, so what was selected underneath stays selected.
    func testChoosingAnythingLeavesTheAutomationPage() async throws {
        let store = try floor()
        await store.refreshBots()
        await store.refreshWorld()

        store.select(.desk("acme/storefront"))
        store.automationOpen = true
        XCTAssertEqual(store.selection, .desk("acme/storefront"),
                       "opening the page decides nothing about what is underneath it")

        store.select(.bot("chief"))
        XCTAssertFalse(store.automationOpen, "a bot is a thing to look at, not to look at behind a page")
        XCTAssertEqual(store.selection, .bot("chief"))

        store.automationOpen = true
        store.select(.desk("acme/website"))
        XCTAssertFalse(store.automationOpen, "so is a desk")
        XCTAssertEqual(store.selection, .desk("acme/website"))

        store.automationOpen = true
        store.select(.section("clock"))
        XCTAssertFalse(store.automationOpen, "and so is a thing on the wall")
        XCTAssertEqual(store.selection, .section("clock"))
    }

    // MARK: - two hands in the air at once

    /// The M3 check, driven through the real store against a real fixture:
    /// two bots raise gates at once, both show, both answer independently,
    /// neither is lost.
    func testBothHandsShowAndTheOldestIsTheOneOnTop() async throws {
        let store = try crowdedFloor()
        await store.refreshBots()
        await store.refreshWorld()
        await store.refreshGate()

        XCTAssertEqual(store.gates.map(\.id), ["q-first", "q-second"])
        XCTAssertEqual(store.gate.id, "q-first", "the oldest is what every older surface reads")
        XCTAssertTrue(store.showsGateSheet)
        XCTAssertEqual(store.gateQueueLine, "1 of 2, Chief is next")

        // Both bots are marked, not only the one at the front of the queue.
        XCTAssertTrue(store.gateBelongsTo(bot: "release"))
        XCTAssertTrue(store.gateBelongsTo(bot: "chief"))
        XCTAssertEqual(store.gate(for: "chief")?.id, "q-second")
        XCTAssertEqual(store.gate(for: "release")?.id, "q-first")
        XCTAssertNil(store.gate(for: "inbox"))

        // And both desks are marked, each with its own question.
        let first = try XCTUnwrap(store.station("acme/checkout-api"))
        let second = try XCTUnwrap(store.station("acme/storefront"))
        XCTAssertEqual(store.gateShown(at: first)?.id, "q-first")
        XCTAssertEqual(store.gateShown(at: second)?.id, "q-second")
        XCTAssertEqual(StateRules.deskState(station: second, gate: store.gateShown(at: second)),
                       .gated)
    }

    func testAnsweringTheFirstLeavesTheSecondStandingThere() async throws {
        let store = try crowdedFloor()
        await store.refreshBots()
        await store.refreshWorld()
        await store.refreshGate()

        await store.answerGate(id: "q-first", answer: "allow", always: false)

        XCTAssertEqual(store.gates.map(\.id), ["q-second"], "the second hand is still up")
        XCTAssertEqual(store.gate.id, "q-second", "and it is now the one on top")
        XCTAssertTrue(store.showsGateSheet, "the sheet leaves when the room is empty, not the queue")
        XCTAssertNil(store.gateQueueLine, "one hand up says nothing about a queue")
        XCTAssertFalse(store.gateBelongsTo(bot: "release"))
        XCTAssertTrue(store.gateBelongsTo(bot: "chief"))

        await store.answerGate(id: "q-second", answer: "deny", always: false)
        XCTAssertTrue(store.gates.isEmpty)
        XCTAssertFalse(store.showsGateSheet)
        XCTAssertFalse(store.gate.isPending)
    }

    /// A click aimed at a question that has left must land on nothing, not on
    /// whatever moved up into its place.
    func testAnAnswerAimedAtAQuestionThatLeftNeverLandsOnTheNextOne() async throws {
        let store = try crowdedFloor()
        await store.refreshBots()
        await store.refreshGate()
        await store.answerGate(id: "q-first", answer: "allow", always: false)

        await store.answerGate(id: "q-first", answer: "allow", always: true)
        XCTAssertEqual(store.gates.map(\.id), ["q-second"],
                       "the second question is untouched by a click that was never aimed at it")
        XCTAssertEqual(store.toast, "that question has moved on. the floor is now asking about "
                       + "rm -rf ~/code/acme/storefront/node_modules")
    }

    /// One alert per question, however many hands are up. A second bot arriving
    /// while the first is still waiting is its own interruption; a poll that
    /// merely repeats what is already on screen is not.
    func testEveryNewQuestionIsAnnouncedExactlyOnce() async throws {
        let store = try crowdedFloor()
        await store.refreshGate()
        XCTAssertEqual(store.announced, ["q-first", "q-second"])

        await store.refreshGate()
        await store.refreshGate()
        XCTAssertEqual(store.announced, ["q-first", "q-second"],
                       "polling the same two hands twice is not two more interruptions")

        await store.answerGate(id: "q-first", answer: "allow", always: false)
        XCTAssertEqual(store.announced, ["q-first", "q-second"],
                       "the one still waiting was already announced when it went up")
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

    /// A floor with two bots standing there at once, which is the case the
    /// single-gate office could not draw: the second hand waited invisibly
    /// behind the first until somebody answered it.
    private func crowdedFloor() throws -> Store {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("office-store-tests-\(UUID().uuidString).json")
        try Data(Self.crowded.utf8).write(to: url)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return Store(api: Api(source: .demo(url)))
    }

    private static let crowded = #"""
    {
      "bots": {
        "runtime": "up",
        "bots": [
          {"id": "chief", "name": "Chief", "purpose": "What is running."},
          {"id": "release", "name": "Release", "purpose": "What shipped."},
          {"id": "inbox", "name": "Inbox", "purpose": "What arrived."}
        ]
      },
      "chats": {},
      "gates": [
        {"state": "pending", "id": "q-first", "permission": "run_bash",
         "target": "git push origin main --follow-tags", "waiting_s": 47, "bot": "release"},
        {"state": "pending", "id": "q-second", "permission": "run_bash",
         "target": "rm -rf ~/code/acme/storefront/node_modules", "waiting_s": 12, "bot": "chief"}
      ],
      "world": {
        "generated": "2026-08-26T18:40:00Z",
        "killed": false,
        "runtime": {"url": "http://127.0.0.1:8787", "root": "/Users/you/code/checkout-api"},
        "sections": {},
        "stations": [
          {"repo": "acme/checkout-api", "access": true, "at": "2026-08-26T18:18:00Z",
           "fetched_at": "2026-08-26T18:18:00Z", "hidden": false, "issues": [], "prs": []},
          {"repo": "acme/storefront", "access": true, "at": "2026-08-26T18:18:00Z",
           "fetched_at": "2026-08-26T18:18:00Z", "hidden": false, "issues": [], "prs": []},
          {"repo": "acme/docs", "access": true, "at": "2026-08-26T18:18:00Z",
           "fetched_at": "2026-08-26T18:18:00Z", "hidden": false, "issues": [], "prs": []}
        ]
      }
    }
    """#

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
        "pins": ["acme/website"],
        "owners": ["acme"],
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

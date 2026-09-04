import XCTest

/// The desk rules, proved without a screen.
///
/// These are the rules the web room already runs (`src/main.js:117-131`,
/// `src/ui/panel.js:40-42`). If this file and that file ever disagree, one of
/// the two surfaces is lying to a person about whether something needs them, so
/// the ordering is asserted branch by branch rather than sampled.
final class StateRulesTests: XCTestCase {

    // MARK: - the ordering

    func testEachStateIsReachable() {
        XCTAssertEqual(state(Station(repo: "a/gated", outcome: "landed",
                                     issues: [waitingIssue()],
                                     gate: pendingGate())), .gated,
                       "a gate outranks everything: an agent is sitting there with a clock running")

        XCTAssertEqual(state(Station(repo: "a/waiting", access: false, outcome: "landed",
                                     issues: [waitingIssue()])), .waiting,
                       "the thing a person has to do wins the desk")

        XCTAssertEqual(state(Station(repo: "a/locked", access: false, outcome: "landed")), .locked)
        XCTAssertEqual(state(Station(repo: "a/parked", outcome: "parked")), .parked)
        XCTAssertEqual(state(Station(repo: "a/refused", outcome: "refused")), .refused)
        XCTAssertEqual(state(Station(repo: "a/landed", outcome: "landed")), .landed)
        XCTAssertEqual(state(Station(repo: "a/working", outcome: "survey",
                                     issues: [quietIssue()])), .working)
        XCTAssertEqual(state(Station(repo: "a/idle", outcome: "caught-up")), .idle)

        XCTAssertEqual(Set(DeskState.allCases.map(\.rawValue)).count, 8)
    }

    func testARefusalReadsAsAQuestionPosted() {
        XCTAssertEqual(DeskState.refused.label, "question posted")
        XCTAssertEqual(StateRules.outcomeLabel("refused"), "question posted")
        XCTAssertEqual(StateRules.outcomeLabel("landed"), "landed")
    }

    func testTheOrderingIsStrict() {
        // Each state, given every field that a lower state would have claimed.
        let loaded = Station(repo: "a/all", access: false, outcome: "parked",
                             issues: [quietIssue()])
        XCTAssertEqual(state(loaded), .locked, "no push access beats parked")

        let parkedAndRefused = Station(repo: "a/pr", outcome: "parked", issues: [quietIssue()])
        XCTAssertEqual(state(parkedAndRefused), .parked, "parked beats the issue list")

        let refusedWithIssues = Station(repo: "a/ri", outcome: "refused", issues: [quietIssue()])
        XCTAssertEqual(state(refusedWithIssues), .refused, "refused beats working")

        let landedWithIssues = Station(repo: "a/li", outcome: "landed", issues: [quietIssue()])
        XCTAssertEqual(state(landedWithIssues), .landed, "landed beats working")
    }

    func testAccessNilIsNotLocked() {
        // `nil` means the snapshot did not say. Only an explicit false is a lock.
        let unknown = Station(repo: "a/unknown", access: nil, outcome: "caught-up")
        XCTAssertEqual(state(unknown), .idle)
    }

    // MARK: - waiting on you

    func testNeedsHumanPrefersTheComputedField() {
        XCTAssertTrue(StateRules.needsHuman(issue: Issue(number: 1, title: "x", botLast: true)))
        XCTAssertFalse(StateRules.needsHuman(issue: Issue(number: 2, title: "x", botLast: false)))
    }

    func testAStaleLabelNeverOverridesTheComputedField() {
        // The label is a hint that can go stale behind the truth. `bot_last`
        // is computed from the comments themselves, so it wins outright.
        let contradicted = Issue(number: 3, title: "x",
                                 labels: ["waiting on human"], botLast: false)
        XCTAssertFalse(StateRules.needsHuman(issue: contradicted),
                       "a label must not resurrect an issue the bot did not have the last word on")
    }

    func testAMissingFieldIsNotAYesAndNoLabelCanMakeItOne() {
        // The ported rule is `bot_last === true`. A snapshot that does not say
        // is not a snapshot that says yes, and a label a human typed is not
        // evidence about who spoke last.
        XCTAssertFalse(StateRules.needsHuman(issue: Issue(number: 6, title: "x", botLast: nil)))
        for label in ["waiting on human", "needs you", "needs-decision", "blocked", "question",
                      "Waiting On Someone", "NEEDS HUMAN", "bug", "docs"] {
            XCTAssertFalse(StateRules.needsHuman(issue: Issue(number: 4, title: "x", labels: [label])),
                           "\(label) is a hint a person typed, not the comment history")
        }
    }

    func testANullOnTheWireIsNotWaiting() throws {
        // The exact shape that read as "waiting on you" before: the field
        // arrives, explicitly null, next to a label that looks urgent.
        let json = #"{"number":9,"title":"t","bot_last":null,"labels":["waiting on human"]}"#
        let issue = try JSONDecoder().decode(Issue.self, from: Data(json.utf8))
        XCTAssertNil(issue.botLast)
        XCTAssertFalse(StateRules.needsHuman(issue: issue))
    }

    func testWaitingCountAddsUpIssuesNotDesks() {
        let station = Station(repo: "a/many",
                              issues: [waitingIssue(1), waitingIssue(2), quietIssue(3)])
        XCTAssertEqual(StateRules.waitingCount([station, Station(repo: "b/none")]), 2)
    }

    // MARK: - the roster line

    func testLastLineFlattensAndTruncates() {
        let noisy = Bot(id: "chief", name: "Chief",
                        last: ChatTurn(role: "assistant",
                                       content: "  first line\n\nsecond   line\tthird  "))
        XCTAssertEqual(StateRules.lastLine(bot: noisy), "first line second line third")

        let long = Bot(id: "chief", name: "Chief",
                       last: ChatTurn(role: "assistant", content: String(repeating: "a", count: 200)))
        let cut = StateRules.lastLine(bot: long, limit: 20)
        XCTAssertEqual(cut.count, 21, "twenty characters and the ellipsis that says there is more")
        XCTAssertTrue(cut.hasSuffix("\u{2026}"))
        XCTAssertEqual(String(cut.prefix(20)), String(repeating: "a", count: 20))
    }

    func testLastLineOfASilentBotIsEmptyRatherThanInvented() {
        XCTAssertEqual(StateRules.lastLine(bot: Bot(id: "quiet", name: "Quiet")), "")
    }

    func testShortLinesAreLeftAlone() {
        XCTAssertEqual(StateRules.line("all done", limit: 40), "all done")
        XCTAssertFalse(StateRules.line("all done", limit: 40).hasSuffix("\u{2026}"))
    }

    // MARK: - a gate is never hidden

    func testNoFilterCanHideARaisedHand() {
        let gated = Station(repo: "acme/checkout-api", outcome: "survey",
                            issues: [quietIssue()], gate: pendingGate())
        let quiet = Station(repo: "acme/docs", outcome: "caught-up")
        let waiting = Station(repo: "acme/mobile", issues: [waitingIssue()])
        let floor = [gated, quiet, waiting]

        // The needs-me filter would drop it: nothing on that desk needs a person
        // by the issue rule. The gate keeps it anyway.
        let needsOnly = StateRules.visibleDesks(floor, needsOnly: true)
        XCTAssertTrue(needsOnly.contains { $0.repo == gated.repo })

        // A search for something else would drop it too.
        let searched = StateRules.visibleDesks(floor, query: "zzzz-nothing-matches")
        XCTAssertEqual(searched.map(\.repo), [gated.repo])

        // Both at once.
        let both = StateRules.visibleDesks(floor, query: "zzzz", needsOnly: true)
        XCTAssertEqual(both.map(\.repo), [gated.repo])
    }

    func testFiltersStillFilterEverythingElse() {
        let floor = [Station(repo: "acme/docs", outcome: "caught-up"),
                     Station(repo: "acme/mobile", issues: [waitingIssue()]),
                     Station(repo: "northwind/api", issues: [quietIssue()])]

        XCTAssertEqual(StateRules.visibleDesks(floor, needsOnly: true).map(\.repo), ["acme/mobile"])
        XCTAssertEqual(StateRules.visibleDesks(floor, query: "north").map(\.repo), ["northwind/api"])
        XCTAssertEqual(StateRules.visibleDesks(floor, query: "ACME").count, 2)
        XCTAssertEqual(StateRules.visibleDesks(floor).count, 3)
    }

    // MARK: - the roster is grouped, and drops nothing

    private func floorOfThree() -> [Station] {
        [Station(repo: "zeta/tools"), Station(repo: "acme/docs"), Station(repo: "acme/billing"),
         Station(repo: "northwind/api"), Station(repo: "Beta/site"), Station(repo: "acme/Checkout")]
    }

    func testPinnedDesksComeFirstInTheOrderTheyWerePinned() {
        let groups = StateRules.roster(floorOfThree(), pins: ["northwind/api", "acme/billing"])
        XCTAssertEqual(groups.first?.header, StateRules.pinnedHeader)
        XCTAssertEqual(groups.first?.desks.map(\.repo), ["northwind/api", "acme/billing"],
                       "drag order, not alphabetical")
        XCTAssertFalse(groups.dropFirst().flatMap(\.desks).contains { $0.repo == "acme/billing" },
                       "a pinned desk is not also in its owner group")
    }

    func testNoPinsMeansNoPinnedGroup() {
        let groups = StateRules.roster(floorOfThree(), pins: [])
        XCTAssertNotEqual(groups.first?.header, StateRules.pinnedHeader)
    }

    func testYourOwnOwnerComesFirstThenTheRestAToZ() {
        let groups = StateRules.roster(floorOfThree(), pins: [], owners: ["northwind"])
        XCTAssertEqual(groups.map(\.header), ["northwind", "acme", "Beta", "zeta"])
        let alone = StateRules.roster(floorOfThree(), pins: [])
        XCTAssertEqual(alone.map(\.header), ["acme", "Beta", "zeta", "northwind"].sorted { $0.lowercased() < $1.lowercased() },
                       "owners A to Z, case blind, when the door named nobody")
    }

    func testDesksInsideAGroupAreAToZ() {
        let groups = StateRules.roster(floorOfThree(), pins: [], owners: ["acme"])
        XCTAssertEqual(groups[0].desks.map(\.repo), ["acme/billing", "acme/Checkout", "acme/docs"])
    }

    func testAPinNamingADeskNotOnTheFloorIsIgnoredAndDropsNothing() {
        let floor = floorOfThree()
        let groups = StateRules.roster(floor, pins: ["gone/away", "acme/docs", "acme/docs"])
        XCTAssertEqual(groups[0].desks.map(\.repo), ["acme/docs"], "twice pinned is once shown")
        XCTAssertEqual(groups.flatMap(\.desks).count, floor.count)
    }

    func testGroupingNeverLosesARaisedHand() {
        let gated = Station(repo: "acme/checkout-api", outcome: "survey",
                            issues: [quietIssue()], gate: pendingGate())
        let floor = [gated, Station(repo: "acme/docs"), Station(repo: "tiny/scratch")]
        // What the filters would have handed over with everything else filtered
        // out: the gate alone. Grouping must still show it, pinned or not.
        for pins in [[String](), ["tiny/scratch"], ["acme/checkout-api"]] {
            let visible = StateRules.visibleDesks(floor, query: "zzzz", needsOnly: true)
            let groups = StateRules.roster(visible, pins: pins)
            XCTAssertEqual(groups.flatMap(\.desks).map(\.repo), ["acme/checkout-api"], "\(pins)")
        }
        let all = StateRules.roster(StateRules.visibleDesks(floor), pins: ["acme/docs"])
        XCTAssertEqual(all.flatMap(\.desks).count, 3)
    }

    func testAPinnedDeskThatIsPutAwayGoesToTheDrawerNotTheTop() {
        // Put away outranks the pin: it is the later and the more deliberate
        // of the two. The pin is kept in the order, so bringing the desk back
        // puts it straight back at the top.
        let floor = [Station(repo: "acme/docs", hidden: true), Station(repo: "acme/site")]
        let groups = StateRules.roster(StateRules.visibleDesks(floor), pins: ["acme/docs"])
        XCTAssertEqual(groups.map(\.header), ["acme"])
        XCTAssertEqual(groups[0].desks.map(\.repo), ["acme/site"])
        XCTAssertEqual(StateRules.putAwayDesks(floor).map(\.repo), ["acme/docs"])
    }

    // MARK: - the roster can be ordered

    private func floorToSort() -> [Station] {
        [Station(repo: "acme/docs", at: "2026-01-03T00:00:00Z",
                 issues: [quietIssue()], prs: []),
         Station(repo: "zeta/tools", at: "2026-01-05T00:00:00Z",
                 issues: [], prs: [openPR(1), openPR(2)]),
         Station(repo: "Beta/site", at: "",
                 issues: [quietIssue(), quietIssue(2)], prs: [openPR(3)])]
    }

    func testEachOrderPutsTheRightDeskFirst() {
        let floor = floorToSort()
        XCTAssertEqual(StateRules.roster(floor, pins: [], sort: .recent).flatMap(\.desks).map(\.repo),
                       ["zeta/tools", "acme/docs", "Beta/site"],
                       "no readable timestamp sorts last: unknown is not new")
        XCTAssertEqual(StateRules.roster(floor, pins: [], sort: .name).flatMap(\.desks).map(\.repo),
                       ["acme/docs", "Beta/site", "zeta/tools"])
        XCTAssertEqual(StateRules.roster(floor, pins: [], sort: .issues).flatMap(\.desks).map(\.repo),
                       ["Beta/site", "acme/docs", "zeta/tools"])
        XCTAssertEqual(StateRules.roster(floor, pins: [], sort: .prs).flatMap(\.desks).map(\.repo),
                       ["zeta/tools", "Beta/site", "acme/docs"])
    }

    func testAnOrderOtherThanOwnerIsOneGroupUnderItsOwnName() {
        let groups = StateRules.roster(floorToSort(), pins: [], owners: ["acme"], sort: .name)
        XCTAssertEqual(groups.map(\.header), [StateRules.DeskSort.name.label])
        let byOwner = StateRules.roster(floorToSort(), pins: [], owners: ["acme"])
        XCTAssertEqual(byOwner.map(\.header), ["acme", "Beta", "zeta"], "the default is unchanged")
    }

    func testSortingKeepsThePinsOnTopAndDropsNothing() {
        let floor = floorToSort()
        for order in StateRules.DeskSort.allCases {
            let groups = StateRules.roster(floor, pins: ["Beta/site"], sort: order)
            XCTAssertEqual(groups.first?.header, StateRules.pinnedHeader, "\(order)")
            XCTAssertEqual(groups.first?.desks.map(\.repo), ["Beta/site"], "\(order)")
            XCTAssertEqual(groups.flatMap(\.desks).count, floor.count, "\(order)")
        }
    }

    func testNoOrderCanHideARaisedHand() {
        let gated = Station(repo: "acme/checkout-api", at: "", gate: pendingGate())
        let floor = [gated, Station(repo: "acme/docs", at: "2026-01-09T00:00:00Z",
                                    hidden: true, issues: [quietIssue()], prs: [openPR(1)])]
        for order in StateRules.DeskSort.allCases {
            let groups = StateRules.roster(StateRules.visibleDesks(floor, query: "zzzz", needsOnly: true),
                                           pins: [], sort: order)
            XCTAssertEqual(groups.flatMap(\.desks).map(\.repo), [gated.repo], "\(order)")
        }
    }

    func testDroppingOnARowLandsJustAboveItAndOnNothingLandsLast() {
        let pins = ["a/one", "a/two", "a/three"]
        XCTAssertEqual(StateRules.moved(pins: pins, repo: "a/three", before: "a/one"),
                       ["a/three", "a/one", "a/two"])
        XCTAssertEqual(StateRules.moved(pins: pins, repo: "a/one", before: nil),
                       ["a/two", "a/three", "a/one"])
        XCTAssertEqual(StateRules.moved(pins: pins, repo: "a/two", before: "a/two"), pins,
                       "dropped on itself, nothing moves")
        XCTAssertEqual(StateRules.moved(pins: pins, repo: "b/new", before: "a/two"),
                       ["a/one", "b/new", "a/two", "a/three"], "a drop pins what was not pinned")
    }

    // MARK: - the gate needs a desk to stand at

    func testAGateAttachesToTheRepoTheRuntimeIsIn() {
        let floor = [Station(repo: "acme/checkout-api"), Station(repo: "acme/docs")]
        let out = StateRules.attachGate(stations: floor,
                                        runtime: RuntimeInfo(root: "/Users/you/code/checkout-api"),
                                        gate: pendingGate())
        XCTAssertEqual(out.count, 2)
        XCTAssertEqual(out.first { $0.gate?.isPending == true }?.repo, "acme/checkout-api")
    }

    func testAHomelessGateGetsADeskOfItsOwn() {
        let out = StateRules.attachGate(stations: [Station(repo: "acme/docs")],
                                        runtime: RuntimeInfo(root: "/tmp/somewhere-else"),
                                        gate: pendingGate())
        XCTAssertEqual(out.count, 2, "the gate is never dropped on the floor")
        let host = out.first { $0.gate?.isPending == true }
        XCTAssertEqual(host?.repo, "runtime/somewhere-else")
        XCTAssertTrue(host?.synthetic == true)
        XCTAssertEqual(state(host!), .gated)
    }

    func testAClearGateAttachesToNobody() {
        let out = StateRules.attachGate(stations: [Station(repo: "acme/docs", gate: pendingGate())],
                                        runtime: RuntimeInfo(root: "/x/docs"),
                                        gate: Gate.clear)
        XCTAssertNil(out[0].gate)
        XCTAssertEqual(out.count, 1)
    }

    func testAttachingTwiceLeavesTheSameFloor() {
        // The gate poll is five times faster than the world poll, so this runs
        // against a list that already holds the desk it invented last time.
        let once = StateRules.attachGate(stations: [Station(repo: "acme/docs")],
                                         runtime: RuntimeInfo(root: ""), gate: pendingGate())
        let twice = StateRules.attachGate(stations: once,
                                          runtime: RuntimeInfo(root: ""), gate: pendingGate())
        XCTAssertEqual(once.map(\.repo), twice.map(\.repo), "the roster must not grow while you watch it")
        XCTAssertEqual(twice.filter(\.synthetic).count, 1)
    }

    // MARK: - two hands in the air at once

    /// One desk can only draw one question, so two questions need two desks.
    /// The world snapshot knows where exactly one agent is, so that root goes to
    /// the oldest hand and the second is placed by what it says out loud.
    func testTwoGatesGetADeskEachRatherThanSharingOne() {
        let floor = [Station(repo: "acme/checkout-api"), Station(repo: "acme/storefront"),
                     Station(repo: "acme/docs")]
        let out = StateRules.attachGates(stations: floor,
                                         runtime: RuntimeInfo(root: "/Users/you/code/checkout-api"),
                                         gates: [pendingGate(), secondGate()])
        XCTAssertEqual(out.first { $0.gate?.id == pendingGate().id }?.repo, "acme/checkout-api")
        XCTAssertEqual(out.first { $0.gate?.id == secondGate().id }?.repo, "acme/storefront")
        XCTAssertEqual(out.filter { $0.gate?.isPending == true }.count, 2)
        XCTAssertEqual(out.count, 3, "neither of them needed a desk inventing")
    }

    func testASecondGateNamingNobodyStillGetsADeskOfItsOwn() throws {
        let floor = [Station(repo: "acme/checkout-api")]
        let homeless = Gate(state: "pending", id: "q-homeless", permission: "run_bash",
                            target: "make deploy", bot: "chief")
        let out = StateRules.attachGates(stations: floor,
                                         runtime: RuntimeInfo(root: "/Users/you/code/checkout-api"),
                                         gates: [pendingGate(), homeless])
        XCTAssertEqual(out.count, 2, "the second hand is never dropped on the floor")
        let host = try XCTUnwrap(out.first { $0.gate?.id == "q-homeless" })
        XCTAssertEqual(host.repo, "runtime/chief")
        XCTAssertTrue(host.synthetic)
    }

    func testAttachingTwoTwiceLeavesTheSameFloor() {
        let homeless = Gate(state: "pending", id: "q-homeless", target: "make deploy", bot: "chief")
        let once = StateRules.attachGates(stations: [Station(repo: "acme/docs")],
                                          runtime: RuntimeInfo(root: ""),
                                          gates: [pendingGate(), homeless])
        let twice = StateRules.attachGates(stations: once, runtime: RuntimeInfo(root: ""),
                                           gates: [pendingGate(), homeless])
        XCTAssertEqual(once.map(\.repo), twice.map(\.repo),
                       "the roster must not grow one row every two seconds")
        XCTAssertEqual(twice.filter { $0.gate?.isPending == true }.count, 2)
    }

    /// The point of the whole change. A desk that owns the second question draws
    /// the second question; a desk that owns the first draws the first.
    func testEachDeskDrawsItsOwnQuestionAndNotOnlyTheOldest() throws {
        let floor = StateRules.attachGates(
            stations: [Station(repo: "acme/checkout-api"), Station(repo: "acme/storefront")],
            runtime: RuntimeInfo(root: "/Users/you/code/checkout-api"),
            gates: [pendingGate(), secondGate()])
        let room = [pendingGate(), secondGate()]

        let first = try XCTUnwrap(floor.first { $0.repo == "acme/checkout-api" })
        let second = try XCTUnwrap(floor.first { $0.repo == "acme/storefront" })
        XCTAssertEqual(StateRules.gateShown(gates: room, at: first, stations: floor)?.id,
                       pendingGate().id)
        XCTAssertEqual(StateRules.gateShown(gates: room, at: second, stations: floor)?.id,
                       secondGate().id)
    }

    /// A gate nobody can place is shown everywhere, and that fallback must not
    /// paint over a desk that actually owns a later question.
    func testAnUnplaceableGateNeverCoversADeskThatOwnsALaterOne() {
        let floor = [Station(repo: "acme/checkout-api"),
                     Station(repo: "acme/storefront", gate: secondGate())]
        let room = [pendingGate(), secondGate()]
        XCTAssertEqual(StateRules.gateShown(gates: room, at: floor[1], stations: floor)?.id,
                       secondGate().id, "the desk that owns it beats the one nobody could place")
        XCTAssertEqual(StateRules.gateShown(gates: room, at: floor[0], stations: floor)?.id,
                       pendingGate().id, "and the one nobody could place is still shown somewhere")
    }

    func testNoHandsUpMeansNoDeskDrawsOne() {
        let floor = [Station(repo: "acme/docs")]
        XCTAssertNil(StateRules.gateShown(gates: [], at: floor[0], stations: floor))
        XCTAssertNil(StateRules.gateShown(gates: [.clear], at: floor[0], stations: floor))
    }

    // MARK: - whose hand is up

    func testABotSecondInLineStillHasItsOwnRaisedHand() {
        let room = [pendingGate(), secondGate()]
        XCTAssertEqual(StateRules.gate(in: room, for: "chief")?.id, secondGate().id)
        XCTAssertEqual(StateRules.gate(in: room, for: "release")?.id, pendingGate().id)
        XCTAssertTrue(StateRules.gateBelongsTo(gates: room, bot: "chief"))
        XCTAssertTrue(StateRules.gateBelongsTo(gates: room, bot: "release"))
        XCTAssertFalse(StateRules.gateBelongsTo(gates: room, bot: "inbox"))
        XCTAssertFalse(StateRules.gateBelongsTo(gates: room, bot: ""),
                       "a gate the door could not name a bot for belongs to nobody's thread")
    }

    func testAHandThatIsNoLongerPendingBelongsToNobody() {
        let stale = Gate(state: "clear", id: "q-old", bot: "release")
        XCTAssertNil(StateRules.gate(in: [stale], for: "release"))
    }

    // MARK: - the line that says how many are left

    func testOneHandUpSaysNothingAboutAQueue() {
        XCTAssertNil(StateRules.gateQueueLine([]))
        XCTAssertNil(StateRules.gateQueueLine([pendingGate()]), "\"1 of 1\" is noise")
        XCTAssertNil(StateRules.gateQueueLine([pendingGate(), Gate(state: "clear", id: "x")]),
                     "a gate that is not pending is not somebody waiting")
    }

    func testTwoHandsUpSayHowManyAndWhoIsNext() {
        let room = [pendingGate(), secondGate()]
        XCTAssertEqual(StateRules.gateQueueLine(room) { $0 == "chief" ? "Chief" : nil },
                       "1 of 2, Chief is next")
        XCTAssertEqual(StateRules.gateQueueLine(room), "1 of 2, chief is next",
                       "with no roster to ask, the id it was given is still a name")
        XCTAssertEqual(StateRules.gateQueueLine(room + [pendingGate()]) { _ in "Chief" },
                       "1 of 3, Chief is next")
    }

    func testAQueueBehindANamelessGateStillSaysHowManyAreLeft() {
        let nameless = Gate(state: "pending", id: "q-nameless", target: "make deploy")
        XCTAssertEqual(StateRules.gateQueueLine([pendingGate(), nameless]),
                       "1 of 2, another is next")
    }

    // MARK: - which desks draw the raised hand

    func testAGateNamingARepoIsShownAtThatDeskAlone() {
        let floor = [Station(repo: "acme/checkout-api"), Station(repo: "acme/docs")]
        let gate = Gate(state: "pending", id: "q1", permission: "run_bash",
                        target: "gh pr merge 12 --repo acme/checkout-api")
        XCTAssertEqual(StateRules.gateDesks(gate: gate, stations: floor), ["acme/checkout-api"])
    }

    func testAShortNameCountsOnlyAsAWholeWordAndOnlyWhenItIsAWord() {
        let floor = [Station(repo: "acme/storefront"), Station(repo: "acme/docs")]

        let substring = Gate(state: "pending", id: "q1", target: "python capitalise.py",
                             detail: "docstrings only, no storefronts")
        XCTAssertEqual(StateRules.gateDesks(gate: substring, stations: floor),
                       ["acme/storefront", "acme/docs"],
                       "a substring is not a name: it names nobody, so it is shown at every desk")

        let word = Gate(state: "pending", id: "q2", target: "cd ~/code/storefront && make release")
        XCTAssertEqual(StateRules.gateDesks(gate: word, stations: floor), ["acme/storefront"])
    }

    func testANameTooShortToMeanAnythingNeverClaimsTheGate() {
        // `api` is three characters and turns up in every second URL. Claiming
        // the gate on that would take it off the desk it actually belongs to,
        // which is worse than showing it at all of them.
        let floor = [Station(repo: "northwind/api"), Station(repo: "acme/docs")]
        let url = Gate(state: "pending", id: "q1", target: "curl https://api.example.com/v1/orders")
        XCTAssertEqual(StateRules.gateDesks(gate: url, stations: floor),
                       ["northwind/api", "acme/docs"])

        let spelled = Gate(state: "pending", id: "q2", target: "gh issue list -R northwind/api")
        XCTAssertEqual(StateRules.gateDesks(gate: spelled, stations: floor), ["northwind/api"],
                       "written out in full it is a name, however short")
    }

    func testAGateNamingNobodyFallsBackToTheDeskTheRuntimeIsAt() {
        let floor = StateRules.attachGate(stations: [Station(repo: "acme/checkout-api"),
                                                     Station(repo: "acme/docs")],
                                          runtime: RuntimeInfo(root: "/Users/you/code/checkout-api"),
                                          gate: pendingGate())
        XCTAssertEqual(StateRules.gateDesks(gate: pendingGate(), stations: floor),
                       ["acme/checkout-api"])
    }

    func testTheDeskTheAgentIsAtOutranksADeskTheCommandMerelyNames() {
        // A path argument is a string; the runtime root is where the agent is.
        // `cp ~/acme/docs/x ~/acme/website/` must not move the hand off
        // checkout-api onto two desks it never touched.
        let gate = Gate(state: "pending", id: "q9",
                        target: "cp ~/code/acme/docs/readme.md ~/code/acme/website/",
                        detail: "")
        let floor = StateRules.attachGate(stations: [Station(repo: "acme/checkout-api"),
                                                     Station(repo: "acme/docs"),
                                                     Station(repo: "acme/website")],
                                          runtime: RuntimeInfo(root: "/Users/you/code/checkout-api"),
                                          gate: gate)
        XCTAssertEqual(StateRules.gateDesks(gate: gate, stations: floor), ["acme/checkout-api"])
    }

    func testAGateNobodyCanPlaceIsShownAtEveryDeskRatherThanNone() {
        // No repo named, and no desk holding it yet: the world poll has not come
        // round since this question opened. It is never the answer to hide it.
        let floor = [Station(repo: "acme/checkout-api"), Station(repo: "acme/docs")]
        XCTAssertEqual(StateRules.gateDesks(gate: pendingGate(), stations: floor),
                       ["acme/checkout-api", "acme/docs"])
    }

    func testAClearGateIsShownNowhere() {
        let floor = [Station(repo: "acme/docs", gate: pendingGate())]
        XCTAssertTrue(StateRules.gateDesks(gate: .clear, stations: floor).isEmpty)
        XCTAssertTrue(StateRules.gateDesks(gate: nil, stations: floor).isEmpty)
    }

    func testTheDeskShownADifferentQuestionsGateIsNotTheStaleOne() {
        // The exact drift the verifier caught: the station still carries q-AAAA
        // from the world snapshot while the door has moved to q-BBBB.
        let stale = Gate(state: "pending", id: "q-AAAA", target: "rm -rf ~/Documents/Vaults")
        let live = Gate(state: "pending", id: "q-BBBB", target: "git push --force origin main")
        let floor = [Station(repo: "acme/checkout-api", gate: stale), Station(repo: "acme/docs")]
        XCTAssertEqual(StateRules.gateDesks(gate: live, stations: floor),
                       ["acme/checkout-api", "acme/docs"],
                       "the stale attachment must not claim the new question for itself")
    }

    // MARK: - clocks

    func testWaitedReadsAsTime() {
        XCTAssertEqual(StateRules.waited(seconds: 0), "0s")
        XCTAssertEqual(StateRules.waited(seconds: 47), "47s")
        XCTAssertEqual(StateRules.waited(seconds: 90), "1m 30s")
        XCTAssertEqual(StateRules.waited(seconds: 3700), "1h 1m")
        XCTAssertEqual(StateRules.waited(seconds: -5), "0s")
    }

    func testAnUnparseableStampIsBlankRatherThanWrong() {
        XCTAssertEqual(StateRules.stamp(""), "")
        XCTAssertEqual(StateRules.stamp("not a date"), "")
    }

    // MARK: - the wire

    func testATurnTakesItsTextFromEitherFieldName() throws {
        let content = try JSONDecoder().decode(
            ChatTurn.self, from: Data(#"{"role":"user","content":"one"}"#.utf8))
        XCTAssertEqual(content.content, "one")
        XCTAssertTrue(content.isUser)

        let text = try JSONDecoder().decode(
            ChatTurn.self, from: Data(#"{"role":"assistant","text":"two"}"#.utf8))
        XCTAssertEqual(text.content, "two")
        XCTAssertFalse(text.isUser)
    }

    func testAStationSurvivesFieldsItHasNeverSeen() throws {
        let json = #"""
        {"repo":"a/b","access":true,"outcome":"landed","detail":"d","at":"",
         "issues":[{"number":1,"title":"t","bot_last":true}],
         "issues_error":null,"prs":[],"newfield":{"nested":true}}
        """#
        let station = try JSONDecoder().decode(Station.self, from: Data(json.utf8))
        XCTAssertEqual(station.repo, "a/b")
        XCTAssertEqual(station.issues.first?.botLast, true)
        XCTAssertEqual(state(station), .waiting)
    }

    func testAGateCarriesTheBotThatRaisedItWhenTheDoorKnows() throws {
        let json = #"""
        {"state":"pending","id":"q-AAAA","permission":"run_bash","target":"git push",
         "detail":"cutting 0.4.2","waiting_s":47,"bot":"release"}
        """#
        let named = try JSONDecoder().decode(Gate.self, from: Data(json.utf8))
        XCTAssertEqual(named.bot, "release")
        XCTAssertTrue(named.isPending)

        // Absent means the shared session, which is a thread this app can still
        // draw. It is never read as a bot called "".
        let shared = try JSONDecoder().decode(
            Gate.self, from: Data(#"{"state":"pending","id":"q-B","target":"ls"}"#.utf8))
        XCTAssertNil(shared.bot)
    }

    func testOnlyMergeableNonDraftPullRequestsOfferAMerge() {
        XCTAssertTrue(PullRequest(number: 1, title: "t", mergeable: "MERGEABLE").canMerge)
        XCTAssertFalse(PullRequest(number: 2, title: "t", draft: true, mergeable: "MERGEABLE").canMerge)
        XCTAssertFalse(PullRequest(number: 3, title: "t", mergeable: "CONFLICTING").canMerge)
        XCTAssertFalse(PullRequest(number: 4, title: "t", mergeable: "UNKNOWN").canMerge,
                       "UNKNOWN is ask again in a moment, never permission")
    }

    // MARK: - the most recent thing we were able to pull

    func testADeskThatPulledCleanlySaysNothingAboutFreshness() {
        let station = Station(repo: "a/fresh", at: built, fetchedAt: built)
        XCTAssertFalse(StateRules.isStale(station: station, generated: built))
        XCTAssertNil(StateRules.asOf(station: station, generated: built, now: now))
        XCTAssertNil(StateRules.staleNotice(station: station, github: nil, now: now))
        XCTAssertNil(StateRules.staleNotice(station: station, github: GitHubBudget(remaining: 4200),
                                            now: now),
                     "a budget with room left is not news")
    }

    func testAPauseSaysNothingOnADeskThatIsCurrent() {
        // The floor is paused, but this desk answered this build and this one
        // was put away before anyone asked. Neither is behind, so neither talks.
        let budget = GitHubBudget(limit: 5000, remaining: 0, pausedUntil: shift(8 * 60),
                                  error: "secondary rate limit")
        let fresh = Station(repo: "a/fresh", fetchedAt: shift(-45))
        XCTAssertNil(StateRules.staleNotice(station: fresh, github: budget,
                                            generated: built, now: now))
        let away = Station(repo: "a/away", fetchedAt: "", hidden: true)
        XCTAssertNil(StateRules.staleNotice(station: away, github: budget,
                                            generated: built, now: now))
        let old = Station(repo: "a/old", fetchedAt: shift(-3 * 3600))
        XCTAssertNotNil(StateRules.staleNotice(station: old, github: budget,
                                               generated: built, now: now))
    }

    func testTheGapEveryHealthyPullHasIsNotStaleness() {
        // `fetched_at` is written when GitHub answers and `generated` when the
        // snapshot finishes, so they are never equal. A warning that is always
        // on is a warning nobody reads.
        let station = Station(repo: "a/normal", fetchedAt: shift(-45))
        XCTAssertFalse(StateRules.isStale(station: station, generated: built))
        XCTAssertNil(StateRules.asOf(station: station, generated: built, now: now))
    }

    func testAnOldPullIsSaidOutLoudInTheHeader() {
        let station = Station(repo: "a/old", fetchedAt: shift(-3 * 3600))
        XCTAssertTrue(StateRules.isStale(station: station, generated: built))
        let asOf = StateRules.asOf(station: station, generated: built, now: now)
        XCTAssertEqual(asOf, "as of " + StateRules.moment(station.fetchedAt, now: now))
        XCTAssertTrue(asOf?.hasPrefix("as of ") == true)
    }

    func testAFetchedAtNobodyCanReadIsNotReportedAsCurrent() {
        // "we do not know how old this is" must not render as "this is current",
        // and it must not render as a wrong time either. It renders as nothing.
        for unreadable in ["", "not a date", "yesterday-ish"] {
            let station = Station(repo: "a/unknown", fetchedAt: unreadable)
            XCTAssertFalse(StateRules.isStale(station: station, generated: built))
            XCTAssertNil(StateRules.asOf(station: station, generated: built, now: now))
        }
    }

    func testAnErrorMakesTheHeaderSayAsOfEvenWhenTheClocksAgree() {
        // The pull failed, so what is on screen is the previous answer whatever
        // the two timestamps happen to say.
        let station = Station(repo: "a/failed", fetchedAt: built,
                              issuesError: "GitHub answered 403")
        XCTAssertFalse(StateRules.isStale(station: station, generated: built))
        XCTAssertNotNil(StateRules.asOf(station: station, generated: built, now: now))
    }

    // MARK: - one notice, never two

    func testTheSameFailureTwiceIsOneSentence() {
        // The exact defect: both halves of a pull fail together and report the
        // same line, and two identical red rows read as two faults.
        let said = "GitHub answered 403: the hourly budget for this token is spent"
        let station = Station(repo: "a/stale", fetchedAt: shift(-3 * 3600),
                              issuesError: said, prsError: said)
        XCTAssertEqual(station.problems, [said], "said once, not twice")

        let notice = try? XCTUnwrap(StateRules.staleNotice(station: station,
                                                           github: nil, now: now))
        let text = notice ?? ""
        XCTAssertEqual(occurrences(of: said, in: text), 1)
        XCTAssertTrue(text.hasPrefix(said))
        XCTAssertEqual(text, said + "; showing what we had at "
                       + StateRules.moment(station.fetchedAt, now: now))
    }

    func testTwoDifferentFailuresAreBothKept() {
        let station = Station(repo: "a/two", fetchedAt: shift(-3 * 3600),
                              issuesError: "issues: 403", prsError: "pulls: 502")
        XCTAssertEqual(station.problems, ["issues: 403", "pulls: 502"])
        let text = StateRules.staleNotice(station: station, github: nil, now: now) ?? ""
        XCTAssertTrue(text.hasPrefix("issues: 403; pulls: 502; showing what we had at "))
    }

    func testABlankErrorIsNotAProblem() {
        let station = Station(repo: "a/blank", fetchedAt: built,
                              issuesError: "", prsError: "   ")
        XCTAssertTrue(station.problems.isEmpty)
        XCTAssertNil(StateRules.staleNotice(station: station, github: nil, now: now))
    }

    func testAnErrorWithNoSuccessfulPullEverSaysOnlyWhatWentWrong() {
        // Nothing was ever pulled here, so there is no "what we had".
        let station = Station(repo: "a/never", fetchedAt: "",
                              issuesError: "no account holds push here")
        XCTAssertEqual(StateRules.staleNotice(station: station, github: nil, now: now),
                       "no account holds push here")
    }

    func testASpentBudgetOutranksTheDesksOwnError() {
        // While the door has stopped asking, no repo's data is current and no
        // repo's error is news. One sentence explains the whole floor.
        let budget = GitHubBudget(limit: 5000, remaining: 0,
                                  pausedUntil: shift(8 * 60),
                                  error: "secondary rate limit")
        let station = Station(repo: "a/stale", fetchedAt: shift(-3 * 3600),
                              issuesError: "GitHub answered 403", prsError: "GitHub answered 403")
        let text = StateRules.staleNotice(station: station, github: budget, now: now) ?? ""
        XCTAssertEqual(text,
                       "GitHub is out of budget until "
                       + StateRules.moment(budget.pausedUntil, now: now)
                       + "; showing what we had at "
                       + StateRules.moment(station.fetchedAt, now: now))
        XCTAssertEqual(occurrences(of: "showing what we had at", in: text), 1)
        XCTAssertFalse(text.contains("403"))
    }

    func testASpentBudgetWithNoReadableClockStillSaysWhy() {
        let budget = GitHubBudget(pausedUntil: "soon")
        // A desk that is behind (it has an error) under a pause with an unreadable
        // clock still gets the reason, just without the hour.
        XCTAssertEqual(StateRules.staleNotice(station: Station(repo: "a/x", fetchedAt: "",
                                                               issuesError: "GitHub answered 403"),
                                              github: budget, now: now),
                       "GitHub is out of budget")
    }

    func testAMomentSaysWhichDayWhenItIsNotToday() {
        XCTAssertEqual(StateRules.moment(""), "")
        XCTAssertEqual(StateRules.moment("not a date"), "")
        XCTAssertFalse(StateRules.moment(built, now: now).isEmpty)

        // "showing what we had at Yesterday" is not English. Two days back is
        // far enough that no time zone can call it today or yesterday.
        let old = StateRules.moment(shift(-48 * 3600), now: now)
        XCTAssertTrue(old.contains(" on "), "a stamp from another day places the day: \(old)")
    }

    // MARK: - what a bot is for

    func testABotRowSaysWhatItIsForUntilItHasSaidAnything() {
        let quiet = Bot(id: "research", name: "Research",
                        purpose: "Looks before anyone builds: prior work, failure modes.")
        XCTAssertEqual(StateRules.botSubtitle(bot: quiet), quiet.purpose)

        let spoken = Bot(id: "research", name: "Research", purpose: quiet.purpose,
                         last: ChatTurn(role: "assistant", content: "Failure map is written."))
        XCTAssertEqual(StateRules.botSubtitle(bot: spoken), "Failure map is written.")
    }

    func testASilentBotWithNoPurposeSaysNothingRatherThanSomethingUseless() {
        XCTAssertEqual(StateRules.botSubtitle(bot: Bot(id: "x", name: "X")), "")
    }

    func testALongPurposeIsCutLikeAnyOtherRosterLine() {
        let bot = Bot(id: "x", name: "X", purpose: String(repeating: "p", count: 200))
        XCTAssertEqual(StateRules.botSubtitle(bot: bot, limit: 20).count, 21)
    }

    // MARK: - put away, and brought back

    func testAPutAwayDeskLeavesTheListAndTurnsUpInTheOtherOne() {
        let floor = [Station(repo: "acme/docs"),
                     Station(repo: "acme/legacy-import", hidden: true),
                     Station(repo: "northwind/api", issues: [quietIssue()])]

        XCTAssertEqual(StateRules.visibleDesks(floor).map(\.repo), ["acme/docs", "northwind/api"])
        XCTAssertEqual(StateRules.putAwayDesks(floor).map(\.repo), ["acme/legacy-import"])
        XCTAssertEqual(StateRules.polledLine(floor), "2 of 3 polled")
    }

    func testPuttingSomethingAwayCanNeverHideARaisedHand() {
        // The rule the whole surface is built on, now with a third filter to
        // survive. A desk a person put away that is standing there with its
        // hand up is back in the list, and is not in the put-away section.
        let gated = Station(repo: "acme/legacy-import", hidden: true, gate: pendingGate())
        let floor = [Station(repo: "acme/docs"), gated]

        XCTAssertTrue(StateRules.visibleDesks(floor).contains { $0.repo == gated.repo })
        XCTAssertTrue(StateRules.visibleDesks(floor, query: "zzzz", needsOnly: true)
            .contains { $0.repo == gated.repo })
        XCTAssertTrue(StateRules.putAwayDesks(floor).isEmpty,
                      "it is up in the list, so it must not also be down in the drawer")
    }

    func testHiddenIsNeverSilent() {
        // Something put away has started needing a person. The closed header
        // says so, so nobody has to open it to find out.
        let quiet = [Station(repo: "a/one", hidden: true), Station(repo: "a/two", hidden: true)]
        XCTAssertEqual(StateRules.putAwayHeadline(quiet), "put away (2)")
        XCTAssertEqual(StateRules.putAwayNeedingAPerson(quiet), 0)

        let waiting = quiet + [Station(repo: "a/three", hidden: true, issues: [waitingIssue()])]
        XCTAssertEqual(StateRules.putAwayNeedingAPerson(waiting), 1)
        XCTAssertEqual(StateRules.putAwayHeadline(waiting), "put away (3) \u{00b7} 1 needs you")
    }

    func testTheSearchBoxNeverReachesTheDrawer() {
        // A put-away desk is out of the way, not out of the building: the
        // section always holds every one of them so they stay findable.
        let floor = [Station(repo: "acme/docs"),
                     Station(repo: "acme/legacy-import", hidden: true),
                     Station(repo: "northwind/analytics", hidden: true)]
        XCTAssertEqual(StateRules.putAwayDesks(floor).count, 2)
        XCTAssertEqual(StateRules.visibleDesks(floor, query: "legacy").count, 0,
                       "the desks list is what the search filters")
    }

    func testTheInventedGateDeskIsNotCountedAsSomethingWePoll() {
        let floor = [Station(repo: "acme/docs"),
                     Station(repo: "runtime/agent", gate: pendingGate(), synthetic: true)]
        XCTAssertEqual(StateRules.polledLine(floor), "1 of 1 polled")
    }

    func testAnOverrideBeatsWhatTheSnapshotSaysUntilItCatchesUp() {
        // The optimistic flip: the person said put this away, the world poll has
        // not come round, and the row has to have moved already.
        let floor = [Station(repo: "acme/docs"), Station(repo: "acme/website")]
        let justClicked: (Station) -> Bool = { $0.repo == "acme/website" }
        XCTAssertEqual(StateRules.visibleDesks(floor, isHidden: justClicked).map(\.repo),
                       ["acme/docs"])
        XCTAssertEqual(StateRules.putAwayDesks(floor, isHidden: justClicked).map(\.repo),
                       ["acme/website"])
        XCTAssertEqual(StateRules.polledLine(floor, isHidden: justClicked), "1 of 2 polled")
    }

    // MARK: - the wire, again

    func testAStationReadsTheFreshnessAndPutAwayFields() throws {
        let json = #"""
        {"repo":"a/b","access":true,"at":"2026-08-26T18:33:00Z",
         "fetched_at":"2026-08-26T15:10:00Z","hidden":true,
         "issues":[],"prs":[],"issues_error":"403","prs_error":"403"}
        """#
        let station = try JSONDecoder().decode(Station.self, from: Data(json.utf8))
        XCTAssertEqual(station.fetchedAt, "2026-08-26T15:10:00Z")
        XCTAssertTrue(station.hidden)
        XCTAssertEqual(station.problems, ["403"])
        XCTAssertTrue(StateRules.isStale(station: station, generated: "2026-08-26T18:40:00Z"))
    }

    func testAServerThatPredatesTheseFieldsIsNotAFloorOfHiddenStaleDesks() throws {
        // The python door is upgraded on its own schedule. Absent must read as
        // "on the floor, freshness unknown", never as "put away".
        let json = #"{"repo":"a/b","access":true,"at":"2026-08-26T18:33:00Z","issues":[],"prs":[]}"#
        let station = try JSONDecoder().decode(Station.self, from: Data(json.utf8))
        XCTAssertFalse(station.hidden)
        XCTAssertEqual(station.fetchedAt, "")
        XCTAssertFalse(StateRules.isStale(station: station, generated: "2026-08-26T18:40:00Z"))
        XCTAssertNil(StateRules.staleNotice(station: station, github: nil, now: now))
    }

    func testABotCarriesItsPurposeAndSurvivesNotHavingOne() throws {
        let named = try JSONDecoder().decode(
            Bot.self,
            from: Data(#"{"id":"chief","name":"Chief","purpose":"What is running."}"#.utf8))
        XCTAssertEqual(named.purpose, "What is running.")

        let bare = try JSONDecoder().decode(
            Bot.self, from: Data(#"{"id":"chief","name":"Chief"}"#.utf8))
        XCTAssertEqual(bare.purpose, "")
    }

    func testTheBudgetReadsAnUnknownCountAsUnknownRatherThanZero() throws {
        // `null` is the door declining to say. Reading it as zero would put
        // "out of budget" on a floor that is perfectly healthy.
        let json = #"""
        {"limit":null,"remaining":null,"reset_at":"","cost":12,"paused_until":"","error":""}
        """#
        let budget = try JSONDecoder().decode(GitHubBudget.self, from: Data(json.utf8))
        XCTAssertNil(budget.limit)
        XCTAssertNil(budget.remaining)
        XCTAssertEqual(budget.cost, 12)
        XCTAssertFalse(budget.isPaused)
    }

    func testTheWorldCarriesTheBudgetAndSurvivesNotHavingOne() throws {
        let with = try JSONDecoder().decode(
            World.self,
            from: Data(#"{"generated":"g","stations":[],"github":{"paused_until":"2026-01-01T00:00:00Z"}}"#.utf8))
        XCTAssertEqual(with.github?.isPaused, true)

        let without = try JSONDecoder().decode(
            World.self, from: Data(#"{"generated":"g","stations":[]}"#.utf8))
        XCTAssertNil(without.github)
    }

    func testTheDesksListIsAListAndNotADelta() throws {
        let answer = try JSONDecoder().decode(
            DesksResponse.self,
            from: Data(#"{"ok":true,"hidden":["a/b","c/d"]}"#.utf8))
        XCTAssertEqual(answer.hidden, ["a/b", "c/d"])

        let empty = try JSONDecoder().decode(DesksResponse.self, from: Data(#"{"ok":true}"#.utf8))
        XCTAssertTrue(empty.hidden.isEmpty)
    }

    // MARK: - the wall

    /// A source that ships before its card does still gets a name and a
    /// sentence. This is the exact state the live door is in while the python
    /// side is still being written, and a wall of blank rows is a wall a person
    /// stops believing.
    func testASectionWithNoCardStillHasSomethingToSay() throws {
        let world = try JSONDecoder().decode(World.self, from: Data(#"""
        {"generated":"g","stations":[],
         "sections":{"leads":{"state":"missing","detail":"no gig desk at /Users/x/leads"},
                     "cost":{"state":"ok","by_family":{"opus":3},"rows":91}}}
        """#.utf8))

        let leads = try XCTUnwrap(world.sections.first { $0.id == "leads" })
        XCTAssertEqual(leads.title, "Leads")
        XCTAssertEqual(leads.headline, "no gig desk at /Users/x/leads",
                       "the detail is the sentence when the card did not write one")
        XCTAssertEqual(leads.needs, 0)
        XCTAssertTrue(leads.card.facts.isEmpty)

        let cost = try XCTUnwrap(world.sections.first { $0.id == "cost" })
        XCTAssertEqual(cost.title, "Cost")
        XCTAssertEqual(cost.headline, "ok",
                       "with no detail either, the state is the only honest sentence")
    }

    /// The bag of source-specific keys is ignored, and no shape of it is fatal.
    func testNothingInASectionCanTakeTheWallDown() throws {
        let world = try JSONDecoder().decode(World.self, from: Data(#"""
        {"generated":"g","stations":[],
         "sections":{"odd":{"state":42,"detail":["a","b"],
                            "card":{"title":"Odd","headline":"fine","needs":"lots",
                                    "as_of":null,
                                    "facts":[{"label":"count","value":91,"tone":"nonsense"},
                                             {"label":"ratio","value":0.5},
                                             {"label":"on","value":true,"tone":"ok"}]},
                            "whatever":{"deeply":{"nested":[1,2,3]}}}}}
        """#.utf8))

        let odd = try XCTUnwrap(world.sections.first)
        XCTAssertEqual(odd.state, "ok", "a state that is not a string is no state at all")
        XCTAssertEqual(odd.needs, 0, "a count that is not a number does not become one")
        XCTAssertEqual(odd.card.asOf, "")
        XCTAssertEqual(odd.card.facts.map(\.value), ["91", "0.5", "yes"],
                       "a value written as a number is still a value")
        XCTAssertEqual(StateRules.tone(odd.card.facts[0]), .plain,
                       "a tone nobody defined paints nothing")
        XCTAssertEqual(StateRules.tone(odd.card.facts[2]), .ok)
    }

    func testANegativeCountIsNotABadge() throws {
        let world = try JSONDecoder().decode(World.self, from: Data(#"""
        {"generated":"g","stations":[],
         "sections":{"x":{"state":"ok","card":{"title":"X","headline":"h","needs":-3}}}}
        """#.utf8))
        XCTAssertEqual(world.sections.first?.needs, 0)
        XCTAssertNil(StateRules.sectionBadge(try XCTUnwrap(world.sections.first)))
    }

    /// A JSON object has no order, so the wall has to make one. Without this
    /// the roster reshuffles its own rows under a person every ten seconds.
    func testTheWallArrivesInAStableOrder() throws {
        let json = #"""
        {"generated":"g","stations":[],
         "sections":{"pipeline":{"state":"ok","card":{"title":"Pipeline","headline":"h"}},
                     "clock":{"state":"ok","card":{"title":"Clock","headline":"h"}},
                     "mail":{"state":"ok","card":{"title":"Mail","headline":"h"}},
                     "cost":{"state":"ok","card":{"title":"Cost","headline":"h"}}}}
        """#
        for _ in 0..<12 {
            let world = try JSONDecoder().decode(World.self, from: Data(json.utf8))
            XCTAssertEqual(world.sections.map(\.title), ["Clock", "Cost", "Mail", "Pipeline"])
        }
    }

    /// The same ordering a desk gets, for the same reason: a wall sorted
    /// alphabetically buries the one source that needed you behind four that
    /// did not.
    func testWhatWantsAPersonComesFirstThenWhatIsBroken() {
        let ordered = StateRules.sectionOrder([
            section("cost", state: "ok"),
            section("pipeline", state: "unconfigured"),
            section("clock", state: "ok", needs: 5),
            section("library", state: "stale"),
            section("mail", state: "error", needs: 2)
        ])

        XCTAssertEqual(ordered.map(\.title),
                       ["Clock", "Mail", "Library", "Pipeline", "Cost"])
        XCTAssertEqual(StateRules.mood(ordered[0]), .needs)
        XCTAssertEqual(StateRules.mood(ordered[1]), .needs,
                       "broken AND wanted is still wanted: the reason waits until you are there")
        XCTAssertEqual(StateRules.mood(ordered[2]), .off)
        XCTAssertEqual(StateRules.mood(ordered[4]), .quiet)
    }

    func testTiesGoByTitleAndNeverWobble() {
        let a = section("bbb", state: "ok", needs: 1)
        let b = section("aaa", state: "ok", needs: 1)
        XCTAssertEqual(StateRules.sectionOrder([a, b]).map(\.id), ["aaa", "bbb"])
        XCTAssertEqual(StateRules.sectionOrder([b, a]).map(\.id), ["aaa", "bbb"])
    }

    func testPodcastsStayAtTheTopOfTheWall() {
        let ordered = StateRules.sectionOrder([
            section("clock", state: "ok", needs: 5),
            section("podcasts", state: "ok"),
            section("mail", state: "error", needs: 2)
        ])
        XCTAssertEqual(ordered.map(\.id), ["podcasts", "clock", "mail"])
    }

    func testTheWallTotalAndItsLine() {
        let wall = [section("clock", state: "ok", needs: 5),
                    section("mail", state: "error", needs: 2),
                    section("cost", state: "ok")]
        XCTAssertEqual(StateRules.wallNeeds(wall), 7)
        XCTAssertEqual(StateRules.wallLine(wall), "the wall needs 7")

        let quiet = [section("cost", state: "ok"), section("mail", state: "stale")]
        XCTAssertEqual(StateRules.wallNeeds(quiet), 0)
        XCTAssertEqual(StateRules.wallLine(quiet), "",
                       "nothing at all rather than a nought: a zero is a roster shouting")
    }

    func testTheFilterAndTheSearchReadTheCardAndNothingElse() {
        let wall = [section("clock", state: "ok", needs: 5,
                            headline: "5 jobs need a look"),
                    section("cost", state: "ok", headline: "$18.42 today"),
                    section("mail", state: "error", headline: "the host refused the token")]

        XCTAssertEqual(StateRules.visibleSections(wall, needsOnly: true).map(\.id), ["clock"])
        XCTAssertEqual(StateRules.visibleSections(wall, query: "cost").map(\.id), ["cost"],
                       "the title is searchable")
        XCTAssertEqual(StateRules.visibleSections(wall, query: "token").map(\.id), ["mail"],
                       "so is the sentence under it")
        XCTAssertEqual(StateRules.visibleSections(wall, query: "  ").map(\.id).count, 3,
                       "whitespace is not a search")
        XCTAssertTrue(StateRules.visibleSections(wall, query: "zzz").isEmpty)
    }

    func testTheSubtitleIsOneLineNoMatterWhatTheSourceWrote() {
        let noisy = section("x", state: "ok",
                            headline: "line one\n\nline two   with   gaps\tand a tab")
        let said = StateRules.sectionSubtitle(noisy)
        XCTAssertFalse(said.contains("\n"))
        XCTAssertEqual(said, "line one line two with gaps and a tab")

        let long = section("y", state: "ok", headline: String(repeating: "word ", count: 60))
        XCTAssertLessThanOrEqual(StateRules.sectionSubtitle(long, limit: 40).count, 41)
    }

    func testTheBadgeIsTheCountOrNothing() {
        XCTAssertEqual(StateRules.sectionBadge(section("a", state: "ok", needs: 5)), "5")
        XCTAssertNil(StateRules.sectionBadge(section("b", state: "error")),
                     "broken is not a count, and a badge saying nothing is worse than none")
    }

    // MARK: - helpers

    private func section(_ id: String, state: String, needs: Int = 0,
                         headline: String = "h", facts: [SectionFact] = []) -> Section {
        Section(id: id, state: state,
                card: SectionCard(title: id.capitalized, headline: headline,
                                  needs: needs, facts: facts))
    }

    /// The snapshot everything in the freshness tests is measured against, and
    /// a fixed "now" so no assertion here can pass at one time of day and fail
    /// at another.
    private let built = "2026-08-26T18:40:00Z"
    private var now: Date { StateRules.date(built)! }

    private func shift(_ seconds: TimeInterval) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        f.timeZone = TimeZone(identifier: "UTC")
        return f.string(from: now.addingTimeInterval(seconds))
    }

    private func occurrences(of needle: String, in haystack: String) -> Int {
        var count = 0
        var rest = haystack[...]
        while let found = rest.range(of: needle) {
            count += 1
            rest = rest[found.upperBound...]
        }
        return count
    }

    private func state(_ station: Station) -> DeskState { StateRules.deskState(station) }

    private func waitingIssue(_ number: Int = 1) -> Issue {
        Issue(number: number, title: "needs a decision", botLast: true)
    }

    private func quietIssue(_ number: Int = 2) -> Issue {
        Issue(number: number, title: "in flight", botLast: false)
    }

    private func openPR(_ number: Int) -> PullRequest {
        PullRequest(number: number, title: "open", mergeable: "MERGEABLE")
    }

    private func pendingGate() -> Gate {
        Gate(state: "pending", id: "a1b2c3d4e5f60718", permission: "run_bash",
             target: "git push origin main --follow-tags", waitingS: 47, bot: "release")
    }

    /// A second hand, up while the first is still waiting, and one that says a
    /// desk's name out loud so it has somewhere of its own to stand.
    private func secondGate() -> Gate {
        Gate(state: "pending", id: "f7e6d5c4b3a29180", permission: "run_bash",
             target: "rm -rf ~/code/acme/storefront/node_modules", waitingS: 12, bot: "chief")
    }
    // MARK: - a drag that visibly reorders

    /// The gesture everybody tries first: take the top pin and drop it on the
    /// one below. Inserting before the target made that a no-op, so the drag
    /// looked broken while working perfectly.
    func testDraggingDownLandsAfterTheRowItWasDroppedOn() {
        let pins = ["a", "b", "c"]
        XCTAssertEqual(StateRules.moved(pins: pins, repo: "a", before: "b"), ["b", "a", "c"])
    }

    func testDraggingUpStillLandsBeforeTheRowItWasDroppedOn() {
        let pins = ["a", "b", "c"]
        XCTAssertEqual(StateRules.moved(pins: pins, repo: "c", before: "b"), ["a", "c", "b"])
    }

    func testDraggingPastTheLastPinGoesToTheEnd() {
        let pins = ["a", "b", "c"]
        XCTAssertEqual(StateRules.moved(pins: pins, repo: "a", before: nil), ["b", "c", "a"])
    }

    func testADeskThatWasNotPinnedBecomesPinnedWhereItLands() {
        let pins = ["a", "b"]
        XCTAssertEqual(StateRules.moved(pins: pins, repo: "z", before: "b"), ["a", "z", "b"])
    }

    func testDroppingOnItselfChangesNothing() {
        let pins = ["a", "b", "c"]
        XCTAssertEqual(StateRules.moved(pins: pins, repo: "b", before: "b"), pins)
    }

}

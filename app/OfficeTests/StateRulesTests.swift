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

    // MARK: - helpers

    private func state(_ station: Station) -> DeskState { StateRules.deskState(station) }

    private func waitingIssue(_ number: Int = 1) -> Issue {
        Issue(number: number, title: "needs a decision", botLast: true)
    }

    private func quietIssue(_ number: Int = 2) -> Issue {
        Issue(number: number, title: "in flight", botLast: false)
    }

    private func pendingGate() -> Gate {
        Gate(state: "pending", id: "a1b2c3d4e5f60718", permission: "run_bash",
             target: "git push origin main --follow-tags", waitingS: 47, bot: "release")
    }
}

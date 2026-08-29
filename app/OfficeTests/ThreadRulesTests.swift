import XCTest

/// The two decisions a thread makes that have nothing to do with a view:
/// what a one-line roster row says a bot said, and whether a reply arriving is
/// allowed to move the screen.
final class ThreadRulesTests: XCTestCase {

    // MARK: - markdown, taken off

    func testEmphasisComesOff() {
        XCTAssertEqual(StateRules.plain("**bold**"), "bold")
        XCTAssertEqual(StateRules.plain("__bold__"), "bold")
        XCTAssertEqual(StateRules.plain("*italic*"), "italic")
        XCTAssertEqual(StateRules.plain("_italic_"), "italic")
        XCTAssertEqual(StateRules.plain("~~gone~~"), "gone")
        XCTAssertEqual(StateRules.plain("a **bold** word"), "a bold word")
    }

    func testCodeSpansComeOff() {
        XCTAssertEqual(StateRules.plain("`code`"), "code")
        XCTAssertEqual(StateRules.plain("it drops `host_name` on empty"),
                       "it drops host_name on empty")
    }

    func testHeadingsLoseTheirHashes() {
        XCTAssertEqual(StateRules.plain("# head"), "head")
        XCTAssertEqual(StateRules.plain("### deeper head"), "deeper head")
        // Not a heading: no space after the hash, so it is somebody writing
        // about issue #58 and the number has to survive.
        XCTAssertEqual(StateRules.plain("#58 is the block"), "#58 is the block")
    }

    func testListMarkersGoAndTheItemsStay() {
        XCTAssertEqual(StateRules.plain("- item"), "item")
        XCTAssertEqual(StateRules.plain("* item"), "item")
        XCTAssertEqual(StateRules.plain("+ item"), "item")
        XCTAssertEqual(StateRules.plain("1. item"), "item")
        XCTAssertEqual(StateRules.plain("2) item"), "item")
        XCTAssertEqual(StateRules.plain("- one\n- two"), "one two")
    }

    func testLinksKeepTheirWordsAndLoseTheirBrackets() {
        XCTAssertEqual(StateRules.plain("see [the runbook](https://example.com/r)"),
                       "see the runbook")
        XCTAssertEqual(StateRules.plain("![a shot](https://example.com/s.png)"), "a shot")
    }

    func testQuotesAndRulesGo() {
        XCTAssertEqual(StateRules.plain("> quoted"), "quoted")
        XCTAssertEqual(StateRules.plain("one\n---\ntwo"), "one two")
    }

    /// Inside a fence nothing is syntax. Eating the underscores out of a
    /// variable name would be this helper inventing a different identifier.
    func testAFencedBlockKeepsItsCharacters() {
        let said = """
        here:

        ```swift
        let host_name = order.host
        ```
        """
        XCTAssertEqual(StateRules.plain(said), "here:  let host_name = order.host")
    }

    func testPlainTextIsLeftAlone() {
        XCTAssertEqual(StateRules.plain("Nothing in code."), "Nothing in code.")
        XCTAssertEqual(StateRules.plain(""), "")
        XCTAssertEqual(StateRules.plain("2 * 3 * 4"), "2 * 3 * 4")
    }

    // MARK: - what the roster row shows

    func testTheRosterRowShowsTheWordsAndNoneOfTheSyntax() {
        let bot = Bot(id: "chief", name: "Chief",
                      last: ChatTurn(role: "assistant",
                                     content: "- `host_name` is dropped\n- **billing** is next"))
        let line = StateRules.lastLine(bot: bot)
        XCTAssertEqual(line, "host_name is dropped billing is next")
        XCTAssertFalse(line.contains("`"))
        XCTAssertFalse(line.contains("*"))
        XCTAssertFalse(line.contains("-"))
    }

    func testTheRosterRowStillTruncates() {
        let long = "# " + String(repeating: "word ", count: 40)
        let line = StateRules.lastLine(bot: Bot(id: "b", name: "B",
                                                last: ChatTurn(role: "assistant", content: long)))
        XCTAssertTrue(line.hasSuffix("\u{2026}"))
        XCTAssertFalse(line.hasPrefix("#"))
    }

    func testABotThatHasSaidNothingStillFallsBackToItsPurpose() {
        let bot = Bot(id: "b", name: "B", purpose: "watches the pipeline")
        XCTAssertEqual(StateRules.botSubtitle(bot: bot), "watches the pipeline")
    }

    func testBotFrequencyIsVisibleButBackwardCompatible() throws {
        let data = Data(#"{"id":"north","name":"North","frequency":"08:30 · 13:00 · 20:30"}"#.utf8)
        let bot = try JSONDecoder().decode(Bot.self, from: data)
        XCTAssertEqual(bot.frequency, "08:30 · 13:00 · 20:30")

        let old = try JSONDecoder().decode(
            Bot.self, from: Data(#"{"id":"legacy","name":"Legacy"}"#.utf8))
        XCTAssertEqual(old.frequency, "")
    }

    // MARK: - whether a reply moves the screen

    func testAThreadOnTheBottomFollowsTheNewestTurn() {
        XCTAssertTrue(StateRules.shouldFollow(distanceFromBottom: 0))
        XCTAssertTrue(StateRules.shouldFollow(distanceFromBottom: 12))
        // A scroll view bounces past its own end; that is still the bottom.
        XCTAssertTrue(StateRules.shouldFollow(distanceFromBottom: -30))
    }

    func testAThreadScrolledUpKeepsItsPlace() {
        XCTAssertFalse(StateRules.shouldFollow(distanceFromBottom: 400))
        XCTAssertFalse(StateRules.shouldFollow(distanceFromBottom: 49))
    }

    /// The boundary itself, written down, because "close enough to the bottom"
    /// is exactly the kind of number that drifts by one on a refactor and turns
    /// into either a screen that jumps or a pill that never goes away.
    func testTheSlackIsTheBoundaryAndNotAVibe() {
        XCTAssertEqual(StateRules.bottomSlack, 48)
        XCTAssertTrue(StateRules.shouldFollow(distanceFromBottom: StateRules.bottomSlack))
        XCTAssertFalse(StateRules.shouldFollow(distanceFromBottom: StateRules.bottomSlack + 0.5))
        XCTAssertTrue(StateRules.shouldFollow(distanceFromBottom: 90, slack: 100))
    }

    func testThePillSaysHowManyAndSaysNothingWhenNothingArrived() {
        XCTAssertNil(StateRules.newRepliesLine(0))
        XCTAssertNil(StateRules.newRepliesLine(-1))
        XCTAssertEqual(StateRules.newRepliesLine(1), "new reply below")
        XCTAssertEqual(StateRules.newRepliesLine(3), "3 new replies below")
    }
}

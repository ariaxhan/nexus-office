import XCTest

final class AutomationTests: XCTestCase {
    func testRecentRunsUnifiesFamiliesNewestFirst() {
        var older = RunBoard.Run()
        older.id = "older"
        older.startedAt = "2026-09-04T18:00:00Z"
        older.endedAt = "2026-09-04T18:05:00Z"

        var running = RunBoard.Run()
        running.id = "running"
        running.startedAt = "2026-09-04T19:00:00Z"

        var newest = RunBoard.Run()
        newest.id = "newest"
        newest.startedAt = "2026-09-04T18:30:00Z"
        newest.endedAt = "2026-09-04T19:10:00Z"

        var first = RunBoard.Family()
        first.id = "first"
        first.runs = [older, newest]
        var second = RunBoard.Family()
        second.id = "second"
        second.runs = [running]

        var board = RunBoard()
        board.families = [first, second]

        XCTAssertEqual(board.recentRuns.map(\.id), ["newest", "running", "older"])
    }
}

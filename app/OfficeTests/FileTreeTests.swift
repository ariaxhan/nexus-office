import XCTest


final class FileTreeTests: XCTestCase {
    private func file(_ path: String) -> ContextFile {
        ContextFile(path: path, name: String(path.split(separator: "/").last ?? ""),
                    group: "", bytes: 1)
    }

    private func aged(_ path: String, _ mtime: Int) -> ContextFile {
        ContextFile(path: path, name: String(path.split(separator: "/").last ?? ""),
                    group: "", bytes: 1, mtime: mtime)
    }

    /// A desk of 20 documents, one written today, the rest months old.
    private func desk(now: Int) -> [ContextFile] {
        var files = (0..<20).map { aged("_meta/old/\($0).md", now - 90 * 86_400) }
        files.append(aged("_meta/plans/today.md", now - 3_600))
        files.append(aged("_meta/plans/yesterday.md", now - 86_400))
        return files
    }

    func test_recent_is_newest_first_and_small() {
        let now = 1_780_000_000
        let picked = FileTree.recent(of: desk(now: now), now: now)
        XCTAssertEqual(picked.map(\.name), ["today.md", "yesterday.md"])
    }

    func test_an_old_desk_offers_nothing_recent() {
        let now = 1_780_000_000
        let old = (0..<20).map { aged("_meta/old/\($0).md", now - 90 * 86_400) }
        XCTAssertTrue(FileTree.recent(of: old, now: now).isEmpty)
    }

    func test_a_short_index_needs_no_shortcut() {
        let now = 1_780_000_000
        let few = [aged("a.md", now - 60), aged("b.md", now - 120)]
        XCTAssertTrue(FileTree.recent(of: few, now: now).isEmpty)
    }

    func test_a_missing_or_future_mtime_is_never_recent() {
        let now = 1_780_000_000
        var files = (0..<20).map { aged("_meta/old/\($0).md", now - 90 * 86_400) }
        files.append(aged("_meta/no-mtime.md", 0))
        files.append(aged("_meta/from-the-future.md", now + 86_400))
        XCTAssertTrue(FileTree.recent(of: files, now: now).isEmpty)
    }

    func test_recent_is_capped_and_ties_break_on_path() {
        let now = 1_780_000_000
        var files = (0..<20).map { aged("_meta/old/\($0).md", now - 90 * 86_400) }
        files += ["f.md", "a.md", "e.md", "b.md", "d.md", "c.md"].map { aged($0, now - 60) }
        let picked = FileTree.recent(of: files, now: now)
        XCTAssertEqual(picked.count, 5)
        XCTAssertEqual(picked.map(\.name), ["a.md", "b.md", "c.md", "d.md", "e.md"])
    }

    func test_folders_are_read_off_the_paths_and_drawn_once_each() {
        let rows = FileTree.rows(of: [file("README.md"), file("_meta/plans/a.md"),
                                      file("_meta/plans/b.md"), file("docs/guide.md")])
        XCTAssertEqual(rows.map(\.id), ["README.md", "_meta/", "_meta/plans/",
                                        "_meta/plans/a.md", "_meta/plans/b.md",
                                        "docs/", "docs/guide.md"])
        XCTAssertEqual(rows.map(\.depth), [0, 0, 1, 2, 2, 0, 1])
    }

    func test_a_shut_folder_hides_everything_under_it_and_counts_it() {
        let rows = FileTree.rows(of: [file("_meta/plans/a.md"), file("_meta/deep/x/y.md"),
                                      file("docs/guide.md")],
                                 closed: ["_meta/"])
        XCTAssertEqual(rows.map(\.id), ["_meta/", "docs/", "docs/guide.md"])
        XCTAssertEqual(rows.first?.count, 2)
    }

    func test_a_folder_shut_under_an_open_one_hides_only_its_own() {
        let rows = FileTree.rows(of: [file("_meta/plans/a.md"), file("_meta/notes.md")],
                                 closed: ["_meta/plans/"])
        XCTAssertEqual(rows.map(\.id), ["_meta/", "_meta/plans/", "_meta/notes.md"])
    }

    func test_an_empty_index_is_no_rows() {
        XCTAssertTrue(FileTree.rows(of: []).isEmpty)
    }
}

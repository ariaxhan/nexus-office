import XCTest


final class FileTreeTests: XCTestCase {
    private func file(_ path: String) -> ContextFile {
        ContextFile(path: path, name: String(path.split(separator: "/").last ?? ""),
                    group: "", bytes: 1)
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

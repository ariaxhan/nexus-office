import XCTest

final class OfficeURLTests: XCTestCase {
    func test_the_one_shape_that_opens_a_file() {
        let ask = OfficeURL.parse(URL(string: "nexus-office://open?repo=acme/checkout-api&path=docs/api/capture.md")!)
        XCTAssertEqual(ask, OfficeURL(repo: "acme/checkout-api", path: "docs/api/capture.md"))
    }

    func test_a_percent_encoded_path_comes_back_as_a_path() {
        let ask = OfficeURL.parse(URL(string: "nexus-office://open?repo=a/b&path=_meta/plans/one%20two.md")!)
        XCTAssertEqual(ask?.path, "_meta/plans/one two.md")
    }

    func test_web_office_destination_keeps_the_exact_requested_file() {
        let ask = OfficeURL(repo: "ariaxhan/CollabVault", path: "content/one two.md")
        let url = ask.webDestination(from: URL(string: "http://127.0.0.1:8790")!)
        let parts = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        XCTAssertEqual(parts.host, "127.0.0.1")
        XCTAssertEqual(parts.fragment?.split(separator: "?").first, "document")
        let query = URLComponents(string: "?" + (parts.fragment?.split(separator: "?").last.map(String.init) ?? ""))!
        XCTAssertEqual(query.queryItems?.first(where: { $0.name == "repo" })?.value, ask.repo)
        XCTAssertEqual(query.queryItems?.first(where: { $0.name == "path" })?.value, ask.path)
    }

    func test_anything_that_is_not_a_relative_file_in_a_repo_is_not_a_request() {
        for bad in ["nexus-office://open?repo=acme&path=x.md",
                    "nexus-office://open?repo=acme/x&path=/etc/passwd",
                    "nexus-office://open?repo=acme/x&path=../x.md",
                    "nexus-office://open?repo=acme/x&path=",
                    "nexus-office://open?repo=a%20b/x&path=x.md",
                    "nexus-office://quit?repo=acme/x&path=x.md",
                    "https://open?repo=acme/x&path=x.md"] {
            XCTAssertNil(OfficeURL.parse(URL(string: bad)!), bad)
        }
    }
}

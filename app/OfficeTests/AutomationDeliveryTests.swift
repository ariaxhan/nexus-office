import XCTest

final class AutomationDeliveryTests: XCTestCase {
    private func page(health: String, blocked: Bool = false) throws -> Automation {
        let bad = blocked ? #", "blocked":[{"repo":"a/b","pr":1,"problems":["bad"]}]"# : ""
        let json = #"{"state":"ok","delivery":{"pipeline_health":"\#(health)"\#(bad)}}"#
        return try JSONDecoder().decode(Automation.self, from: Data(json.utf8))
    }

    func testUnconfiguredDeliveryNeedsSomebodyEvenWithoutBlockedRows() throws {
        XCTAssertTrue(try page(health: "unconfigured").needsSomebody)
    }

    func testNeverRunDeliveryNeedsSomebodyEvenWithoutBlockedRows() throws {
        XCTAssertTrue(try page(health: "never").needsSomebody)
    }

    func testHealthyDeliveryDoesNotNeedSomebodyByItself() throws {
        XCTAssertFalse(try page(health: "ok").needsSomebody)
    }
}

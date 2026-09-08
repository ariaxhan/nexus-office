import XCTest

final class OfficeMobileUITests: XCTestCase {
    func testPhonePodcastContinuesInBackground() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--office-url", ProcessInfo.processInfo.environment["OFFICE_TEST_URL"] ?? "https://arias-macbook-pro-2.tail4f309a.ts.net:9443"]
        app.launch()
        let library = app.links["Library"]
        XCTAssertTrue(library.waitForExistence(timeout: 30), app.debugDescription)
        library.tap()
        let podcasts = app.buttons["Podcasts"]
        XCTAssertTrue(podcasts.waitForExistence(timeout: 20))
        podcasts.tap()
        let episode = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "The Harbour Is Only the Beginning")).firstMatch
        XCTAssertTrue(episode.waitForExistence(timeout: 20), app.debugDescription)
        episode.tap()
        let play = app.buttons["Play episode"]
        XCTAssertTrue(play.waitForExistence(timeout: 15))
        play.tap()
        let position = app.staticTexts.matching(NSPredicate(format: "label MATCHES %@", "[0-9]+:[0-9]{2} / [0-9]+:[0-9]{2}")).firstMatch
        XCTAssertTrue(position.waitForExistence(timeout: 15), app.debugDescription)
        sleep(3)
        let before = position.label
        XCUIDevice.shared.press(.home)
        sleep(6)
        app.activate()
        XCTAssertTrue(position.waitForExistence(timeout: 15))
        XCTAssertNotEqual(position.label, before, "Playback must advance while Office is backgrounded")
        let proof = XCTAttachment(screenshot: app.screenshot())
        proof.name = "Office podcast after background playback"
        proof.lifetime = .keepAlways
        add(proof)
        app.buttons["Pause"].firstMatch.tap()
    }
}

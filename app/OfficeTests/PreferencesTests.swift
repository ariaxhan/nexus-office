import XCTest

/// What a preference has to survive.
///
/// The interesting cases are all about the store rather than the control: a
/// filter that draws is a screenshot away, and a filter that quietly comes back
/// off after a relaunch, or an order written by a newer version that reorders
/// the floor into something nobody chose, are exactly the defects no picture
/// would ever show.
@MainActor
final class PreferencesTests: XCTestCase {

    /// Its own suite, wiped either side. `UserDefaults.standard` is shared with
    /// the machine running this, so a test that touched it would change a real
    /// person's roster and would read whatever the last run left behind.
    private var suite: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suite = "app.nexusoffice.tests." + UUID().uuidString
        defaults = UserDefaults(suiteName: suite)
        defaults.removePersistentDomain(forName: suite)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        defaults = nil
        suite = nil
        super.tearDown()
    }

    func test_a_choice_survives_the_process_that_made_it() {
        let made = Preferences(defaults: defaults)
        made.set(deskSort: .issues)
        made.set(needsOnly: true)

        let relaunched = Preferences(defaults: defaults)
        XCTAssertEqual(relaunched.deskSort, .issues)
        XCTAssertTrue(relaunched.needsOnly)
    }

    func test_nothing_chosen_is_the_order_the_roster_has_always_had() {
        let fresh = Preferences(defaults: defaults)
        XCTAssertEqual(fresh.deskSort, .owner)
        XCTAssertFalse(fresh.needsOnly)
    }

    /// An order from a newer version. Falling back is the only honest read:
    /// picking one of the five at random reorders the floor into an answer
    /// nobody asked for and says nothing about having done it.
    func test_an_order_this_version_does_not_know_falls_back() {
        defaults.set("byWhoIsAwake", forKey: "settings.deskSort.v1")
        XCTAssertEqual(Preferences(defaults: defaults).deskSort, .owner)
    }

    /// The default is in memory, so a store built by a test or by the shot
    /// harness cannot write to the machine it is running on.
    func test_a_store_with_no_preferences_given_writes_nothing_down() {
        let quiet = Preferences()
        quiet.set(deskSort: .prs)
        XCTAssertEqual(quiet.deskSort, .prs)
        XCTAssertNil(defaults.string(forKey: "settings.deskSort.v1"))
    }

    /// The store is what the views bind to, so the load has to land there and
    /// a change made through it has to reach the disk.
    func test_the_store_opens_on_what_was_chosen_and_saves_what_changes() {
        Preferences(defaults: defaults).set(deskSort: .name)

        let store = Store(api: Api(source: .live(URL(string: "http://127.0.0.1:8765")!)),
                          prefs: Preferences(defaults: defaults))
        XCTAssertEqual(store.deskSort, .name)

        store.needsOnly = true
        XCTAssertTrue(Preferences(defaults: defaults).needsOnly)
    }
}

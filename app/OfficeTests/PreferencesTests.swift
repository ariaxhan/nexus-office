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

    // MARK: - the type scale

    func test_the_type_scale_is_one_until_somebody_steps_it() {
        XCTAssertEqual(Preferences(defaults: defaults).typeScale, 1)
    }

    func test_a_step_lands_on_the_next_named_size_and_survives_a_relaunch() {
        let prefs = Preferences(defaults: defaults)
        prefs.stepTypeScale(+1)
        XCTAssertEqual(prefs.typeScale, 1.1)
        prefs.stepTypeScale(+1)
        XCTAssertEqual(prefs.typeScale, 1.25)
        prefs.stepTypeScale(-1)
        XCTAssertEqual(prefs.typeScale, 1.1)
        XCTAssertEqual(Preferences(defaults: defaults).typeScale, 1.1)
    }

    func test_the_scale_stops_at_both_ends_rather_than_running_away() {
        let prefs = Preferences(defaults: defaults)
        for _ in 0..<40 { prefs.stepTypeScale(+1) }
        XCTAssertEqual(prefs.typeScale, Preferences.typeScales.last!)
        for _ in 0..<40 { prefs.stepTypeScale(-1) }
        XCTAssertEqual(prefs.typeScale, Preferences.typeScales.first!)
    }

    func test_an_unreadable_stored_scale_comes_up_readable() {
        defaults.set(0.05, forKey: "settings.typeScale.v1")
        XCTAssertEqual(Preferences(defaults: defaults).typeScale, 0.8)
        defaults.set(Double.nan, forKey: "settings.typeScale.v1")
        XCTAssertEqual(Preferences(defaults: defaults).typeScale, 1)
    }

    // MARK: - the layout

    func test_a_preset_survives_the_process_that_chose_it() {
        Preferences(defaults: defaults).set(layout: .compare)
        XCTAssertEqual(Preferences(defaults: defaults).layout, .compare)
    }

    func test_nothing_chosen_is_the_layout_this_app_has_always_had() {
        XCTAssertEqual(Preferences(defaults: defaults).layout, .focus)
    }

    func test_appearance_defaults_to_system_and_survives_a_relaunch() {
        XCTAssertEqual(Preferences(defaults: defaults).appearance, .system)
        Preferences(defaults: defaults).set(appearance: .dark)
        XCTAssertEqual(Preferences(defaults: defaults).appearance, .dark)
    }

    func test_an_unknown_appearance_falls_back_to_system() {
        defaults.set("sepia", forKey: "settings.appearance.v1")
        XCTAssertEqual(Preferences(defaults: defaults).appearance, .system)
    }

    /// A preset written by a newer version. Same reasoning as the desk order:
    /// guessing which of the three it meant rearranges the window into a shape
    /// nobody chose and says nothing about having done it.
    func test_a_preset_this_version_does_not_know_falls_back_to_focus() {
        defaults.set("threePanes", forKey: "settings.layout.v1")
        XCTAssertEqual(Preferences(defaults: defaults).layout, .focus)
    }

    func test_the_store_opens_on_the_preset_that_was_chosen_and_saves_a_change() {
        Preferences(defaults: defaults).set(layout: .minimal)

        let store = Store(api: Api(source: .live(URL(string: "http://127.0.0.1:8765")!)),
                          prefs: Preferences(defaults: defaults))
        XCTAssertEqual(store.layout, .minimal)

        store.layout = .compare
        XCTAssertEqual(Preferences(defaults: defaults).layout, .compare)
    }

    // MARK: - which panes show

    /// Both default ON, which is the case `bool(forKey:)` cannot express: it
    /// answers false for "never set" as well as for "turned off", so an
    /// untouched install would come up with no bots and no wall.
    func test_panes_nobody_has_touched_are_both_on() {
        let fresh = Preferences(defaults: defaults)
        XCTAssertTrue(fresh.showBots)
        XCTAssertTrue(fresh.showWall)
    }

    func test_a_pane_turned_off_stays_off_across_a_relaunch() {
        let made = Preferences(defaults: defaults)
        made.set(showBots: false)

        let relaunched = Preferences(defaults: defaults)
        XCTAssertFalse(relaunched.showBots)
        XCTAssertTrue(relaunched.showWall)

        relaunched.set(showBots: true)
        XCTAssertTrue(Preferences(defaults: defaults).showBots)
    }

    func test_the_store_carries_pane_visibility_both_ways() {
        Preferences(defaults: defaults).set(showWall: false)

        let store = Store(api: Api(source: .live(URL(string: "http://127.0.0.1:8765")!)),
                          prefs: Preferences(defaults: defaults))
        XCTAssertFalse(store.showWall)

        store.showBots = false
        XCTAssertFalse(Preferences(defaults: defaults).showBots)
    }

    // MARK: - the faces

    func test_a_desk_colour_survives_the_relaunch_and_reset_removes_it() {
        let made = Preferences(defaults: defaults)
        XCTAssertTrue(made.set(face: "#4C8DFF", for: "ariaxhan/nexus-office"))

        let relaunched = Preferences(defaults: defaults)
        XCTAssertEqual(relaunched.faceOverrides["ariaxhan/nexus-office"], "#4c8dff")

        relaunched.clearFace(repo: "ariaxhan/nexus-office")
        XCTAssertNil(Preferences(defaults: defaults).faceOverrides["ariaxhan/nexus-office"])
    }

    /// A half-typed hex is refused rather than stored, so the field cannot land
    /// a colour nothing can draw.
    func test_a_colour_that_is_not_a_colour_is_never_written_down() {
        let prefs = Preferences(defaults: defaults)
        XCTAssertFalse(prefs.set(face: "#4c8", for: "ariaxhan/nexus-office"))
        XCTAssertTrue(prefs.faceOverrides.isEmpty)
        XCTAssertNil(defaults.dictionary(forKey: "faces.v1"))
    }

    /// The book is the view the rows use, and it has to be reading the same
    /// dictionary the settings write: two stores of the same fact is how a
    /// colour comes back after a Reset.
    func test_the_face_book_reads_and_writes_the_same_preferences() {
        let prefs = Preferences(defaults: defaults)
        let book = FaceBook(prefs: prefs)

        XCTAssertEqual(book.hex(repo: "ariaxhan/nexus-office"),
                       Faces.coat(repo: "ariaxhan/nexus-office"))
        XCTAssertFalse(book.isChosen(repo: "ariaxhan/nexus-office"))

        XCTAssertTrue(book.choose(repo: "ariaxhan/nexus-office", hex: "#4c8dff"))
        XCTAssertEqual(prefs.faceOverrides["ariaxhan/nexus-office"], "#4c8dff")
        XCTAssertTrue(FaceBook(prefs: Preferences(defaults: defaults))
            .isChosen(repo: "ariaxhan/nexus-office"))

        book.reset(repo: "ariaxhan/nexus-office")
        XCTAssertEqual(FaceBook(prefs: Preferences(defaults: defaults))
            .hex(repo: "ariaxhan/nexus-office"),
                       Faces.coat(repo: "ariaxhan/nexus-office"))
    }
}

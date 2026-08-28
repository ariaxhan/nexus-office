import XCTest

/// Whether a desk keeps its face.
///
/// Everything interesting about a face is arithmetic on a string, and none of it
/// is visible in a picture: a framing of the roster shows a floor of colours
/// whether or not they are the same colours the floor had yesterday, and whether
/// or not they are the ones the villagers wore. So the derivation is asserted
/// here rather than looked at.
final class FacesTests: XCTestCase {

    // MARK: - the derivation the 3d room used

    /// FNV-1a, 32 bit, against values the JS produced for the same strings. A
    /// hash that drifts is every desk changing colour at once, silently.
    func testTheHashIsTheOneTheVillagersUsed() {
        XCTAssertEqual(Faces.hash(""), 0x811c_9dc5)
        XCTAssertEqual(Faces.hash("a"), 0xe40c_292c)
        XCTAssertEqual(Faces.hash("foobar"), 0xbf9c_f968)
    }

    /// The point of deriving rather than storing: no roster file to drift, and
    /// the same answer on every machine and every launch.
    func testAFaceIsAPureFunctionOfTheRepoPath() {
        XCTAssertEqual(Faces.coat(repo: "ariaxhan/nexus-office"),
                       Faces.coat(repo: "ariaxhan/nexus-office"))
        XCTAssertEqual(Faces.name(repo: "ariaxhan/nexus-office"),
                       Faces.name(repo: "ariaxhan/nexus-office"))
        // A different desk is a different draw, not the same one shifted.
        XCTAssertNotEqual(Faces.coat(repo: "ariaxhan/nexus-office") + Faces.name(repo: "ariaxhan/nexus-office"),
                          Faces.coat(repo: "ariaxhan/our4cuts") + Faces.name(repo: "ariaxhan/our4cuts"))
    }

    /// "No two alike" is not achievable for an unbounded list of desks, and
    /// assigning colours by position in the list would buy it by breaking the
    /// property that actually matters. What can be held is that the fourteen
    /// coats are fourteen different colours and that they are all reachable.
    func testThePaletteIsFourteenDistinctColoursAndAllOfThemAreUsed() {
        XCTAssertEqual(Set(Faces.coats).count, Faces.coats.count)
        XCTAssertEqual(Set(Faces.names).count, Faces.names.count)
        var seen: Set<String> = []
        for index in 0..<4000 { seen.insert(Faces.coat(repo: "owner/repo-\(index)")) }
        XCTAssertEqual(seen, Set(Faces.coats), "a coat no repo can ever wear")
    }

    /// Every coat is six hex digits, or the row draws the grey fallback and the
    /// whole feature is one colour.
    func testEveryCoatParses() {
        for coat in Faces.coats {
            XCTAssertEqual(Faces.normalise(hex: coat), coat)
            XCTAssertNotEqual(Palette.RGB(hex: coat), Palette.RGB(white: 0.42))
        }
    }

    // MARK: - what a person typed

    func testHexIsAcceptedTheWayAPersonWritesItAndRefusedOtherwise() {
        XCTAssertEqual(Faces.normalise(hex: "#4C8DFF"), "#4c8dff")
        XCTAssertEqual(Faces.normalise(hex: "4c8dff"), "#4c8dff")
        XCTAssertEqual(Faces.normalise(hex: "  #4c8dff \n"), "#4c8dff")
        for bad in ["", "#", "#4c8", "#4c8dfff", "#4c8dfg", "rebeccapurple", "#12 456"] {
            XCTAssertNil(Faces.normalise(hex: bad), "\(bad) was accepted as a colour")
        }
    }

    // MARK: - the book

    @MainActor
    func testChoosingADeskColourSticksAndResettingGivesTheCoatBack() {
        let faces = FaceBook(defaults: nil)
        let repo = "ariaxhan/nexus-office"
        XCTAssertEqual(faces.hex(repo: repo), Faces.coat(repo: repo))
        XCTAssertFalse(faces.isChosen(repo: repo))

        XCTAssertTrue(faces.choose(repo: repo, hex: "4C8DFF"))
        XCTAssertEqual(faces.hex(repo: repo), "#4c8dff")
        XCTAssertTrue(faces.isChosen(repo: repo))
        // One desk dressed is one desk dressed.
        XCTAssertEqual(faces.hex(repo: "ariaxhan/our4cuts"), Faces.coat(repo: "ariaxhan/our4cuts"))

        faces.reset(repo: repo)
        XCTAssertEqual(faces.hex(repo: repo), Faces.coat(repo: repo))
        XCTAssertFalse(faces.isChosen(repo: repo))
    }

    /// A half-typed hex must not land as a colour: the field is read on every
    /// keystroke, and `#4c8` is a person still typing rather than a choice.
    @MainActor
    func testAnUnparseableHexIsRefusedRatherThanStored() {
        let faces = FaceBook(defaults: nil)
        let repo = "ariaxhan/nexus-office"
        XCTAssertFalse(faces.choose(repo: repo, hex: "#4c8"))
        XCTAssertFalse(faces.isChosen(repo: repo))
        XCTAssertEqual(faces.hex(repo: repo), Faces.coat(repo: repo))
    }

    /// Same promise `Reactions` makes: a store handed no defaults writes
    /// nothing. `shoot.sh` opens the real app on this machine, and a harness
    /// that could recolour Aria's desks is a check that damages what it checks.
    @MainActor
    func testAStoreWithNoDefaultsNeverWritesAnything() {
        let suite = "faces.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }

        FaceBook(defaults: nil).choose(repo: "ariaxhan/nexus-office", hex: "#4c8dff")
        XCTAssertNil(defaults.dictionary(forKey: "faces.v1"))

        let writing = FaceBook(defaults: defaults)
        writing.choose(repo: "ariaxhan/nexus-office", hex: "#4c8dff")
        XCTAssertEqual(FaceBook(defaults: defaults).hex(repo: "ariaxhan/nexus-office"), "#4c8dff")
    }
}

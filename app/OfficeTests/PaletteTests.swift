import XCTest

/// Whether a person can read the light room.
///
/// This is the whole reason `Palette` is Foundation and not `Theme`. Contrast is
/// the one property of a colour that cannot be settled by looking at it: two
/// greys that are obviously different on the machine that picked them are one
/// grey on a laptop at an angle in a bright room, and a timestamp that vanished
/// would pass every build and every framing where somebody happened to look at
/// the wrong corner.
final class PaletteTests: XCTestCase {

    // MARK: - the arithmetic itself

    /// Break the ruler and everything measured with it is a guess.
    func testContrastAgreesWithTheTwoValuesEverybodyKnows() {
        let black = Palette.RGB(white: 0)
        let white = Palette.RGB(white: 1)
        XCTAssertEqual(Palette.RGB.contrast(black, white), 21, accuracy: 0.01)
        XCTAssertEqual(Palette.RGB.contrast(white, white), 1, accuracy: 0.0001)
        // Symmetric: which one you call the background cannot change the answer.
        XCTAssertEqual(Palette.RGB.contrast(black, white),
                       Palette.RGB.contrast(white, black), accuracy: 0.0001)
    }

    func testLuminanceIsTheWcagOne() {
        XCTAssertEqual(Palette.RGB(white: 0).relativeLuminance, 0, accuracy: 0.0001)
        XCTAssertEqual(Palette.RGB(white: 1).relativeLuminance, 1, accuracy: 0.0001)
        // Mid grey is not mid luminance, which is the whole reason this curve
        // exists and the reason eyeballing a palette does not work.
        XCTAssertEqual(Palette.RGB(hex: "#808080").relativeLuminance, 0.2159, accuracy: 0.001)
    }

    func testHexParsesAndFallsBackRatherThanCrashing() {
        let amber = Palette.RGB(hex: "#ffb020")
        XCTAssertEqual(amber.red, 1, accuracy: 0.0001)
        XCTAssertEqual(amber.green, 0xb0 / 255, accuracy: 0.0001)
        XCTAssertEqual(amber.blue, 0x20 / 255, accuracy: 0.0001)
        XCTAssertEqual(Palette.RGB(hex: "ffb020"), amber)
        XCTAssertEqual(Palette.RGB(hex: "nonsense"), Palette.RGB(white: 0.42))
        XCTAssertEqual(Palette.RGB(hex: ""), Palette.RGB(white: 0.42))
    }

    // MARK: - the floor

    /// Every word this app can draw, on every surface it can draw it on.
    func testEveryLightColourClearsTheReadableFloor() {
        var failures: [String] = []
        for pairing in Palette.legibility {
            let ratio = pairing.contrast(dark: false)
            if ratio < Palette.readableRatio {
                failures.append(String(format: "%@ on %@ is %.2f:1",
                                       pairing.word, pairing.surface, ratio))
            }
        }
        XCTAssertTrue(failures.isEmpty,
                      "light mode cannot be read: " + failures.joined(separator: ", "))
    }

    /// A palette with nothing in it passes the test above without meaning
    /// anything, and a colour added to `Theme` but not to `legibility` is a
    /// colour nobody is checking.
    func testTheLegibilityTableActuallyCoversTheRoom() {
        let words = Set(Palette.legibility.map(\.word))
        for expected in ["text", "dim", "faint", "amber", "red", "green", "blue", "onFilled"] {
            XCTAssertTrue(words.contains(expected), "\(expected) is not being checked")
        }
        for state in DeskState.allCases {
            XCTAssertTrue(words.contains("desk.\(state.rawValue)"),
                          "the \(state.rawValue) desk colour is not being checked")
        }
        let surfaces = Set(Palette.legibility.map(\.surface))
        for expected in ["ink", "roster", "raised", "selected", "well"] {
            XCTAssertTrue(surfaces.contains(expected), "\(expected) is not a checked surface")
        }
    }

    // MARK: - the dark room did not move

    /// Seven framings were photographed against these numbers. Making the room
    /// switchable was not permission to redecorate the half that already
    /// worked, and a "tidy up" of these values is a silent change to every
    /// picture in the repo.
    func testTheDarkValuesAreTheOnesThatWereAlreadyHere() {
        XCTAssertEqual(Palette.ink.dark, Palette.RGB(white: 0))
        XCTAssertEqual(Palette.roster.dark, Palette.RGB(white: 0.055))
        XCTAssertEqual(Palette.raised.dark, Palette.RGB(white: 0.105))
        XCTAssertEqual(Palette.selected.dark, Palette.RGB(white: 0.165))
        XCTAssertEqual(Palette.hairline.dark, Palette.RGB(white: 0.13))
        XCTAssertEqual(Palette.well.dark, Palette.RGB(white: 0))
        XCTAssertEqual(Palette.text.dark, Palette.RGB(white: 0.94))
        XCTAssertEqual(Palette.dim.dark, Palette.RGB(white: 0.54))
        XCTAssertEqual(Palette.faint.dark, Palette.RGB(white: 0.36))
        XCTAssertEqual(Palette.amber.dark, Palette.RGB(hex: "#ffb020"))
        XCTAssertEqual(Palette.red.dark, Palette.RGB(hex: "#ff5d5d"))
        XCTAssertEqual(Palette.green.dark, Palette.RGB(hex: "#39d98a"))
        XCTAssertEqual(Palette.blue.dark, Palette.RGB(hex: "#4c8dff"))
    }

    func testTheEightDeskColoursStillReadTheSameInTheDark() {
        XCTAssertEqual(DeskState.gated.hex, "#ffb020")
        XCTAssertEqual(DeskState.waiting.hex, "#ff5d5d")
        XCTAssertEqual(DeskState.locked.hex, "#4a4a52")
        XCTAssertEqual(DeskState.parked.hex, "#6b6b78")
        XCTAssertEqual(DeskState.refused.hex, "#ff8c42")
        XCTAssertEqual(DeskState.landed.hex, "#39d98a")
        XCTAssertEqual(DeskState.working.hex, "#4c8dff")
        XCTAssertEqual(DeskState.idle.hex, "#3a3a42")
        for state in DeskState.allCases {
            XCTAssertEqual(state.swatch.dark, Palette.RGB(hex: state.hex))
            XCTAssertEqual(state.swatch.light, Palette.RGB(hex: state.lightHex))
            XCTAssertNotEqual(state.lightHex, state.hex,
                              "\(state.rawValue) never got a light value of its own")
        }
    }

    func testTheThreeWallColoursHaveBothRooms() {
        for mood in [StateRules.SectionMood.needs, .off, .quiet] {
            XCTAssertEqual(mood.swatch.dark, Palette.RGB(hex: mood.hex))
            XCTAssertEqual(mood.swatch.light, Palette.RGB(hex: mood.lightHex))
        }
    }

    /// The two rooms are actually two rooms. A pair that resolved the same both
    /// ways would pass every contrast check by being dark mode twice.
    func testEverySwatchIsAPairAndNotOneColourTwice() {
        for pairing in Palette.legibility {
            XCTAssertNotEqual(pairing.colour.light, pairing.colour.dark,
                              "\(pairing.word) is the same colour in both rooms")
        }
        XCTAssertNotEqual(Palette.dotIdle.light, Palette.dotIdle.dark)
        XCTAssertNotEqual(Palette.dotWorking.light, Palette.dotWorking.dark)
        XCTAssertNotEqual(Palette.dotNeedsYou.light, Palette.dotNeedsYou.dark)
    }

    /// The surfaces have to be told apart from each other, or a selected row is
    /// a row that does not look selected and a card is a rectangle nobody sees.
    func testTheLightSurfacesAreDistinguishableFromEachOther() {
        let steps: [(String, Palette.Swatch, Palette.Swatch)] = [
            ("raised over ink", Palette.raised, Palette.ink),
            ("selected over roster", Palette.selected, Palette.roster),
            ("well under ink", Palette.well, Palette.ink),
            // Against the card and not only against the page: a fenced block
            // in a reply sits inside a bubble, which is where the first light
            // palette had a code panel nobody could see.
            ("well under raised", Palette.well, Palette.raised),
            ("hairline over roster", Palette.hairline, Palette.roster),
        ]
        for (what, over, under) in steps {
            let ratio = Palette.RGB.contrast(over.light, under.light)
            XCTAssertGreaterThan(ratio, 1.05, "\(what) is invisible in the light room")
        }
    }
}

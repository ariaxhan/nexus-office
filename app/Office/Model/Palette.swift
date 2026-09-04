import Foundation

/// Every colour in the office, in both rooms, as numbers.
///
/// Foundation only, deliberately, and for the same reason `StateRules` is: the
/// question "can a person read this" is arithmetic on three channels, and
/// arithmetic is provable headlessly. A palette that can only be checked by
/// looking at it is a palette that gets checked once.
///
/// `Theme` turns each pair into one `Color` that knows which room it is in.
/// Nothing in here reaches for AppKit or SwiftUI, and the day it does, the test
/// target stops building.
///
/// **The dark values are the ones that were already here.** They are written as
/// the same numbers, not as re-derived approximations, because seven framings
/// were photographed against them and a palette rewrite that quietly moves the
/// dark room is a change nobody asked for hiding inside one they did.
public enum Palette {

    // MARK: - a colour, and whether it can be read

    /// Three channels, 0 to 1, sRGB. Not a `Color`, on purpose.
    public struct RGB: Equatable {
        public let red: Double
        public let green: Double
        public let blue: Double

        public init(red: Double, green: Double, blue: Double) {
            self.red = red
            self.green = green
            self.blue = blue
        }

        /// The grey `Color(white:)` used to make, with the same numbers.
        public init(white: Double) {
            self.init(red: white, green: white, blue: white)
        }

        /// `#rrggbb`, the way `_meta/bots.json` and `DeskState` write it. An
        /// unreadable string is not a crash and not a blank: it lands on
        /// something visible, exactly as `Color(hex:)` does.
        public init(hex: String, fallback: RGB = RGB(white: 0.42)) {
            var raw = hex.trimmingCharacters(in: .whitespacesAndNewlines)
            if raw.hasPrefix("#") { raw.removeFirst() }
            guard raw.count == 6, let value = UInt64(raw, radix: 16) else {
                self = fallback
                return
            }
            self.init(red: Double((value >> 16) & 0xff) / 255,
                      green: Double((value >> 8) & 0xff) / 255,
                      blue: Double(value & 0xff) / 255)
        }

        /// WCAG 2.1 relative luminance. The one number a contrast ratio is made
        /// of, and the reason a mid grey on white is legible while the same
        /// grey on black is not.
        public var relativeLuminance: Double {
            func channel(_ raw: Double) -> Double {
                raw <= 0.04045 ? raw / 12.92 : pow((raw + 0.055) / 1.055, 2.4)
            }
            return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)
        }

        /// WCAG 2.1 contrast, 1 (invisible) to 21 (black on white).
        public static func contrast(_ one: RGB, _ other: RGB) -> Double {
            let a = one.relativeLuminance, b = other.relativeLuminance
            return (max(a, b) + 0.05) / (min(a, b) + 0.05)
        }

        public func mixed(with other: RGB, amount: Double) -> RGB {
            let amount = min(max(amount, 0), 1)
            return RGB(red: red + (other.red - red) * amount,
                       green: green + (other.green - green) * amount,
                       blue: blue + (other.blue - blue) * amount)
        }
    }

    /// One colour, in two rooms.
    public struct Swatch: Equatable {
        public let light: RGB
        public let dark: RGB

        public init(light: RGB, dark: RGB) {
            self.light = light
            self.dark = dark
        }

        public init(light: String, dark: String) {
            self.init(light: RGB(hex: light), dark: RGB(hex: dark))
        }

        public func value(dark isDark: Bool) -> RGB { isDark ? dark : light }
    }

    // MARK: - the surfaces

    /// The pane a thread is drawn on. Pure black in the dark room; warm paper
    /// rather than white in the light one, because a screen full of #ffffff
    /// under a chat is a screen that glares.
    public static let defaultLightCanvas = "#f8f2e8"
    public static let defaultDarkCanvas = "#000000"
    public static let ink = Swatch(light: RGB(hex: defaultLightCanvas), dark: RGB(white: 0))

    /// The left column.
    public static let roster = Swatch(light: RGB(hex: "#f1efeb"), dark: RGB(white: 0.055))

    /// A bubble, a card, a field: one step off the page.
    public static let raised = Swatch(light: RGB(hex: "#e8e5df"), dark: RGB(white: 0.105))

    /// The row you are on.
    public static let selected = Swatch(light: RGB(hex: "#e0dbd1"), dark: RGB(white: 0.165))

    /// The only lines drawn on this screen.
    public static let hairline = Swatch(light: RGB(hex: "#d6d1c7"), dark: RGB(white: 0.13))

    /// The recess a command or a half-written comment sits in: below the page
    /// rather than above it. Black in the dark room, which is what it always
    /// was.
    ///
    /// The light value is measured against `raised` and not against `ink`,
    /// because a fenced block in a reply sits inside a bubble rather than on
    /// the page. The first light palette got this wrong: `#eae7e0` on `#e8e5df`
    /// is 1.008:1, and the code panel in `app-light.png` was a rectangle you
    /// could only find by knowing it was there.
    public static let well = Swatch(light: RGB(hex: "#dcd8d0"), dark: RGB(white: 0))

    // MARK: - the words

    public static let text = Swatch(light: RGB(hex: "#1b1a18"), dark: RGB(white: 0.94))
    public static let dim = Swatch(light: RGB(hex: "#544f46"), dark: RGB(white: 0.54))
    public static let faint = Swatch(light: RGB(hex: "#615c54"), dark: RGB(white: 0.36))

    /// What goes on top of a filled accent button. Black on a bright green in
    /// the dark room; white on a deep green in the light one, because the light
    /// room's accents are dark enough to be read on paper and black on them is
    /// not.
    public static let onFilled = Swatch(light: RGB(white: 1), dark: RGB(white: 0))

    // MARK: - the four meanings

    /// Amber shouts, red asks, blue works, green landed.
    ///
    /// The light values are not the dark ones dimmed. A colour that carries a
    /// meaning has to survive being read at eleven points on paper, and
    /// `#ffb020` on `#fbfaf8` is 1.8:1, which is a decoration rather than a
    /// word.
    public static let amber = Swatch(light: RGB(hex: "#845200"), dark: RGB(hex: "#ffb020"))
    public static let red = Swatch(light: RGB(hex: "#b81d1d"), dark: RGB(hex: "#ff5d5d"))
    public static let green = Swatch(light: RGB(hex: "#0d6a3d"), dark: RGB(hex: "#39d98a"))
    public static let blue = Swatch(light: RGB(hex: "#1854c4"), dark: RGB(hex: "#4c8dff"))

    /// Custom canvases tint every structural surface together, preserving the
    /// room's depth instead of replacing only the page and leaving cards from
    /// another palette behind. Demo shots stay isolated from real preferences.
    public static func surface(_ swatch: Swatch, dark: Bool) -> RGB {
        let isDemo = CommandLine.arguments.contains("--demo")
            || !(ProcessInfo.processInfo.environment["OFFICE_DEMO"] ?? "").isEmpty
        guard !isDemo,
              let hex = UserDefaults.standard.string(forKey: dark
                  ? "settings.canvas.dark.v1" : "settings.canvas.light.v1")
        else { return swatch.value(dark: dark) }
        let canvas = RGB(hex: hex, fallback: ink.value(dark: dark))
        let edge = RGB(white: dark ? 1 : 0)
        let amount: Double
        switch swatch {
        case ink: amount = 0
        case roster: amount = dark ? 0.055 : 0.04
        case raised: amount = dark ? 0.105 : 0.08
        case selected: amount = dark ? 0.165 : 0.12
        case hairline: amount = dark ? 0.13 : 0.16
        case well: amount = dark ? 0 : 0.12
        default: return swatch.value(dark: dark)
        }
        return canvas.mixed(with: edge, amount: amount)
    }

    // MARK: - the menu bar

    /// The dot, which is drawn into a bitmap rather than drawn by SwiftUI, so
    /// it resolves its own appearance. Not in `legibility`: a fourteen point
    /// circle is not text, and holding it to a text ratio would only make it
    /// muddy.
    public static let dotIdle = Swatch(light: RGB(white: 0.42), dark: RGB(white: 0.55))
    public static let dotWorking = Swatch(light: RGB(hex: "#1854c4"),
                                          dark: RGB(red: 0.30, green: 0.55, blue: 1.0))
    public static let dotNeedsYou = Swatch(light: RGB(hex: "#cc7000"),
                                           dark: RGB(red: 1.0, green: 0.69, blue: 0.13))

    // MARK: - what has to stay readable

    /// One colour, on one surface it is actually drawn on.
    public struct Pairing {
        public let word: String
        public let colour: Swatch
        public let surface: String
        public let background: Swatch

        public func contrast(dark: Bool) -> Double {
            RGB.contrast(colour.value(dark: dark), background.value(dark: dark))
        }
    }

    /// The floor for body text, from WCAG 2.1 AA.
    public static let readableRatio: Double = 4.5

    /// Every pairing this app can put on screen, as a list a test can walk.
    ///
    /// This exists because contrast is the one thing about a palette that
    /// cannot be settled by looking: two greys that are obviously different on
    /// the machine that chose them are one grey on a laptop at an angle in a
    /// bright room. Adding a colour without adding it here is how a light room
    /// quietly acquires a sentence nobody can read.
    ///
    /// The dark room is not held to the same floor and never was: `faint` on
    /// black is 3.1:1 today, deliberately, because a timestamp that shouts is a
    /// timestamp that competes with the message. Paper has no such headroom,
    /// which is why the light column is the one the test asserts.
    public static var legibility: [Pairing] {
        let surfaces: [(String, Swatch)] = [
            ("ink", ink), ("roster", roster), ("raised", raised),
            ("selected", selected), ("well", well),
        ]
        var words: [(String, Swatch)] = [
            ("text", text), ("dim", dim), ("faint", faint),
            ("amber", amber), ("red", red), ("green", green), ("blue", blue),
        ]
        words += DeskState.allCases.map { ("desk.\($0.rawValue)", $0.swatch) }
        words += [StateRules.SectionMood.needs, .off, .quiet]
            .map { ("wall.\($0.rawValue)", $0.swatch) }

        return words.flatMap { word in
            surfaces.map { Pairing(word: word.0, colour: word.1,
                                   surface: $0.0, background: $0.1) }
        } + [
            // The one place a word is drawn on an accent rather than a surface.
            Pairing(word: "onFilled", colour: onFilled, surface: "green", background: green),
        ]
    }
}

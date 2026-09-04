import AppKit
import SwiftUI

/// The look, in one place.
///
/// No toolbar, no sidebar chrome, no borders drawn for their own sake. The only
/// lines on screen are the ones that separate two things a person actually
/// reads apart.
///
/// System font throughout. A chat app that ships its own typeface is a chat app
/// that looks like every other one.
///
/// **The room follows the machine.** Every colour here is a pair out of
/// `Palette`, resolved per draw against the appearance the view is actually in,
/// so switching the Mac to Light Appearance switches this app with it and
/// nothing has to be restarted or re-read. The dark half of each pair is the
/// number that was already here, unchanged.
enum Theme {
    static let ink = Palette.ink.color
    static let roster = Palette.roster.color
    static let raised = Palette.raised.color
    static let selected = Palette.selected.color
    static let hairline = Palette.hairline.color
    /// The recess a command or a half-written comment sits in. This used to be
    /// a literal `Color.black` at three call sites, which is a colour that
    /// means "below the page" in one room and "a hole in the paper" in the
    /// other.
    static let well = Palette.well.color

    static let text = Palette.text.color
    static let dim = Palette.dim.color
    static let faint = Palette.faint.color
    /// The word on a filled accent button.
    static let onFilled = Palette.onFilled.color

    static let amber = Palette.amber.color
    static let red = Palette.red.color
    static let green = Palette.green.color
    static let blue = Palette.blue.color

    /// How wide the roster opens, and, because `HSplitView` hands a flexible
    /// child its MAXIMUM rather than its ideal, also how wide it stays until
    /// somebody drags the divider. It opened at 560 for as long as the ceiling
    /// said 560; 152 was too narrow to read a repo name in. This is the middle,
    /// measured on screen rather than argued about in a comment.
    static let rosterWidth: CGFloat = 240
    static let rosterMaxWidth: CGFloat = 520

    /// A fact's colour, from the tone its source put on it.
    ///
    /// The vocabulary is closed in `StateRules`, so a word nobody defined lands
    /// on the ordinary text colour rather than painting a number a meaning it
    /// was never given.
    static func tone(_ tone: StateRules.SectionTone) -> Color {
        switch tone {
        case .ok: return green
        case .warn: return amber
        case .bad: return red
        case .dim: return faint
        case .plain: return text
        }
    }

    /// A desk's colour, and a wall row's. Both are drawn as a ring AND written
    /// as a word, which is why neither can be read off `hex` alone any more.
    static func color(_ state: DeskState) -> Color { state.swatch.color }
    static func color(_ mood: StateRules.SectionMood) -> Color { mood.swatch.color }
}

/// The reading type's multiplier, handed down from the root so every page in
/// the window grows and shrinks together under Cmd + and Cmd -.
private struct TypeScaleKey: EnvironmentKey {
    static let defaultValue: Double = 1
}

private struct FontPresetKey: EnvironmentKey {
    static let defaultValue: FontPreset = .system
}
private struct ThemeRevisionKey: EnvironmentKey { static let defaultValue = "" }

extension EnvironmentValues {
    var typeScale: Double {
        get { self[TypeScaleKey.self] }
        set { self[TypeScaleKey.self] = newValue }
    }

    var fontPreset: FontPreset {
        get { self[FontPresetKey.self] }
        set { self[FontPresetKey.self] = newValue }
    }
    var themeRevision: String {
        get { self[ThemeRevisionKey.self] }
        set { self[ThemeRevisionKey.self] = newValue }
    }
}

extension FontPreset {
    var design: Font.Design {
        switch self {
        case .system: return .default
        case .rounded: return .rounded
        case .serif: return .serif
        }
    }
}

/// The only way this app chooses a point size. The modifier reads the root's
/// persisted scale itself, so a leaf view cannot forget to thread it through.
private struct OfficeFontModifier: ViewModifier {
    @Environment(\.typeScale) private var scale
    @Environment(\.fontPreset) private var preset
    @Environment(\.themeRevision) private var themeRevision
    let size: Double
    let weight: Font.Weight
    let design: Font.Design
    let monospaced: Bool
    let monospacedDigits: Bool

    func body(content: Content) -> some View {
        _ = themeRevision
        let chosenDesign = design == .default ? preset.design : design
        var font = Font.system(size: size * scale, weight: weight, design: chosenDesign)
        if monospaced { font = font.monospaced() }
        if monospacedDigits { font = font.monospacedDigit() }
        return content.font(font)
    }
}

private struct OfficeLabelStyle: LabelStyle {
    let size: Double
    let symbolSize: Double
    let weight: Font.Weight

    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 5) {
            configuration.icon.officeSymbol(size: symbolSize, weight: weight)
            configuration.title.officeFont(size: size, weight: weight)
        }
    }
}

extension View {
    func officeFont(size: Double, weight: Font.Weight = .regular,
                    design: Font.Design = .default, monospaced: Bool = false,
                    monospacedDigits: Bool = false) -> some View {
        modifier(OfficeFontModifier(size: size, weight: weight, design: design,
                                    monospaced: monospaced,
                                    monospacedDigits: monospacedDigits))
    }

    /// SF Symbols are controls and landmarks, not text. They keep their
    /// footprint while the words around them become easier to read.
    func officeSymbol(size: Double, weight: Font.Weight = .regular) -> some View {
        font(.system(size: size, weight: weight))
    }

    func officeLabel(size: Double = 13, symbolSize: Double = 12,
                     weight: Font.Weight = .regular) -> some View {
        labelStyle(OfficeLabelStyle(size: size, symbolSize: symbolSize,
                                    weight: weight))
    }
}

extension Palette.Swatch {
    /// One colour that knows which room it is in.
    ///
    /// `NSColor(name:dynamicProvider:)` rather than two `Color`s and a branch:
    /// the provider is asked again every time anything redraws, so a person
    /// changing the system appearance while the office is open watches it
    /// change, and a view that draws itself into an appearance it was not built
    /// under still comes out right.
    var color: Color { Color(nsColor: nsColor) }

    var nsColor: NSColor {
        NSColor(name: nil) { appearance in
            let rgb = Palette.surface(self, dark: appearance.isDark)
            return NSColor(srgbRed: rgb.red, green: rgb.green, blue: rgb.blue, alpha: 1)
        }
    }
}

extension NSAppearance {
    /// Dark, in the only way that is safe to ask. The name can be
    /// `darkAqua`, `vibrantDark`, or an accessibility variant of either, so
    /// comparing `name` against one constant answers the wrong question.
    var isDark: Bool { bestMatch(from: [.aqua, .darkAqua]) == .darkAqua }
}

extension Color {
    /// `#rrggbb`, the way `_meta/bots.json` writes it. An unreadable colour is
    /// not a crash and not a blank: it falls back to something visible.
    ///
    /// One parse, in `Palette.RGB`, so the string a bot picked for itself and
    /// the strings this app picked for its own states cannot start disagreeing
    /// about what `#39d98a` means.
    init(hex: String, fallback: Color = Color(white: 0.42)) {
        var raw = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if raw.hasPrefix("#") { raw.removeFirst() }
        guard raw.count == 6, UInt64(raw, radix: 16) != nil else {
            self = fallback
            return
        }
        let rgb = Palette.RGB(hex: hex)
        self.init(.sRGB, red: rgb.red, green: rgb.green, blue: rgb.blue)
    }

    /// A stable colour for a bot whose roster row did not carry one. Derived
    /// from the id so it never changes between launches.
    static func derived(from seed: String) -> Color {
        let hues: [Double] = [0.47, 0.72, 0.09, 0.60, 0.85, 0.33]
        let index = abs(seed.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) }) % hues.count
        return Color(hue: hues[index], saturation: 0.55, brightness: 0.78)
    }

    var hexRGB: String? {
        guard let color = NSColor(self).usingColorSpace(.sRGB) else { return nil }
        return String(format: "#%02x%02x%02x",
                      Int((color.redComponent * 255).rounded()),
                      Int((color.greenComponent * 255).rounded()),
                      Int((color.blueComponent * 255).rounded()))
    }
}

/// The blob. A circle with light coming from the top left, which is the whole
/// reason it reads as a person and not as a bullet point.
struct BotAvatar: View {
    let color: Color
    var size: CGFloat = 34
    var busy = false

    var body: some View {
        Circle()
            .fill(
                LinearGradient(colors: [color.opacity(0.95), color.opacity(0.55)],
                               startPoint: .topLeading, endPoint: .bottomTrailing)
            )
            .overlay(
                Circle()
                    .fill(
                        RadialGradient(colors: [.white.opacity(0.35), .clear],
                                       center: UnitPoint(x: 0.3, y: 0.26),
                                       startRadius: 0, endRadius: size * 0.55)
                    )
            )
            .overlay(
                Circle().strokeBorder(.white.opacity(0.08), lineWidth: 0.5)
            )
            .frame(width: size, height: size)
            .overlay(alignment: .bottomTrailing) {
                if busy {
                    Circle()
                        .fill(Theme.blue)
                        .frame(width: size * 0.28, height: size * 0.28)
                        .overlay(Circle().strokeBorder(Theme.roster, lineWidth: 2))
                        .offset(x: 1, y: 1)
                }
            }
    }
}

/// A ring with a filled centre, at avatar size. Same footprint as a face so
/// every group in the roster lines up down one edge, and not a face, because
/// neither a repo nor a source is a person.
struct Dot: View {
    let color: Color
    var size: CGFloat = 34

    var body: some View {
        ZStack {
            Circle()
                .fill(color.opacity(0.16))
            Circle()
                .fill(color)
                .frame(width: size * 0.32, height: size * 0.32)
        }
        .frame(width: size, height: size)
    }
}

/// The desk's one glyph.
struct StateDot: View {
    let state: DeskState
    var size: CGFloat = 34

    var body: some View { Dot(color: Theme.color(state), size: size) }
}

/// A desk's face: the colour it keeps, with what it is doing badged on top.
///
/// Two questions, two glyphs. The disc is a pure function of the repo path (or
/// the colour a person picked for it) and never moves, so a desk is recognisable
/// across a floor of seventy; the badge in the top-right corner carries the
/// state, which is the half that changes. Drawing both into one dot is how every
/// working desk came to look like every other working desk.
///
/// Same footprint as `Dot` and `BotAvatar`, so the roster still lines up down
/// one edge.
struct DeskFace: View {
    let repo: String
    let state: DeskState
    var size: CGFloat = 34
    /// The colour a person chose, if they chose one. Handed in rather than read
    /// here, so this view stays a drawing and the store stays the store.
    var hex: String?

    private var coat: Color { Color(hex: hex ?? Faces.coat(repo: repo)) }

    var body: some View {
        BotAvatar(color: coat, size: size)
            .overlay(alignment: .topTrailing) {
                Circle()
                    .fill(Theme.color(state))
                    .frame(width: size * 0.32, height: size * 0.32)
                    // Rung against the roster it sits on, not against the face:
                    // a badge the same colour as the coat under it is a badge
                    // that disappears on exactly one desk.
                    .overlay(Circle().strokeBorder(Theme.roster, lineWidth: 2))
                    .offset(x: 2, y: -2)
            }
    }
}

/// The wall's one glyph. Three states rather than eight: something wants you,
/// something is not answering, or it is quiet.
struct MoodDot: View {
    let mood: StateRules.SectionMood
    var size: CGFloat = 34

    var body: some View { Dot(color: Theme.color(mood), size: size) }
}

/// The raised hand, drawn rather than lettered.
///
/// An SF Symbol hand renders as something a person reads as an emoji, and this
/// surface does not use emoji. A ring with a filled centre says the same thing
/// in the same language as every other dot in the room.
struct GateMark: View {
    var size: CGFloat = 11

    var body: some View {
        Circle()
            .strokeBorder(Theme.amber, lineWidth: size * 0.16)
            .background(Circle().fill(Theme.amber.opacity(0.28)))
            .overlay(Circle().fill(Theme.amber).frame(width: size * 0.34, height: size * 0.34))
            .frame(width: size, height: size)
    }
}

/// A label on an issue, or a state on a desk. Small, quiet, never shouting.
struct Pill: View {
    let text: String
    var color: Color = Theme.dim

    var body: some View {
        Text(text)
            .officeFont(size: 11)
            .foregroundStyle(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(
                Capsule().fill(color.opacity(0.14))
            )
    }
}

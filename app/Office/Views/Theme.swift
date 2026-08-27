import SwiftUI

/// The look, in one place.
///
/// Pure black, light text, and nothing else: no toolbar, no sidebar chrome, no
/// borders drawn for their own sake. The only lines on screen are the ones that
/// separate two things a person actually reads apart.
///
/// System font throughout. A chat app that ships its own typeface is a chat app
/// that looks like every other one.
enum Theme {
    static let ink = Color.black
    static let roster = Color(white: 0.055)
    static let raised = Color(white: 0.105)
    static let selected = Color(white: 0.165)
    static let hairline = Color(white: 0.13)

    static let text = Color(white: 0.94)
    static let dim = Color(white: 0.54)
    static let faint = Color(white: 0.36)

    static let amber = Color(hex: "#ffb020")
    static let red = Color(hex: "#ff5d5d")
    static let green = Color(hex: "#39d98a")
    static let blue = Color(hex: "#4c8dff")

    static let rosterWidth: CGFloat = 296

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
}

extension Color {
    /// `#rrggbb`, the way `_meta/bots.json` writes it. An unreadable colour is
    /// not a crash and not a blank: it falls back to something visible.
    init(hex: String, fallback: Color = Color(white: 0.42)) {
        var raw = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if raw.hasPrefix("#") { raw.removeFirst() }
        guard raw.count == 6, let value = UInt64(raw, radix: 16) else {
            self = fallback
            return
        }
        self.init(.sRGB,
                  red: Double((value >> 16) & 0xff) / 255,
                  green: Double((value >> 8) & 0xff) / 255,
                  blue: Double(value & 0xff) / 255)
    }

    /// A stable colour for a bot whose roster row did not carry one. Derived
    /// from the id so it never changes between launches.
    static func derived(from seed: String) -> Color {
        let hues: [Double] = [0.47, 0.72, 0.09, 0.60, 0.85, 0.33]
        let index = abs(seed.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) }) % hues.count
        return Color(hue: hues[index], saturation: 0.55, brightness: 0.78)
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
    let hex: String
    var size: CGFloat = 34

    var body: some View {
        ZStack {
            Circle()
                .fill(Color(hex: hex).opacity(0.16))
            Circle()
                .fill(Color(hex: hex))
                .frame(width: size * 0.32, height: size * 0.32)
        }
        .frame(width: size, height: size)
    }
}

/// The desk's one glyph.
struct StateDot: View {
    let state: DeskState
    var size: CGFloat = 34

    var body: some View { Dot(hex: state.hex, size: size) }
}

/// The wall's one glyph. Three states rather than eight: something wants you,
/// something is not answering, or it is quiet.
struct MoodDot: View {
    let mood: StateRules.SectionMood
    var size: CGFloat = 34

    var body: some View { Dot(hex: mood.hex, size: size) }
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
            .font(.system(size: 11))
            .foregroundStyle(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(
                Capsule().fill(color.opacity(0.14))
            )
    }
}

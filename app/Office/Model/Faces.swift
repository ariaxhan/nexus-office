import Foundation
import Observation

/// A face for every desk: a colour it keeps, rather than a colour it borrows
/// from whatever it happens to be doing.
///
/// Until now the roster drew one dot per desk in the state's colour, so every
/// working desk looked like every other working desk and no row had an identity
/// at all. Status answers "what is it doing"; a face answers "which desk is
/// this", and one glyph cannot answer both.
///
/// **The derivation is the one the 3d room already used.** It came out of
/// `src/names.js`, which was deleted in `fcfbba0` when the web room went away:
/// FNV-1a over the repo path, one of fourteen coats, one of seventy-two names.
/// Reusing the arithmetic rather than inventing a new one means a desk wears the
/// same colour it wore as a villager, and there is no artwork to ship: the coat
/// IS the character.
///
/// Foundation and Observation only, so it proves out with no app host, for the
/// same reason `StateRules` and `Palette` are.
public enum Faces {

    /// Warm, saturated, distinguishable at small size. No two adjacent hues.
    /// The same fourteen the villagers wore, as the same strings.
    public static let coats = [
        "#f2946b", "#7fc7a4", "#f4c95d", "#9db6f0", "#e58a9e", "#a8d38d", "#c9a2e0",
        "#6fc4d6", "#f0a3c0", "#d9b382", "#8fb3e0", "#eda36f", "#8fd6c0", "#dda0dd",
    ]

    /// Every villager name, kept because a character you recognise is easier to
    /// track across visits than a string. Shown next to the picker, so a person
    /// choosing a colour knows which resident they are dressing.
    public static let names = [
        "Pumpkin", "Biscuit", "Marlow", "Tansy", "Juniper", "Waffles", "Pepper", "Mochi",
        "Clove", "Barley", "Fennel", "Tuppence", "Nutmeg", "Bramble", "Olive", "Cinder",
        "Poppy", "Dumpling", "Sorrel", "Hazel", "Pickle", "Maple", "Cricket", "Thistle",
        "Wren", "Custard", "Bunty", "Sage", "Pip", "Gumdrop", "Rosemary", "Toast",
        "Marzipan", "Beetle", "Ferngully", "Plum", "Nibs", "Cardamom", "Truffle", "Bo",
        "Perry", "Quill", "Saffron", "Bumble", "Cobweb", "Doodle", "Elmer", "Fig",
        "Gruff", "Halo", "Inky", "Jamboree", "Kipper", "Lolly", "Mittens", "Noodle",
        "Otto", "Parsnip", "Quince", "Rhubarb", "Scout", "Tilly", "Umber", "Violet",
        "Whisk", "Yarrow", "Zephyr", "Apricot", "Bandit", "Comfrey", "Dandy", "Ember",
    ]

    /// FNV-1a, 32 bit, written out rather than reached for.
    ///
    /// Swift's own `Hasher` is seeded per process, so a face derived from
    /// `hashValue` would be a different colour every launch and would change it
    /// silently. `Reactions.fingerprint` writes the 64 bit one out for the same
    /// reason; this is the 32 bit one the JS used, digit for digit, because a
    /// wider hash would pick different coats and every desk would change colour
    /// on the way over from the 3d room.
    static func hash(_ text: String) -> UInt32 {
        var value: UInt32 = 0x811c_9dc5
        for unit in text.utf16 {
            value ^= UInt32(unit)
            value = value &* 0x0100_0193
        }
        return value
    }

    /// The colour this desk is born with. A pure function of the repo path, so
    /// the same desk is the same colour on every machine and after every
    /// relaunch, with nothing stored anywhere.
    public static func coat(repo: String) -> String {
        coats[Int(hash(repo) >> 8) % coats.count]
    }

    /// The resident's name. Not drawn on the row — the thing a person scans for
    /// is always a repo — but it is what the picker is dressing.
    public static func name(repo: String) -> String {
        names[Int(hash(repo)) % names.count]
    }

    /// `#rrggbb`, lowercased, or `nil` for anything that is not six hex digits.
    ///
    /// The picker accepts what a person typed and does not clamp it to pass a
    /// contrast check: a face is a disc and never a word, so no sentence in this
    /// app is drawn in it and `Palette.legibility` does not read it. The state
    /// is still written out under the name in a colour that IS checked, which is
    /// why a face nobody can see is a face that looks wrong rather than a row
    /// that cannot be read.
    public static func normalise(hex: String) -> String? {
        var raw = hex.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if raw.hasPrefix("#") { raw.removeFirst() }
        guard raw.count == 6, raw.allSatisfy(\.isHexDigit) else { return nil }
        return "#" + raw
    }
}

/// The faces a person changed, and nothing else's business.
///
/// **Local only, on purpose,** exactly like `Reactions`: a face colour never
/// reaches the door, the pipeline or GitHub, because there is no consumer for it
/// on the other side. Losing it costs one hash away from the colour it started
/// as.
@MainActor
@Observable
public final class FaceBook {
    /// repo → `#rrggbb`. Only the desks somebody actually dressed.
    private var chosen: [String: String] = [:]

    /// Where it persists, or `nil` to keep it in memory and never write. The
    /// demo floor and the tests both pass `nil`: a screenshot run opens the real
    /// app on this machine, and a harness that could recolour Aria's desks would
    /// be a check that damages the thing it checks.
    private let defaults: UserDefaults?

    private static let storeKey = "faces.v1"

    public static let shared = FaceBook(defaults: Reactions.isDemoRun ? nil : .standard)

    public init(defaults: UserDefaults? = .standard) {
        self.defaults = defaults
        if let raw = defaults?.dictionary(forKey: Self.storeKey) as? [String: String] {
            for (repo, hex) in raw {
                if let clean = Faces.normalise(hex: hex) { chosen[repo] = clean }
            }
        }
    }

    /// What to draw: the chosen colour, or the one the desk was born with.
    public func hex(repo: String) -> String {
        chosen[repo] ?? Faces.coat(repo: repo)
    }

    /// Whether this desk is wearing something a person picked.
    public func isChosen(repo: String) -> Bool { chosen[repo] != nil }

    /// Dress a desk. An unparseable string is refused rather than stored, so a
    /// half-typed hex in the field never lands as a colour.
    @discardableResult
    public func choose(repo: String, hex: String) -> Bool {
        guard let clean = Faces.normalise(hex: hex) else { return false }
        chosen[repo] = clean
        save()
        return true
    }

    /// Back to the coat the villager was wearing.
    public func reset(repo: String) {
        chosen.removeValue(forKey: repo)
        save()
    }

    private func save() {
        defaults?.set(chosen, forKey: Self.storeKey)
    }
}

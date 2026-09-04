import Foundation
import Observation

/// How much of the office is on screen at once.
///
/// Three, because there are exactly three questions a person asks of this
/// window and no fourth one worth a control: *what is this desk doing*, *how do
/// these two desks differ*, and *leave me alone, I only want the floor*.
///
/// - `focus`: the roster and one detail pane. What this app has always been.
/// - `compare`: the roster and TWO detail panes, so two desks are open at once
///   and a desk dragged out of the roster lands in whichever half it was
///   dropped on. The reason the whole preset exists: reading one desk, going to
///   another, and coming back is not comparing, it is remembering.
/// - `minimal`: the roster alone, filling the window, with no detail pane at
///   all. The narrowest sensible framing: the floor is the whole point of
///   glancing at this window, and a detail pane is a thing you open on purpose.
public enum LayoutPreset: String, CaseIterable, Identifiable, Sendable {
    case focus, compare, minimal

    public var id: String { rawValue }

    public var label: String {
        switch self {
        case .focus: return "Focus"
        case .compare: return "Compare"
        case .minimal: return "Minimal"
        }
    }
}

/// Whether this window follows macOS or keeps a chosen room appearance.
public enum AppearancePreset: String, CaseIterable, Identifiable, Sendable {
    case system, light, dark

    public var id: String { rawValue }

    public var label: String {
        switch self {
        case .system: return "System"
        case .light: return "Light"
        case .dark: return "Dark"
        }
    }
}

public enum FontPreset: String, CaseIterable, Identifiable, Sendable {
    case system, rounded, serif
    public var id: String { rawValue }
    public var label: String { rawValue.capitalized }
}

/// The choices a person made about how the floor looks, kept between launches.
///
/// Everything in here is a view of the same office: an order and a filter.
/// Nothing here changes what is polled, what a desk says, or what the door
/// accepts, which is the reason this can live on this machine and never travel.
///
/// **Not the server's config.** The 23 tunables `office-sync.py` reads are env
/// vars on purpose: the trusted hosts and the webhook secret are the auth
/// boundary on the one public path, and the poll interval is the GitHub budget.
/// A window that could edit those would be a door weakening its own lock. This
/// file holds preferences and no settings at all.
///
/// Foundation and Observation only, so it proves out with no app host.
@MainActor
@Observable
public final class Preferences {
    /// Where it persists, or `nil` to keep it in memory and never write.
    ///
    /// The tests and the demo floor both pass `nil`, for the same reason
    /// `Reactions` does: a shoot walks framings by setting `needsOnly`, and a
    /// harness that could leave a real person's roster filtered is a check that
    /// damages the thing it checks.
    private let defaults: UserDefaults?

    private static let sortKey = "settings.deskSort.v1"
    private static let needsOnlyKey = "settings.needsOnly.v1"
    private static let layoutKey = "settings.layout.v1"
    private static let appearanceKey = "settings.appearance.v1"
    static let lightCanvasKey = "settings.canvas.light.v1"
    static let darkCanvasKey = "settings.canvas.dark.v1"
    private static let fontPresetKey = "settings.fontPreset.v1"
    private static let botsKey = "settings.pane.bots.v1"
    private static let wallKey = "settings.pane.wall.v1"
    private static let typeScaleKey = "settings.typeScale.v1"
    private static let homeSeenKey = "settings.home.lastSeen.v1"
    /// The key the faces already shipped under, kept as it was: renaming it
    /// would silently strip every colour somebody has already picked.
    private static let facesKey = "faces.v1"

    /// The one the real app uses.
    public static let shared = Preferences(defaults: Reactions.isDemoRun ? nil : .standard)

    public private(set) var deskSort: StateRules.DeskSort = .owner
    public private(set) var needsOnly = false
    public private(set) var layout: LayoutPreset = .focus
    public private(set) var appearance: AppearancePreset = .system
    public private(set) var lightCanvas = Palette.defaultLightCanvas
    public private(set) var darkCanvas = Palette.defaultDarkCanvas
    public private(set) var fontPreset: FontPreset = .system
    /// Which groups of the roster are drawn. Both default to on, and neither is
    /// allowed to hide a raised hand: a gate comes from the runtime and opens a
    /// sheet over the whole window, so turning a group off cannot bury one.
    public private(set) var showBots = true
    public private(set) var showWall = true
    /// How big the reading type is, as a multiplier on every size a page
    /// draws at. Cmd + and Cmd - step it; Cmd 0 puts it back. Persisted,
    /// because a person who needs bigger type needs it every day.
    public private(set) var typeScale: Double = 1
    /// repo -> `#rrggbb`, only the desks somebody actually dressed. `FaceBook`
    /// reads and writes it through here so there is one place that decides what
    /// a preference is and where it lands.
    public private(set) var faceOverrides: [String: String] = [:]
    /// When this person last opened the home. Local to this Mac and to this
    /// person: nothing on the server reads it, no other device sees it, and it
    /// is the only thing "since you were last here" is measured from. `nil` is
    /// an install that has never opened it, which is honestly nothing to count
    /// over rather than a licence to count everything.
    public private(set) var homeLastSeen: Date?

    public init(defaults: UserDefaults? = nil) {
        self.defaults = defaults
        guard let defaults else { return }
        // An order this version does not know is a value from a newer one, and
        // guessing which of the five it meant would silently reorder the floor.
        // Falling back to the default is the honest read.
        if let raw = defaults.string(forKey: Self.sortKey),
           let order = StateRules.DeskSort(rawValue: raw) {
            deskSort = order
        }
        // This filter used to persist across launches and could make a healthy
        // floor look empty. It no longer has a UI; discard the stale choice.
        defaults.removeObject(forKey: Self.needsOnlyKey)
        // Same honest read as the order above: a preset from a newer version is
        // a word this one cannot draw, and picking one of the three at random
        // rearranges the window into a shape nobody chose.
        if let raw = defaults.string(forKey: Self.layoutKey),
           let preset = LayoutPreset(rawValue: raw) {
            layout = preset
        }
        appearance = Self.appearance(defaults.string(forKey: Self.appearanceKey))
        lightCanvas = Self.canvas(defaults.string(forKey: Self.lightCanvasKey), fallback: Palette.defaultLightCanvas)
        darkCanvas = Self.canvas(defaults.string(forKey: Self.darkCanvasKey), fallback: Palette.defaultDarkCanvas)
        fontPreset = defaults.string(forKey: Self.fontPresetKey).flatMap(FontPreset.init(rawValue:)) ?? .system
        // `bool(forKey:)` cannot tell "off" from "never set", and these two
        // default to ON, so an untouched install would come up with an empty
        // roster if this were read the way `needsOnly` is.
        if let bots = defaults.object(forKey: Self.botsKey) as? Bool { showBots = bots }
        if let wall = defaults.object(forKey: Self.wallKey) as? Bool { showWall = wall }
        // Clamped on the way in as well as on the way out: a value written by
        // hand or by a newer build must not come up as unreadable type.
        if let scale = defaults.object(forKey: Self.typeScaleKey) as? Double {
            typeScale = Self.clamp(scale)
        }
        homeLastSeen = defaults.object(forKey: Self.homeSeenKey) as? Date
        if let raw = defaults.dictionary(forKey: Self.facesKey) as? [String: String] {
            for (repo, hex) in raw {
                if let clean = Faces.normalise(hex: hex) { faceOverrides[repo] = clean }
            }
        }
    }

    public func set(deskSort: StateRules.DeskSort) {
        self.deskSort = deskSort
        defaults?.set(deskSort.rawValue, forKey: Self.sortKey)
    }

    public func set(needsOnly: Bool) {
        self.needsOnly = needsOnly
    }

    public func set(layout: LayoutPreset) {
        self.layout = layout
        defaults?.set(layout.rawValue, forKey: Self.layoutKey)
    }

    public func set(appearance: AppearancePreset) {
        self.appearance = appearance
        defaults?.set(appearance.rawValue, forKey: Self.appearanceKey)
    }

    private static func appearance(_ raw: String?) -> AppearancePreset {
        raw.flatMap(AppearancePreset.init(rawValue:)) ?? .system
    }

    private static func canvas(_ raw: String?, fallback: String) -> String {
        raw.flatMap(Faces.normalise(hex:)) ?? fallback
    }

    public func set(lightCanvas: String) {
        guard let clean = Faces.normalise(hex: lightCanvas) else { return }
        self.lightCanvas = clean
        defaults?.set(clean, forKey: Self.lightCanvasKey)
    }

    public func set(darkCanvas: String) {
        guard let clean = Faces.normalise(hex: darkCanvas) else { return }
        self.darkCanvas = clean
        defaults?.set(clean, forKey: Self.darkCanvasKey)
    }

    public func set(fontPreset: FontPreset) {
        self.fontPreset = fontPreset
        defaults?.set(fontPreset.rawValue, forKey: Self.fontPresetKey)
    }

    public func set(homeLastSeen: Date?) {
        self.homeLastSeen = homeLastSeen
        if let homeLastSeen {
            defaults?.set(homeLastSeen, forKey: Self.homeSeenKey)
        } else {
            defaults?.removeObject(forKey: Self.homeSeenKey)
        }
    }

    public func set(showBots: Bool) {
        self.showBots = showBots
        defaults?.set(showBots, forKey: Self.botsKey)
    }

    public func set(showWall: Bool) {
        self.showWall = showWall
        defaults?.set(showWall, forKey: Self.wallKey)
    }

    /// The steps Cmd + and Cmd - walk. Eight sizes from four fifths to twice,
    /// and no fractional ones in between: a scale a person cannot land on twice
    /// is a scale nobody can describe to somebody else.
    public static let typeScales: [Double] = [0.8, 0.9, 1, 1.1, 1.25, 1.4, 1.6, 1.8, 2]

    static func clamp(_ scale: Double) -> Double {
        guard scale.isFinite else { return 1 }
        return min(max(scale, typeScales.first!), typeScales.last!)
    }

    public func set(typeScale: Double) {
        self.typeScale = Self.clamp(typeScale)
        defaults?.set(self.typeScale, forKey: Self.typeScaleKey)
    }

    /// One step bigger (`+1`) or smaller (`-1`); past the end stays put.
    public func stepTypeScale(_ direction: Int) {
        let scales = Self.typeScales
        let nearest = scales.indices.min { abs(scales[$0] - typeScale) < abs(scales[$1] - typeScale) } ?? 2
        let next = min(max(nearest + (direction > 0 ? 1 : -1), 0), scales.count - 1)
        set(typeScale: scales[next])
    }

    /// Dress a desk. An unparseable string is refused rather than stored, so a
    /// half-typed hex never lands as a colour.
    @discardableResult
    public func set(face hex: String, for repo: String) -> Bool {
        guard let clean = Faces.normalise(hex: hex) else { return false }
        faceOverrides[repo] = clean
        saveFaces()
        return true
    }

    /// Back to the coat the desk was born with.
    public func clearFace(repo: String) {
        faceOverrides.removeValue(forKey: repo)
        saveFaces()
    }

    private func saveFaces() {
        defaults?.set(faceOverrides, forKey: Self.facesKey)
    }
}

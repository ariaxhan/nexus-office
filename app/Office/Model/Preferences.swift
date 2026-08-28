import Foundation
import Observation

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

    /// The one the real app uses.
    public static let shared = Preferences(defaults: Reactions.isDemoRun ? nil : .standard)

    public private(set) var deskSort: StateRules.DeskSort = .owner
    public private(set) var needsOnly = false

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
        needsOnly = defaults.bool(forKey: Self.needsOnlyKey)
    }

    public func set(deskSort: StateRules.DeskSort) {
        self.deskSort = deskSort
        defaults?.set(deskSort.rawValue, forKey: Self.sortKey)
    }

    public func set(needsOnly: Bool) {
        self.needsOnly = needsOnly
        defaults?.set(needsOnly, forKey: Self.needsOnlyKey)
    }
}

import Foundation

/// `/api/reports`: the newest daily report from each bot.
public struct ReportsResponse: Decodable {
    public var reports: [BotReport]
}

public struct BotReport: Decodable, Identifiable, Hashable {
    public var bot: String
    public var name: String?
    public var at: String?
    public var text: String?
    public var ok: Bool?
    public var error: String?
    public var id: String { bot }
    public var title: String { name ?? bot.capitalized }
}

/// `/api/coordinators`: one row per coordinator the Office watches.
public struct CoordinatorsResponse: Decodable {
    public var coordinators: [CoordinatorRow]
}

public struct CoordinatorEnd: Decodable, Hashable {
    public var at: String?
    public var rc: Int?
    public var secs: Int?
    public var timed_out: Int?
}

public struct CoordinatorCommit: Decodable, Hashable, Identifiable {
    public var checkout: String
    public var sha: String
    public var at: String
    public var subject: String
    public var id: String { sha }
}

public struct CoordinatorRow: Decodable, Identifiable, Hashable {
    public var id: String
    public var name: String
    public var health: String
    public var live: Bool?
    public var age_s: Int?
    public var last_end: CoordinatorEnd?
    public var shipped_recent: [Int]?
    public var working_on: String?
    public var lanes: [String]?
    public var commits: [CoordinatorCommit]?
    public var unread: Int?
    public var error: String?

    public var healthy: Bool { ["ok", "running"].contains(health) }

    /// The same line the phone page draws, so both windows say one thing.
    public var line: String {
        let words = ["running": "Running", "ok": "Healthy", "thrashing": "Thrashing",
                     "failing": "Last run failed", "stalled": "Stalled", "error": "Unreadable"]
        var parts = [words[health] ?? health]
        parts.append(live == true ? "run live now" : "last run \(CoordinatorRow.age(age_s))")
        if let rc = last_end?.rc { parts.append("exit \(rc)" + ((last_end?.timed_out ?? 0) != 0 ? " (timed out)" : "")) }
        if let shipped = shipped_recent, !shipped.isEmpty {
            parts.append("shipped per run: " + shipped.map(String.init).joined(separator: " · "))
        }
        if let unread, unread > 0 { parts.append("\(unread) unread") }
        return parts.joined(separator: " · ")
    }

    static func age(_ seconds: Int?) -> String {
        guard let s = seconds else { return "never" }
        if s < 90 { return "just now" }
        if s < 5400 { return "\(s / 60) min ago" }
        if s < 172_800 { return "\(s / 3600) h ago" }
        return "\(s / 86400) d ago"
    }
}

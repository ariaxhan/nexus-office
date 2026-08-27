import Foundation

/// What a desk is, decided once.
///
/// Ported from `src/main.js:117-131` and `src/ui/panel.js:40-42` without
/// changing a single branch. The room and the app must never be able to
/// disagree about whether something needs a person, so this is the same
/// sentence written in another language, not a second opinion on it.
///
/// Foundation only, deliberately. Nothing in here may reach for a colour, a
/// view, or the network, which is what lets the whole file be tested headlessly.

public enum DeskState: String, CaseIterable {
    case gated, waiting, locked, parked, refused, landed, working, idle

    /// What a person reads on the row. Lower case on purpose: these are states,
    /// not headlines.
    public var label: String {
        switch self {
        case .gated: return "asking permission"
        case .waiting: return "waiting on you"
        case .locked: return "no push access"
        case .parked: return "parked"
        case .refused: return "refused"
        case .landed: return "landed a PR"
        case .working: return "working"
        case .idle: return "quiet"
        }
    }

    /// The dot. Amber shouts, red asks, blue works, green landed, grey rests.
    public var hex: String {
        switch self {
        case .gated: return "#ffb020"
        case .waiting: return "#ff5d5d"
        case .locked: return "#4a4a52"
        case .parked: return "#6b6b78"
        case .refused: return "#ff8c42"
        case .landed: return "#39d98a"
        case .working: return "#4c8dff"
        case .idle: return "#3a3a42"
        }
    }

    /// Whether a person has to do something. Drives the menu bar dot and the
    /// "needs me" filter, and nothing else.
    public var needsAPerson: Bool { self == .gated || self == .waiting }
}

public enum StateRules {

    // MARK: - the one ordering

    /// One ordering, written once.
    ///
    /// A repo that both landed a PR and is blocked on a question is blocked: the
    /// thing a person has to do always wins the desk. A GATE outranks even that,
    /// because an agent is sitting there with a clock running while an unanswered
    /// issue simply waits.
    public static func deskState(station: Station, gate: Gate?) -> DeskState {
        if let gate, gate.isPending { return .gated }
        if station.issues.contains(where: { needsHuman(issue: $0) }) { return .waiting }
        if station.access == false { return .locked }
        if station.outcome == "parked" { return .parked }
        if station.outcome == "refused" { return .refused }
        if station.outcome == "landed" { return .landed }
        if !station.issues.isEmpty { return .working }
        return .idle
    }

    /// Convenience for a station that already carries its gate.
    public static func deskState(_ station: Station) -> DeskState {
        deskState(station: station, gate: station.gate)
    }

    // MARK: - waiting on you

    /// The runner's own rule, not a second opinion on it: an issue is waiting on
    /// a person exactly when the bot had the last word.
    ///
    /// `bot_last` is computed from the comments themselves and is the whole
    /// answer. Anything else, a missing field or a null, is the snapshot
    /// declining to say, and a snapshot that does not say is not evidence that
    /// somebody is blocked. Labels are hints a human types and they go stale
    /// behind the truth, so they never get a vote here.
    public static func needsHuman(issue: Issue) -> Bool {
        issue.botLast == true
    }

    public static func waitingCount(_ stations: [Station]) -> Int {
        stations.reduce(0) { $0 + $1.issues.filter { needsHuman(issue: $0) }.count }
    }

    // MARK: - the gate needs a desk to stand at

    /// Attach a pending gate to the station it belongs to.
    ///
    /// The runtime is not a repo. The gate goes to the station whose repo shares
    /// a name with the runtime root when there is one, and otherwise gets a desk
    /// of its own rather than being dropped on the floor.
    ///
    /// Applying this twice must leave the same floor. The gate poll is five
    /// times faster than the world poll, so it runs against a list that already
    /// holds the desk this made last time, and a second desk of its own every
    /// two seconds would be a roster that grows while you watch it.
    public static func attachGate(stations: [Station], runtime: RuntimeInfo?, gate: Gate?) -> [Station] {
        var out = stations
        guard let gate, gate.isPending else {
            for index in out.indices { out[index].gate = nil }
            return out
        }
        let rootName = (runtime?.root ?? "").split(separator: "/").last.map(String.init) ?? ""
        var hostIndex = out.firstIndex { $0.shortName == rootName && !rootName.isEmpty }
            ?? out.firstIndex { $0.synthetic }
        if hostIndex == nil {
            let repo = rootName.isEmpty ? "runtime/agent" : "runtime/\(rootName)"
            out.append(Station(repo: repo, access: true,
                               detail: "the local agent runtime", synthetic: true))
            hostIndex = out.indices.last
        }
        for index in out.indices { out[index].gate = index == hostIndex ? gate : nil }
        return out
    }

    /// Which desks show the raised hand.
    ///
    /// A gate carries a command, not a repo, so where it belongs has to be read
    /// off what it does say. In order: a desk the gate names outright, then the
    /// desk the runtime root put it at, then, when neither answers, **every**
    /// desk. The last one is the whole point. A gate the app cannot place is
    /// shown everywhere rather than nowhere, because a raised hand nobody can
    /// find is the one failure this surface is not allowed to have.
    public static func gateDesks(gate: Gate?, stations: [Station]) -> Set<String> {
        guard let gate, gate.isPending else { return [] }

        let named = stations.filter { names(gate, station: $0) }
        if !named.isEmpty { return Set(named.map(\.repo)) }

        let hosts = stations.filter { $0.gate?.id == gate.id }
        if !hosts.isEmpty { return Set(hosts.map(\.repo)) }

        return Set(stations.map(\.repo))
    }

    /// Does the gate say this desk's name out loud?
    ///
    /// The full `owner/name` counts anywhere. A bare short name has to stand on
    /// its own as a word and be long enough to mean something, because a desk
    /// called `northwind/api` must not claim every command with the word "api"
    /// in it: a wrong desk is worse than no desk, since it takes the gate off
    /// the right one.
    private static func names(_ gate: Gate, station: Station) -> Bool {
        let haystack = (gate.target + " " + gate.detail).lowercased()
        if haystack.contains(station.repo.lowercased()) { return true }
        let short = station.shortName.lowercased()
        guard short.count >= 4 else { return false }
        return contains(word: short, in: haystack)
    }

    /// `word` present with something that is not a letter or a digit on each
    /// side of it, so `api` does not match `capital` and `docs` does not match
    /// `docstring`.
    private static func contains(word: String, in haystack: String) -> Bool {
        var searched = haystack[...]
        while let found = searched.range(of: word) {
            let before = found.lowerBound == haystack.startIndex
                ? nil : haystack[haystack.index(before: found.lowerBound)]
            let after = found.upperBound == haystack.endIndex ? nil : haystack[found.upperBound]
            if edge(before) && edge(after) { return true }
            searched = haystack[found.upperBound...]
        }
        return false
    }

    private static func edge(_ character: Character?) -> Bool {
        guard let character else { return true }
        return !character.isLetter && !character.isNumber
    }

    // MARK: - what is on the roster

    /// Which desks are in the list right now.
    ///
    /// Two rules it must never break. Filtering is a VIEW and never touches what
    /// the runner does. And **a gate is never hidden**: no search string and no
    /// filter removes a raised hand from the room, because losing a blocked agent
    /// behind a view is the one failure this surface cannot be allowed to have.
    public static func visibleDesks(_ stations: [Station],
                                    query: String = "",
                                    needsOnly: Bool = false) -> [Station] {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return stations.filter { station in
            if station.gate?.isPending == true { return true }
            if needsOnly && !deskState(station).needsAPerson { return false }
            if needle.isEmpty { return true }
            return station.repo.lowercased().contains(needle)
                || station.detail.lowercased().contains(needle)
        }
    }

    public static func visibleBots(_ bots: [Bot], query: String = "") -> [Bot] {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if needle.isEmpty { return bots }
        return bots.filter {
            $0.name.lowercased().contains(needle)
                || $0.id.lowercased().contains(needle)
                || lastLine(bot: $0).lowercased().contains(needle)
        }
    }

    // MARK: - the second line of a roster row

    /// The one line under a bot's name: its last message, flattened.
    ///
    /// A roster row is one line tall. A reply with newlines in it would otherwise
    /// either push every other row down or get clipped mid-glyph, so it is
    /// collapsed and cut here rather than left to the layout to survive.
    public static func lastLine(bot: Bot, limit: Int = 78) -> String {
        line(bot.last?.content ?? "", limit: limit)
    }

    public static func line(_ raw: String, limit: Int = 78) -> String {
        let flat = raw.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard limit > 0, flat.count > limit else { return flat }
        let cut = flat.prefix(limit).trimmingCharacters(in: .whitespaces)
        return cut + "\u{2026}"
    }

    // MARK: - clocks

    private static let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    public static func date(_ isoString: String) -> Date? {
        if isoString.isEmpty { return nil }
        if let d = iso.date(from: isoString) { return d }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: isoString)
    }

    /// The right hand side of a roster row, the way a chat app writes it:
    /// a clock today, "Yesterday" yesterday, a date before that.
    public static func stamp(_ isoString: String, now: Date = Date(),
                             calendar: Calendar = .current) -> String {
        guard let when = date(isoString) else { return "" }
        let formatter = DateFormatter()
        if calendar.isDate(when, inSameDayAs: now) {
            formatter.dateFormat = "h:mm a"
        } else if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
                  calendar.isDate(when, inSameDayAs: yesterday) {
            return "Yesterday"
        } else {
            formatter.dateFormat = "MMM d"
        }
        return formatter.string(from: when)
    }

    /// How long the hand has been up, said out loud. A gate's whole value is the
    /// clock running on it.
    public static func waited(seconds: Double) -> String {
        let total = max(0, Int(seconds.rounded()))
        if total < 60 { return "\(total)s" }
        if total < 3600 { return "\(total / 60)m \(total % 60)s" }
        return "\(total / 3600)h \((total % 3600) / 60)m"
    }
}

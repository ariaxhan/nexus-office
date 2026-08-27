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
    /// off what the runtime knows first and what the command says second. In
    /// order: the desk the runtime root put it at (where the agent actually is),
    /// then a desk the command names outright (a path argument is a guess, and
    /// `cp ~/acme/docs/x ~/acme/website/` must not move the hand off the desk
    /// the agent is working at), then, when neither answers, **every** desk.
    /// The last one is the whole point. A gate the app cannot place is shown
    /// everywhere rather than nowhere, because a raised hand nobody can find is
    /// the one failure this surface is not allowed to have.
    public static func gateDesks(gate: Gate?, stations: [Station]) -> Set<String> {
        guard let gate, gate.isPending else { return [] }

        let hosts = stations.filter { $0.gate?.id == gate.id }
        if !hosts.isEmpty { return Set(hosts.map(\.repo)) }

        let named = stations.filter { names(gate, station: $0) }
        if !named.isEmpty { return Set(named.map(\.repo)) }

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
    /// Three rules it must never break. Filtering is a VIEW and never touches
    /// what the runner does. **A gate is never hidden**: no search string, no
    /// filter and no "put this away" removes a raised hand from the room,
    /// because losing a blocked agent behind a view is the one failure this
    /// surface cannot be allowed to have. And putting a desk away is a filter
    /// like the other two, so it obeys the same escape hatch.
    public static func visibleDesks(_ stations: [Station],
                                    query: String = "",
                                    needsOnly: Bool = false,
                                    isHidden: (Station) -> Bool = { $0.hidden }) -> [Station] {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return stations.filter { station in
            if station.gate?.isPending == true { return true }
            if isHidden(station) { return false }
            if needsOnly && !deskState(station).needsAPerson { return false }
            if needle.isEmpty { return true }
            return station.repo.lowercased().contains(needle)
                || station.detail.lowercased().contains(needle)
        }
    }

    /// The desks a person put away, in the order the roster lists everything.
    ///
    /// Never touched by the search box or by "needs me". A put-away desk is out
    /// of the way, not out of the building, and the way it stays findable is
    /// that its section always holds every one of them. A desk with a raised
    /// hand is not in here at all: it is back up in the list.
    public static func putAwayDesks(_ stations: [Station],
                                    isHidden: (Station) -> Bool = { $0.hidden }) -> [Station] {
        stations.filter { isHidden($0) && $0.gate?.isPending != true }
    }

    /// **Hidden is never silent.** How many put-away desks are waiting on a
    /// person right now, so the collapsed header can say so instead of a person
    /// having to open it to find out.
    public static func putAwayNeedingAPerson(_ stations: [Station],
                                             isHidden: (Station) -> Bool = { $0.hidden }) -> Int {
        putAwayDesks(stations, isHidden: isHidden).filter { deskState($0).needsAPerson }.count
    }

    /// The header on a section a person can leave shut: what is in it, and
    /// whether anything in it needs them.
    public static func putAwayHeadline(_ stations: [Station],
                                       isHidden: (Station) -> Bool = { $0.hidden }) -> String {
        let away = putAwayDesks(stations, isHidden: isHidden).count
        let waiting = putAwayNeedingAPerson(stations, isHidden: isHidden)
        if waiting == 0 { return "put away (\(away))" }
        return "put away (\(away)) \u{00b7} \(waiting) needs you"
    }

    /// The count under the desks header. Put away means not polled, so this is
    /// the honest denominator: how much of the floor the server is actually
    /// asking GitHub about.
    public static func polledLine(_ stations: [Station],
                                  isHidden: (Station) -> Bool = { $0.hidden }) -> String {
        let all = stations.filter { !$0.synthetic }
        let polled = all.filter { !isHidden($0) }.count
        return "\(polled) of \(all.count) polled"
    }

    // MARK: - the wall

    /// How a wall row is drawn, in the same three-way language as a desk.
    ///
    /// Needing a person outranks being broken, exactly as it does on a desk: a
    /// source that is stale AND has five things waiting is a source you have to
    /// go and look at, and the reason it is stale can wait until you are there.
    public enum SectionMood: String {
        case needs, off, quiet

        public var hex: String {
            switch self {
            case .needs: return "#ffb020"
            case .off: return "#4a4a52"
            case .quiet: return "#3a3a42"
            }
        }
    }

    public static func mood(_ section: Section) -> SectionMood {
        if section.needs > 0 { return .needs }
        return section.isOK ? .quiet : .off
    }

    /// The tone a source put on one fact.
    ///
    /// A closed set on purpose. Anything else the source writes reads as
    /// `plain`, because a tone nobody defined must not be able to paint a
    /// number a colour that means something it does not.
    public enum SectionTone: String {
        case ok, warn, bad, dim, plain

        public static func read(_ raw: String) -> SectionTone {
            SectionTone(rawValue: raw.trimmingCharacters(in: .whitespaces).lowercased()) ?? .plain
        }
    }

    public static func tone(_ fact: SectionFact) -> SectionTone {
        SectionTone.read(fact.tone)
    }

    /// The wall's order: what wants a person, then what is not ok, then the
    /// quiet ones. Ties by title, so the list never shuffles under a person.
    ///
    /// The same shape as the desk ordering and for the same reason. A wall
    /// sorted alphabetically buries the one source that needed you behind four
    /// that did not.
    public static func sectionOrder(_ sections: [Section]) -> [Section] {
        sections.enumerated().sorted { left, right in
            let a = rank(left.element), b = rank(right.element)
            if a != b { return a < b }
            let titles = (left.element.title.lowercased(), right.element.title.lowercased())
            if titles.0 != titles.1 { return titles.0 < titles.1 }
            return left.offset < right.offset
        }.map(\.element)
    }

    private static func rank(_ section: Section) -> Int {
        if section.needs > 0 { return 0 }
        return section.isOK ? 2 : 1
    }

    /// What is on the wall right now.
    ///
    /// Search reads the two things a wall row actually shows: its name and its
    /// sentence. "needs me" keeps a source exactly when it says a person is
    /// wanted, which is the same question the filter asks of a desk.
    public static func visibleSections(_ sections: [Section],
                                       query: String = "",
                                       needsOnly: Bool = false) -> [Section] {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return sectionOrder(sections).filter { section in
            if needsOnly && section.needs == 0 { return false }
            if needle.isEmpty { return true }
            return section.title.lowercased().contains(needle)
                || section.headline.lowercased().contains(needle)
        }
    }

    /// The one line under a wall row: the source's own sentence, flattened.
    public static func sectionSubtitle(_ section: Section, limit: Int = 64) -> String {
        line(section.headline, limit: limit)
    }

    /// The badge on the right of a wall row, or nothing at all.
    ///
    /// Nothing at all rather than a zero: a column of noughts is a roster
    /// shouting that nothing is happening.
    public static func sectionBadge(_ section: Section) -> String? {
        section.needs > 0 ? String(section.needs) : nil
    }

    /// How much of the wall wants a person. Drives the menu bar dot and the
    /// "needs me" filter, exactly as an issue waiting on you does.
    public static func wallNeeds(_ sections: [Section]) -> Int {
        sections.reduce(0) { $0 + $1.needs }
    }

    /// The line beside the wall header, or nothing when nothing is wanted.
    public static func wallLine(_ sections: [Section]) -> String {
        let total = wallNeeds(sections)
        return total == 0 ? "" : "the wall needs \(total)"
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

    /// What a bot's row says under its name.
    ///
    /// The last thing it said, once it has said anything. Before that, what it
    /// is FOR. "no messages yet" is a fact about the transcript and not about
    /// the colleague, and a roster of four rows all saying it tells a person
    /// nothing about which one to open.
    public static func botSubtitle(bot: Bot, limit: Int = 64) -> String {
        let said = lastLine(bot: bot, limit: limit)
        return said.isEmpty ? line(bot.purpose, limit: limit) : said
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

    /// A point in time a person can say out loud, and place.
    ///
    /// `stamp` answers "when, roughly" for a column of them. This answers
    /// "when exactly" for the middle of a sentence, which needs the clock even
    /// when the day is not today: "showing what we had at Yesterday" is not
    /// English, and "showing what we had at 5:42 PM yesterday" is.
    public static func moment(_ isoString: String, now: Date = Date(),
                              calendar: Calendar = .current) -> String {
        guard let when = date(isoString) else { return "" }
        let clock = DateFormatter()
        clock.dateFormat = "h:mm a"
        let time = clock.string(from: when)
        if calendar.isDate(when, inSameDayAs: now) { return time }
        if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
           calendar.isDate(when, inSameDayAs: yesterday) {
            return "\(time) yesterday"
        }
        let day = DateFormatter()
        day.dateFormat = "MMM d"
        return "\(time) on \(day.string(from: when))"
    }

    // MARK: - the most recent thing we were able to pull

    /// One snapshot interval of slack.
    ///
    /// `fetched_at` is written when GitHub answers and `generated` when the
    /// snapshot finishes, so the two are never exactly equal even on a perfectly
    /// healthy pull. Without this every desk on the floor would call itself
    /// stale, and a warning that is always on is a warning nobody reads.
    private static let freshnessSlack: TimeInterval = 120

    /// Is this desk showing data older than the snapshot it arrived in?
    ///
    /// Only a `fetched_at` we can actually read counts. A missing or unparseable
    /// one is the server declining to say, and "we do not know how old this is"
    /// must not be rendered as "this is current".
    public static func isStale(station: Station, generated: String) -> Bool {
        guard let fetched = date(station.fetchedAt), let built = date(generated) else { return false }
        return built.timeIntervalSince(fetched) > freshnessSlack
    }

    /// The header's right hand phrase: "as of 5:42 PM", or nothing.
    ///
    /// Shown when the data is older than the snapshot, and shown when something
    /// went wrong reading the desk even if the clocks happen to agree, because
    /// an error means what is on screen is the previous answer either way.
    public static func asOf(station: Station, generated: String,
                            now: Date = Date(), calendar: Calendar = .current) -> String? {
        guard isStale(station: station, generated: generated) || !station.problems.isEmpty
        else { return nil }
        let when = moment(station.fetchedAt, now: now, calendar: calendar)
        return when.isEmpty ? nil : "as of \(when)"
    }

    /// The one line a desk says about why what you are reading is not current.
    ///
    /// One line, never two. The issues half and the pull requests half of a pull
    /// fail together and report the same sentence twice, and two identical red
    /// lines read as two problems rather than one, which is how a person starts
    /// looking for a second fault that was never there.
    ///
    /// A spent budget outranks the per-desk error because it explains every desk
    /// at once: while the door has stopped asking, no repo's data is current and
    /// no repo's error is news.
    ///
    /// But only on a desk that is actually behind: one fetched this build, or
    /// one put away and never fetched, has nothing to apologise for, and a
    /// notice on every desk is a notice on none.
    public static func staleNotice(station: Station, github: GitHubBudget?,
                                   generated: String = "",
                                   now: Date = Date(),
                                   calendar: Calendar = .current) -> String? {
        let had = moment(station.fetchedAt, now: now, calendar: calendar)
        let showing = had.isEmpty ? "" : "; showing what we had at \(had)"
        let behind = isStale(station: station, generated: generated) || !station.problems.isEmpty

        if let github, github.isPaused, behind {
            let until = moment(github.pausedUntil, now: now, calendar: calendar)
            let head = until.isEmpty
                ? "GitHub is out of budget"
                : "GitHub is out of budget until \(until)"
            return head + showing
        }

        let problems = station.problems
        guard !problems.isEmpty else { return nil }
        return problems.joined(separator: "; ") + showing
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

import Foundation

/// The agents actually running on this machine, per desk.
///
/// A desk in this office has always been a repo seen from the outside: issues,
/// pull requests, what the pipeline did to it. This is the same desk seen from
/// the inside, and it is the half that has always needed a terminal.
///
/// `state` is four things and never fewer, because `hcom` being absent and there
/// being nothing running are not the same room and must not draw the same:
///
///   unavailable  hcom is not installed. Nothing can be SEEN and nothing can be
///                RULED OUT. An empty list here would be a lie.
///   unreadable   hcom is there and did not answer, or said something that is
///                not JSON.
///   empty        hcom answered, and there is nothing running.
///   ok           there is something running.

public struct SessionRoster: Decodable, Equatable {
    public var state: String = "unavailable"
    public var detail: String = ""
    public var sessions: [Session] = []
    /// How many could read a message right now. Never the same as `count`: a
    /// list with three dead rows in it is not three sessions you can talk to.
    public var live: Int = 0
    public var blocked: Int = 0
    public var at: String = ""

    /// Whether the office can see anything at all. False means "we do not know",
    /// which is the one answer a roster must never render as "nothing".
    public var canSee: Bool { state == "ok" || state == "empty" }

    /// The one sentence for a desk that has no room for a list.
    public var line: String {
        switch state {
        case "unavailable", "unreadable": return detail
        case "empty": return "nothing running here"
        default: break
        }
        var said = "\(live) running"
        if blocked > 0 { said += ", \(blocked) waiting on you" }
        return said
    }

    enum CodingKeys: String, CodingKey { case state, detail, sessions, live, blocked, at }

    public init() {}

    public init(state: String, detail: String = "", sessions: [Session] = [],
                live: Int = 0, blocked: Int = 0, at: String = "") {
        self.state = state
        self.detail = detail
        self.sessions = sessions
        self.live = live
        self.blocked = blocked
        self.at = at
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = c.str(.state) ?? "unavailable"
        detail = c.str(.detail) ?? ""
        // One row at a time: an agent hcom described oddly loses its own row,
        // never the whole roster.
        sessions = c.list(.sessions, Lenient<Session>.self).compactMap(\.value)
        live = c.int(.live) ?? 0
        blocked = c.int(.blocked) ?? 0
        at = c.str(.at) ?? ""
    }
}

public struct Session: Decodable, Equatable, Identifiable {
    public var name: String = ""
    /// claude, codex, or whatever else hcom is holding hooks for.
    public var tool: String = ""
    public var status: String = "unknown"
    /// hcom's own one-liner: "active: Bash", "inactive: stale".
    public var doing: String = ""
    /// The tool call under way, when there is one. Long, and already clipped by
    /// the server: a bash heredoc in here would be the whole row.
    public var detail: String = ""
    public var directory: String = ""
    /// The desk this session sits at, from the folder's git remote. Empty for a
    /// session in a scratch folder, which is a true thing to say about it.
    public var repo: String = ""
    public var branch: String = ""
    public var unread: Int = 0
    public var headless: Bool = false
    /// Whether a message sent now would ever be read. On the row rather than
    /// left for a person to infer from a status word.
    public var reachable: Bool = false
    public var sessionID: String = ""
    public var ageSeconds: Int?
    public var startedAt: String = ""

    public var id: String { name }

    /// The one waiting on a person. The only status that is definitely a
    /// question rather than work in progress.
    public var isBlocked: Bool { status == "blocked" }
    public var isAlive: Bool { status == "active" || status == "listening" || status == "blocked" }

    /// A word for the folder, for a row that has no room for a path.
    public var place: String {
        if !repo.isEmpty { return repo }
        return (directory as NSString).lastPathComponent
    }

    enum CodingKeys: String, CodingKey {
        case name, tool, status, doing, detail, directory, repo, branch
        case unread, headless, reachable
        case sessionID = "session_id", ageSeconds = "age_s", startedAt = "started_at"
    }

    public init() {}

    public init(name: String, tool: String = "claude", status: String = "active",
                doing: String = "", repo: String = "", reachable: Bool = true) {
        self.name = name
        self.tool = tool
        self.status = status
        self.doing = doing
        self.repo = repo
        self.reachable = reachable
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.str(.name) ?? ""
        tool = c.str(.tool) ?? ""
        status = c.str(.status) ?? "unknown"
        doing = c.str(.doing) ?? ""
        detail = c.str(.detail) ?? ""
        directory = c.str(.directory) ?? ""
        repo = c.str(.repo) ?? ""
        branch = c.str(.branch) ?? ""
        unread = c.int(.unread) ?? 0
        headless = c.bool(.headless) ?? false
        reachable = c.bool(.reachable) ?? false
        sessionID = c.str(.sessionID) ?? ""
        ageSeconds = c.int(.ageSeconds)
        startedAt = c.str(.startedAt) ?? ""
    }
}

/// One agent's conversation, as it is read rather than as it is run.
public struct SessionTranscript: Decodable, Equatable {
    public var name: String = ""
    public var exchanges: [Exchange] = []
    public var at: String = ""

    enum CodingKeys: String, CodingKey { case name, exchanges, at }

    public init() {}

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.str(.name) ?? ""
        exchanges = c.list(.exchanges, Lenient<Exchange>.self).compactMap(\.value)
        at = c.str(.at) ?? ""
    }

    public struct Exchange: Decodable, Equatable, Identifiable {
        public var position: Int = 0
        public var at: String = ""
        /// What was said TO the agent. Empty on a turn the agent started.
        public var you: String = ""
        /// What the agent said back.
        public var them: String = ""
        public var files: [String] = []

        public var id: String { "\(position)@\(at)" }

        enum CodingKeys: String, CodingKey { case position, at, you, them, files }

        public init() {}

        public init(position: Int, you: String, them: String, at: String = "") {
            self.position = position
            self.you = you
            self.them = them
            self.at = at
        }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            position = c.int(.position) ?? 0
            at = c.str(.at) ?? ""
            you = c.str(.you) ?? ""
            them = c.str(.them) ?? ""
            files = c.list(.files, String.self)
        }
    }
}

import Foundation

/// The shapes `client/serve.py` hands back, and nothing else.
///
/// Every one of these decodes leniently on purpose. The server is a local python
/// process that is upgraded on its own schedule, so a field arriving as the wrong
/// type, or not arriving at all, has to degrade into a quiet blank rather than
/// take the whole roster down. A room that disappears because one repo's
/// `updatedAt` came back null is worse than a room with one empty timestamp.
///
/// Foundation only. `StateRules` compiles against this file in the test bundle,
/// which is what keeps the desk rules provable without a screen.

// MARK: - lenient reading

extension KeyedDecodingContainer {
    func str(_ key: Key) -> String? { (try? decodeIfPresent(String.self, forKey: key)) ?? nil }
    func bool(_ key: Key) -> Bool? { (try? decodeIfPresent(Bool.self, forKey: key)) ?? nil }
    func int(_ key: Key) -> Int? { (try? decodeIfPresent(Int.self, forKey: key)) ?? nil }
    func dbl(_ key: Key) -> Double? { (try? decodeIfPresent(Double.self, forKey: key)) ?? nil }
    func list<T: Decodable>(_ key: Key, _: T.Type) -> [T] {
        ((try? decodeIfPresent([T].self, forKey: key)) ?? nil) ?? []
    }
}

// MARK: - the chatroom

/// One line of a conversation.
///
/// The turn record names its text `content` in some harnesses and `text` in
/// others, and both are in the wild right now. Accepting either here means no
/// view ever has to know which harness answered.
public struct ChatTurn: Decodable, Hashable {
    public var role: String
    public var content: String
    public var at: String?

    public init(role: String, content: String, at: String? = nil) {
        self.role = role
        self.content = content
        self.at = at
    }

    enum CodingKeys: String, CodingKey { case role, content, text, at }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        role = c.str(.role) ?? "assistant"
        content = c.str(.content) ?? c.str(.text) ?? ""
        at = c.str(.at)
    }

    public var isUser: Bool { role == "user" }
}

public struct Bot: Decodable, Identifiable, Hashable {
    public var id: String
    public var name: String
    public var color: String
    public var last: ChatTurn?
    public var busy: Bool
    public var error: String?

    public init(id: String, name: String, color: String = "",
                last: ChatTurn? = nil, busy: Bool = false, error: String? = nil) {
        self.id = id
        self.name = name
        self.color = color
        self.last = last
        self.busy = busy
        self.error = error
    }

    enum CodingKeys: String, CodingKey { case id, name, color, last, busy, error }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.str(.id) ?? ""
        name = c.str(.name) ?? id
        color = c.str(.color) ?? ""
        last = (try? c.decodeIfPresent(ChatTurn.self, forKey: .last)) ?? nil
        busy = c.bool(.busy) ?? false
        error = c.str(.error)
    }
}

public struct BotsResponse: Decodable {
    public var bots: [Bot] = []
    /// "up" or "down". The roster is read off disk, so four quiet bots and a
    /// dead harness is a normal state, not an empty screen.
    public var runtime: String = "down"
    public var at: String?

    enum CodingKeys: String, CodingKey { case bots, runtime, at }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        bots = c.list(.bots, Bot.self)
        runtime = c.str(.runtime) ?? "down"
        at = c.str(.at)
    }

    public init(bots: [Bot], runtime: String = "up") {
        self.bots = bots
        self.runtime = runtime
    }
}

public struct ChatResponse: Decodable {
    public var bot: String = ""
    public var turns: [ChatTurn] = []

    enum CodingKeys: String, CodingKey { case bot, turns }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        bot = c.str(.bot) ?? ""
        turns = c.list(.turns, ChatTurn.self)
    }
}

// MARK: - the floor

public struct Issue: Decodable, Hashable, Identifiable {
    public var number: Int
    public var title: String
    public var body: String
    public var labels: [String]
    public var url: String
    public var updatedAt: String
    /// Computed by the runner from the comments themselves. `nil` means the
    /// snapshot predates the field, which is the only case where a label is
    /// allowed to answer the question instead.
    public var botLast: Bool?
    public var lastWord: String

    public var id: Int { number }

    public init(number: Int, title: String, body: String = "", labels: [String] = [],
                url: String = "", updatedAt: String = "", botLast: Bool? = nil,
                lastWord: String = "") {
        self.number = number
        self.title = title
        self.body = body
        self.labels = labels
        self.url = url
        self.updatedAt = updatedAt
        self.botLast = botLast
        self.lastWord = lastWord
    }

    enum CodingKeys: String, CodingKey {
        case number, title, body, labels, url, updatedAt
        case botLast = "bot_last"
        case lastWord = "last_word"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        number = c.int(.number) ?? 0
        title = c.str(.title) ?? ""
        body = c.str(.body) ?? ""
        labels = c.list(.labels, String.self)
        url = c.str(.url) ?? ""
        updatedAt = c.str(.updatedAt) ?? ""
        botLast = c.bool(.botLast)
        lastWord = c.str(.lastWord) ?? ""
    }
}

public struct PullRequest: Decodable, Hashable, Identifiable {
    public var number: Int
    public var title: String
    public var head: String
    public var base: String
    public var url: String
    public var draft: Bool
    /// GitHub's own verdict: MERGEABLE / CONFLICTING / UNKNOWN. UNKNOWN is not
    /// permission, it is "ask again in a moment", so it never lights the button.
    public var mergeable: String
    public var state: String
    public var closes: [Int]
    public var updatedAt: String

    public var id: Int { number }
    public var canMerge: Bool { mergeable == "MERGEABLE" && !draft }

    public init(number: Int, title: String, head: String = "", base: String = "main",
                url: String = "", draft: Bool = false, mergeable: String = "UNKNOWN",
                state: String = "UNKNOWN", closes: [Int] = [], updatedAt: String = "") {
        self.number = number
        self.title = title
        self.head = head
        self.base = base
        self.url = url
        self.draft = draft
        self.mergeable = mergeable
        self.state = state
        self.closes = closes
        self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey {
        case number, title, head, base, url, draft, mergeable, state, closes, updatedAt
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        number = c.int(.number) ?? 0
        title = c.str(.title) ?? ""
        head = c.str(.head) ?? ""
        base = c.str(.base) ?? ""
        url = c.str(.url) ?? ""
        draft = c.bool(.draft) ?? false
        mergeable = c.str(.mergeable) ?? "UNKNOWN"
        state = c.str(.state) ?? "UNKNOWN"
        closes = c.list(.closes, Int.self)
        updatedAt = c.str(.updatedAt) ?? ""
    }
}

public struct Run: Decodable, Hashable {
    public var at: String
    public var outcome: String
    public var issue: String
    public var detail: String

    public init(at: String = "", outcome: String = "", issue: String = "", detail: String = "") {
        self.at = at
        self.outcome = outcome
        self.issue = issue
        self.detail = detail
    }

    enum CodingKeys: String, CodingKey { case at, outcome, issue, detail }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        at = c.str(.at) ?? ""
        outcome = c.str(.outcome) ?? ""
        issue = c.str(.issue) ?? ""
        detail = c.str(.detail) ?? ""
    }
}

public struct Station: Decodable, Hashable, Identifiable {
    public var repo: String
    public var identity: String
    /// `false` means no account we hold a token for can push here. `nil` means
    /// the snapshot did not say, which is not the same as "locked".
    public var access: Bool?
    public var outcome: String
    public var detail: String
    public var at: String
    public var runs: [Run]
    public var issues: [Issue]
    public var issuesError: String?
    public var prs: [PullRequest]
    public var prsError: String?

    /// Attached on this side, never decoded: the runtime is not a repo, so an
    /// open gate has to be given a desk to stand at.
    public var gate: Gate?
    public var synthetic: Bool = false

    public var id: String { repo }
    public var owner: String { repo.split(separator: "/").first.map(String.init) ?? repo }
    public var shortName: String { repo.split(separator: "/").last.map(String.init) ?? repo }

    public init(repo: String, identity: String = "", access: Bool? = true,
                outcome: String = "", detail: String = "", at: String = "",
                runs: [Run] = [], issues: [Issue] = [], issuesError: String? = nil,
                prs: [PullRequest] = [], prsError: String? = nil,
                gate: Gate? = nil, synthetic: Bool = false) {
        self.repo = repo
        self.identity = identity
        self.access = access
        self.outcome = outcome
        self.detail = detail
        self.at = at
        self.runs = runs
        self.issues = issues
        self.issuesError = issuesError
        self.prs = prs
        self.prsError = prsError
        self.gate = gate
        self.synthetic = synthetic
    }

    enum CodingKeys: String, CodingKey {
        case repo, identity, access, outcome, detail, at, runs, issues, prs
        case issuesError = "issues_error"
        case prsError = "prs_error"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        repo = c.str(.repo) ?? ""
        identity = c.str(.identity) ?? ""
        access = c.bool(.access)
        outcome = c.str(.outcome) ?? ""
        detail = c.str(.detail) ?? ""
        at = c.str(.at) ?? ""
        runs = c.list(.runs, Run.self)
        issues = c.list(.issues, Issue.self)
        issuesError = c.str(.issuesError)
        prs = c.list(.prs, PullRequest.self)
        prsError = c.str(.prsError)
    }
}

// MARK: - the raised hand

public struct Gate: Decodable, Hashable {
    /// clear | pending | unconfigured | missing-root | error | unreadable
    public var state: String
    public var id: String
    public var permission: String
    /// The literal thing being asked about. Never summarised, never truncated
    /// into ambiguity: a gate you approve without seeing the exact target is not
    /// a gate.
    public var target: String
    public var detail: String
    public var askedAt: Double?
    public var waitingS: Double?
    /// Which bot is standing there with its hand up, when the server knows.
    public var bot: String?

    public var isPending: Bool { state == "pending" && !id.isEmpty }

    public init(state: String, id: String = "", permission: String = "",
                target: String = "", detail: String = "", askedAt: Double? = nil,
                waitingS: Double? = nil, bot: String? = nil) {
        self.state = state
        self.id = id
        self.permission = permission
        self.target = target
        self.detail = detail
        self.askedAt = askedAt
        self.waitingS = waitingS
        self.bot = bot
    }

    enum CodingKeys: String, CodingKey {
        case state, id, permission, target, detail, bot
        case askedAt = "asked_at"
        case waitingS = "waiting_s"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = c.str(.state) ?? "clear"
        id = c.str(.id) ?? ""
        permission = c.str(.permission) ?? ""
        target = c.str(.target) ?? ""
        detail = c.str(.detail) ?? ""
        askedAt = c.dbl(.askedAt)
        waitingS = c.dbl(.waitingS)
        bot = c.str(.bot)
    }

    public static let clear = Gate(state: "clear")
}

public struct RuntimeInfo: Decodable, Hashable {
    public var gate: Gate?
    public var url: String
    public var root: String

    public init(gate: Gate? = nil, url: String = "", root: String = "") {
        self.gate = gate
        self.url = url
        self.root = root
    }

    enum CodingKeys: String, CodingKey { case gate, url, root }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        gate = (try? c.decodeIfPresent(Gate.self, forKey: .gate)) ?? nil
        url = c.str(.url) ?? ""
        root = c.str(.root) ?? ""
    }
}

public struct World: Decodable {
    public var generated: String
    public var heartbeat: String
    public var killed: Bool
    public var stations: [Station]
    public var runtime: RuntimeInfo?

    public init(generated: String = "", heartbeat: String = "", killed: Bool = false,
                stations: [Station] = [], runtime: RuntimeInfo? = nil) {
        self.generated = generated
        self.heartbeat = heartbeat
        self.killed = killed
        self.stations = stations
        self.runtime = runtime
    }

    enum CodingKeys: String, CodingKey { case generated, heartbeat, killed, stations, runtime }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generated = c.str(.generated) ?? ""
        heartbeat = c.str(.heartbeat) ?? ""
        killed = c.bool(.killed) ?? false
        stations = c.list(.stations, Station.self)
        runtime = (try? c.decodeIfPresent(RuntimeInfo.self, forKey: .runtime)) ?? nil
    }
}

public struct WorldResponse: Decodable {
    public var at: String = ""
    public var world: World = World()

    enum CodingKeys: String, CodingKey { case at, world }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        at = c.str(.at) ?? ""
        world = ((try? c.decodeIfPresent(World.self, forKey: .world)) ?? nil) ?? World()
    }
}

/// What came back from a write. `result` and `message` are the server's own
/// words and are shown verbatim, because a rewritten failure is a guess.
public struct Ack: Decodable {
    public var ok: Bool
    public var result: String?
    public var message: String?
    public var error: String?

    public init(ok: Bool, result: String? = nil, message: String? = nil, error: String? = nil) {
        self.ok = ok
        self.result = result
        self.message = message
        self.error = error
    }

    enum CodingKeys: String, CodingKey { case ok, result, message, error }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = c.bool(.ok) ?? false
        result = c.str(.result)
        message = c.str(.message)
        error = c.str(.error)
    }

    /// One sentence a person can read, in the server's words wherever it gave any.
    public var spoken: String {
        for candidate in [result, message, error] {
            if let text = candidate, !text.isEmpty { return text }
        }
        return ok ? "Done." : "That did not apply."
    }
}

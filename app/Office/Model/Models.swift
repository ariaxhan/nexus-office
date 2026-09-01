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

    /// A value a source wrote as whatever it had to hand, read as the one thing
    /// a label can be drawn next to: text.
    ///
    /// Six python files write these and each one decides on its own whether a
    /// count is `32` or `"32"`. Insisting on a string here would blank a fact
    /// because of a missing pair of quotes somewhere else, which is a whole
    /// number turning into nothing on screen for no reason a person can see.
    func loose(_ key: Key) -> String? {
        if let text = str(key) { return text }
        if let whole = int(key) { return String(whole) }
        if let number = dbl(key) { return String(number) }
        if let flag = bool(key) { return flag ? "yes" : "no" }
        return nil
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
    /// Whether a picture rode with this turn.
    ///
    /// The bytes are ephemeral: the office is a courier for them and the harness
    /// never writes them down, so a turn that comes back from the transcript can
    /// say a photo went with it and can never show it again. That is the whole
    /// claim this flag makes, and the mark in the thread says exactly that much.
    ///
    /// Decoded from whatever the harness echoes: an `attachments` list, or a
    /// plain `has_photo`. When it echoes neither, the store marks the turn from
    /// its own record of what it sent, which is a weaker fact and is why that
    /// path is spelled out in `Store.remember(photoFor:message:)` rather than
    /// hidden here.
    public var hasPhoto: Bool

    public init(role: String, content: String, at: String? = nil, hasPhoto: Bool = false) {
        self.role = role
        self.content = content
        self.at = at
        self.hasPhoto = hasPhoto
    }

    enum CodingKeys: String, CodingKey {
        case role, content, text, at, attachments, has_photo, photo
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        role = c.str(.role) ?? "assistant"
        content = c.str(.content) ?? c.str(.text) ?? ""
        at = c.str(.at)
        let carried = c.list(.attachments, Lenient<TurnAttachment>.self).compactMap(\.value)
        hasPhoto = !carried.isEmpty || (c.bool(.has_photo) ?? false) || (c.bool(.photo) ?? false)
    }

    public var isUser: Bool { role == "user" }
}

/// What the harness says about a picture that rode with a turn, if it says
/// anything at all. Only its existence is ever used: the bytes are gone by the
/// time anything here could read them, and a decoder that insisted on the whole
/// shape would drop the mark whenever the harness added a field.
public struct TurnAttachment: Decodable, Hashable {
    public var name: String
    public var mimeType: String

    enum CodingKeys: String, CodingKey { case name, mime_type }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.str(.name) ?? ""
        mimeType = c.str(.mime_type) ?? ""
    }
}

public struct Bot: Decodable, Identifiable, Hashable {
    public var id: String
    public var name: String
    public var color: String
    /// The one line that says what to ask this bot for. It comes from
    /// `_meta/bots.json`, where it sits beside a whole paragraph of identity
    /// that the app deliberately never shows: a person picking who to message
    /// needs to know what a colleague is for, not how they were told to think.
    public var purpose: String
    /// Its reporting cadence, kept separate from purpose because when a bot
    /// speaks and what it judges are different promises.
    public var frequency: String
    public var last: ChatTurn?
    public var busy: Bool
    public var error: String?

    public init(id: String, name: String, color: String = "", purpose: String = "",
                frequency: String = "",
                last: ChatTurn? = nil, busy: Bool = false, error: String? = nil) {
        self.id = id
        self.name = name
        self.color = color
        self.purpose = purpose
        self.frequency = frequency
        self.last = last
        self.busy = busy
        self.error = error
    }

    enum CodingKeys: String, CodingKey {
        case id, name, color, purpose, frequency, last, busy, error
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.str(.id) ?? ""
        name = c.str(.name) ?? id
        color = c.str(.color) ?? ""
        purpose = c.str(.purpose) ?? ""
        frequency = c.str(.frequency) ?? ""
        last = (try? c.decodeIfPresent(ChatTurn.self, forKey: .last)) ?? nil
        busy = c.bool(.busy) ?? false
        error = c.str(.error)
    }
}

public struct BotsResponse: Decodable {
    public var bots: [Bot] = []
    /// "up" or "down". The roster is read off disk, so quiet bots and a
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
    /// Computed by the runner from the comments themselves, and the whole of
    /// what "waiting on you" means. `nil` is the snapshot declining to say, and
    /// is never read as yes.
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
    /// Visibility is for every PR. This flag only grants the Office merge
    /// control; the server independently re-checks the branch before acting.
    public var pipeline: Bool
    public var base: String
    public var url: String
    public var draft: Bool
    /// GitHub's own verdict: MERGEABLE / CONFLICTING / UNKNOWN. UNKNOWN is not
    /// permission, it is "ask again in a moment", so it never lights the button.
    public var mergeable: String
    public var state: String
    public var closes: [Int]
    public var updatedAt: String
    /// What the PR says about itself.
    ///
    /// The office's own PRs are written by the runner, so this is the closest
    /// thing to a note from the agent that did the work, and reading it is most
    /// of deciding whether to press merge. Empty is the ordinary case for a PR
    /// opened with no description, and is drawn as nothing rather than as a
    /// blank panel.
    public var body: String

    public var id: Int { number }
    public var isMergeable: Bool { mergeable == "MERGEABLE" && !draft }
    public var canMerge: Bool { pipeline && isMergeable }

    public init(number: Int, title: String, head: String = "", base: String = "main",
                pipeline: Bool = true, url: String = "", draft: Bool = false,
                mergeable: String = "UNKNOWN",
                state: String = "UNKNOWN", closes: [Int] = [], updatedAt: String = "",
                body: String = "") {
        self.number = number
        self.title = title
        self.head = head
        self.pipeline = pipeline
        self.base = base
        self.url = url
        self.draft = draft
        self.mergeable = mergeable
        self.state = state
        self.closes = closes
        self.updatedAt = updatedAt
        self.body = body
    }

    enum CodingKeys: String, CodingKey {
        case number, title, head, pipeline, base, url, draft, mergeable, state, closes, updatedAt, body
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        number = c.int(.number) ?? 0
        title = c.str(.title) ?? ""
        head = c.str(.head) ?? ""
        // Older snapshots contained pipeline PRs exclusively. Preserve their
        // merge control during a rolling app/server restart.
        pipeline = c.bool(.pipeline) ?? true
        base = c.str(.base) ?? ""
        url = c.str(.url) ?? ""
        draft = c.bool(.draft) ?? false
        mergeable = c.str(.mergeable) ?? "UNKNOWN"
        state = c.str(.state) ?? "UNKNOWN"
        closes = c.list(.closes, Int.self)
        updatedAt = c.str(.updatedAt) ?? ""
        body = c.str(.body) ?? ""
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
    /// The last time GitHub actually answered for this desk. It can be older
    /// than the world's `generated`, and that gap is the whole point: the rest
    /// of this station is then last-good data rather than current data, and a
    /// surface that draws it without saying so is lying about how fresh it is.
    public var fetchedAt: String
    /// Put away by a person. Still arrives with its last data, still counted,
    /// simply not polled and not in the desks list.
    public var hidden: Bool
    /// Its rank at the top of the roster, or nil when a person never pinned
    /// it. The order itself is `World.pins`; this is the same fact per desk.
    public var pinned: Int?
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

    /// What went wrong reading this desk, said once. The two halves of a pull
    /// fail together and report the same sentence twice, and printing the same
    /// red line twice reads as two problems.
    public var problems: [String] {
        var seen: [String] = []
        for candidate in [issuesError, prsError] {
            guard let text = candidate?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !text.isEmpty, !seen.contains(text) else { continue }
            seen.append(text)
        }
        return seen
    }

    public init(repo: String, identity: String = "", access: Bool? = true,
                outcome: String = "", detail: String = "", at: String = "",
                fetchedAt: String = "", hidden: Bool = false, pinned: Int? = nil,
                runs: [Run] = [], issues: [Issue] = [], issuesError: String? = nil,
                prs: [PullRequest] = [], prsError: String? = nil,
                gate: Gate? = nil, synthetic: Bool = false) {
        self.repo = repo
        self.identity = identity
        self.access = access
        self.outcome = outcome
        self.detail = detail
        self.at = at
        self.fetchedAt = fetchedAt
        self.hidden = hidden
        self.pinned = pinned
        self.runs = runs
        self.issues = issues
        self.issuesError = issuesError
        self.prs = prs
        self.prsError = prsError
        self.gate = gate
        self.synthetic = synthetic
    }

    enum CodingKeys: String, CodingKey {
        case repo, identity, access, outcome, detail, at, hidden, pinned, runs, issues, prs
        case fetchedAt = "fetched_at"
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
        fetchedAt = c.str(.fetchedAt) ?? ""
        hidden = c.bool(.hidden) ?? false
        pinned = c.int(.pinned)
        runs = c.list(.runs, Run.self)
        issues = c.list(.issues, Issue.self)
        issuesError = c.str(.issuesError)
        prs = c.list(.prs, PullRequest.self)
        prsError = c.str(.prsError)
    }
}

/// What GitHub is willing to answer right now.
///
/// `pausedUntil` is the door having stopped asking on purpose rather than
/// having failed: while it is set, every desk on the floor is showing the last
/// thing it managed to pull, and that is a fact about the budget and not about
/// any one repo.
public struct GitHubBudget: Decodable, Hashable {
    public var limit: Int?
    public var remaining: Int?
    public var resetAt: String
    public var cost: Int
    public var pausedUntil: String
    public var error: String

    public var isPaused: Bool { !pausedUntil.isEmpty }

    public init(limit: Int? = nil, remaining: Int? = nil, resetAt: String = "",
                cost: Int = 0, pausedUntil: String = "", error: String = "") {
        self.limit = limit
        self.remaining = remaining
        self.resetAt = resetAt
        self.cost = cost
        self.pausedUntil = pausedUntil
        self.error = error
    }

    enum CodingKeys: String, CodingKey {
        case limit, remaining, cost, error
        case resetAt = "reset_at"
        case pausedUntil = "paused_until"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        limit = c.int(.limit)
        remaining = c.int(.remaining)
        resetAt = c.str(.resetAt) ?? ""
        cost = c.int(.cost) ?? 0
        pausedUntil = c.str(.pausedUntil) ?? ""
        error = c.str(.error) ?? ""
    }
}

/// `GET /api/desks` and the answer to a `POST` of one: the whole put-away list,
/// never a delta. A list is reconcilable against the world; a delta is a guess
/// about what the server already had.
public struct DesksResponse: Decodable {
    public var hidden: [String]

    public init(hidden: [String] = []) { self.hidden = hidden }

    enum CodingKeys: String, CodingKey { case hidden }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        hidden = c.list(.hidden, String.self)
    }
}

/// `GET /api/pins` and the answer to a `POST` of one: the whole pin order,
/// never a delta, for the same reason as `DesksResponse`.
public struct PinsResponse: Decodable {
    public var pins: [String]

    public init(pins: [String] = []) { self.pins = pins }

    enum CodingKeys: String, CodingKey { case pins }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        pins = c.list(.pins, String.self)
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

/// Every hand in the air, oldest first.
///
/// The floor is a room, not a queue of one. Two bots can be standing there at
/// the same time, and the failure this shape exists to prevent is the second
/// one being invisible until the first is answered: a raised hand nobody can
/// find is the one thing this app is not allowed to do.
///
/// Decoded defensively on purpose. A door that answers without a `gates` key is
/// a floor with nothing pending, not a crash, and one malformed entry drops
/// itself rather than taking every other raised hand down with it.
public struct GatesResponse: Decodable {
    /// When the door looked, as it said it.
    public var at: String
    /// The harness's own word when there is nothing to list: `""` normally,
    /// `"down"` when the runtime is not there to ask. Never `pending`, so it can
    /// only ever make the floor quieter, never louder.
    public var state: String
    public var gates: [Gate]

    public init(at: String = "", state: String = "", gates: [Gate] = []) {
        self.at = at
        self.state = state
        self.gates = gates
    }

    enum CodingKeys: String, CodingKey { case at, state, gates }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        at = c.str(.at) ?? ""
        state = c.str(.state) ?? ""
        gates = c.list(.gates, Lenient<Gate>.self).compactMap(\.value)
    }

    /// What the floor is when this list is empty: the door's own word for why,
    /// and never a state that could be mistaken for a question.
    public var quiet: Gate { Gate(state: state.isEmpty ? "clear" : state) }
}

/// One element of a list, decoded or dropped, never fatal.
///
/// A bad entry in an array of gates must cost that entry and nothing else.
/// `[Gate]` would throw on the first one and take the whole raised-hand list
/// with it, which turns one malformed record into an office that says nobody
/// is waiting.
public struct Lenient<T: Decodable>: Decodable {
    public let value: T?

    public init(from decoder: Decoder) throws {
        value = try? T(from: decoder)
    }
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

// MARK: - the wall

/// One measured thing, with a label on it.
///
/// `tone` is the source saying whether the number is good news, and it is a
/// hint rather than a vocabulary: an unknown word reads as no tone at all
/// rather than as a crash or as a wrong colour.
public struct SectionFact: Decodable, Hashable, Identifiable {
    public var label: String
    public var value: String
    public var tone: String

    /// Stable enough for a list, because two facts with the same label and the
    /// same value are the same fact twice and would draw identically anyway.
    public var id: String { label + "\u{001f}" + value }

    public init(label: String, value: String, tone: String = "") {
        self.label = label
        self.value = value
        self.tone = tone
    }

    enum CodingKeys: String, CodingKey { case label, value, tone }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = c.loose(.label) ?? ""
        value = c.loose(.value) ?? ""
        tone = c.str(.tone) ?? ""
    }
}

/// One line of the table under the numbers.
///
/// The generic half of the card, and deliberately anonymous: this is a learning,
/// a scheduled job, and whatever the ninth source sends next month, all drawn by
/// the same view. Nothing here knows what it is looking at, which is the only
/// reason a new source can be a new python file and no Swift at all.
///
/// Its decoder cannot throw. A row that arrived as a bare string, or as an
/// object full of numbers, still becomes a row: `[SectionRowItem]` would
/// otherwise throw on the first odd element and take the whole table with it,
/// which turns one malformed record into a section that looks empty.
public struct SectionRowItem: Decodable, Hashable, Identifiable {
    /// The source's own id for the thing. Not required to be unique on screen,
    /// so nothing draws by it; it is what a person quotes back.
    public var id: String
    /// The thing itself. The one line that must not be empty.
    public var title: String
    /// Where it came from, or when it runs. Under the title, quieter.
    public var subtitle: String
    /// The small print. Under everything, quietest.
    public var detail: String
    /// The one number or word worth a pill.
    public var badge: String
    /// The source saying whether this row is good news. A word nobody here
    /// knows reads as no tone rather than as a crash or as a wrong colour.
    public var tone: String
    /// The heading this row sits under. Empty means no heading at all.
    public var group: String
    /// Somewhere to go, or "". Never somewhere to run: see `destination`.
    public var url: String

    public init(id: String = "", title: String = "", subtitle: String = "",
                detail: String = "", badge: String = "", tone: String = "",
                group: String = "", url: String = "") {
        self.id = id
        self.title = title
        self.subtitle = subtitle
        self.detail = detail
        self.badge = badge
        self.tone = tone
        self.group = group
        self.url = url
    }

    enum CodingKeys: String, CodingKey {
        case id, title, subtitle, detail, badge, tone, group, url
    }

    public init(from decoder: Decoder) throws {
        guard let c = try? decoder.container(keyedBy: CodingKeys.self) else {
            // Not an object at all. Keep whatever text it was rather than
            // dropping it: a row nobody can read is still evidence that
            // something is wrong, and a silently shorter list is not.
            let text = (try? decoder.singleValueContainer().decode(String.self)) ?? ""
            self.init(title: text)
            return
        }
        // `loose` on purpose: eight python files write these and each one
        // decides on its own whether a count is `46` or `"46"`. A badge that
        // vanishes over a missing pair of quotes is a number missing from the
        // wall for no reason a person can see.
        self.init(id: c.loose(.id) ?? "",
                  title: c.loose(.title) ?? "",
                  subtitle: c.loose(.subtitle) ?? "",
                  detail: c.loose(.detail) ?? "",
                  badge: c.loose(.badge) ?? "",
                  tone: c.str(.tone) ?? "",
                  group: c.loose(.group) ?? "",
                  url: c.str(.url) ?? "")
    }

    /// Where a click goes, when there is anywhere it may go.
    ///
    /// Two schemes and no others. `https` is the internet and `file` is
    /// something on this machine the source already proved is there; everything
    /// else, `javascript:` first among them, is a way to make a click run
    /// something, and no row in this app runs anything. A refused url leaves
    /// the row drawn as text, which is honest: the row is still true, it is the
    /// destination that could not be stood behind.
    public var destination: URL? {
        guard let url = URL(string: url), let scheme = url.scheme?.lowercased(),
              scheme == "https" || scheme == "file" else { return nil }
        return url
    }
}

/// What a source wants said about itself, in the one shape every source shares.
///
/// This is the whole interface between the six python files that fill
/// `world.sections` and the app that draws them. Nothing above this line knows
/// what a shelf or a job or a ledger row is, and that is the point: a new
/// source is a new python file and no Swift at all.
public struct SectionCard: Decodable, Hashable {
    public var title: String
    /// One sentence. For a state that is not `ok`, it says what is wrong.
    public var headline: String
    /// How many things in here want a person. `0` when nothing does.
    public var needs: Int
    /// When the source last looked, ISO Z, or empty when it declined to say.
    public var asOf: String
    public var facts: [SectionFact]
    /// The list, when the source has one. Six of the eight send no `rows` key
    /// at all, so empty is the ordinary answer and not a failure to decode.
    public var rows: [SectionRowItem]

    public init(title: String = "", headline: String = "", needs: Int = 0,
                asOf: String = "", facts: [SectionFact] = [],
                rows: [SectionRowItem] = []) {
        self.title = title
        self.headline = headline
        self.needs = needs
        self.asOf = asOf
        self.facts = facts
        self.rows = rows
    }

    enum CodingKeys: String, CodingKey {
        case title, headline, needs, facts, rows
        case asOf = "as_of"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = c.str(.title) ?? ""
        headline = c.str(.headline) ?? ""
        // A negative count is a source with a bug, not a request for a badge
        // reading minus one.
        needs = max(0, c.int(.needs) ?? 0)
        asOf = c.str(.asOf) ?? ""
        facts = c.list(.facts, SectionFact.self)
        rows = c.list(.rows, SectionRowItem.self)
    }
}

/// One thing on the wall: a local source, its state, and its card.
///
/// The bag of source-specific keys that sits beside these is deliberately not
/// decoded. Any of it the app should draw belongs in `card.facts`, where the
/// source itself decided what a person needs to read.
public struct Section: Decodable, Hashable, Identifiable {
    /// The key it arrived under. Attached on this side: a value in a dictionary
    /// cannot see its own key.
    public var id: String = ""
    /// `ok`, and whatever else the source says: stale, error, missing,
    /// unconfigured, unreadable. Only `ok` is ever read as fine.
    public var state: String
    public var detail: String
    public var card: SectionCard

    public var isOK: Bool { state == "ok" }
    public var title: String { card.title }
    public var headline: String { card.headline }
    public var needs: Int { card.needs }

    public init(id: String = "", state: String = "ok", detail: String = "",
                card: SectionCard = SectionCard()) {
        self.state = state
        self.detail = detail
        self.card = card
        self.adopt(id: id)
    }

    enum CodingKeys: String, CodingKey { case state, detail, card }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = c.str(.state) ?? "ok"
        detail = c.str(.detail) ?? ""
        card = ((try? c.decodeIfPresent(SectionCard.self, forKey: .card)) ?? nil) ?? SectionCard()
    }

    /// Take the key, and fill in whatever the card did not say.
    ///
    /// A source that ships before its card does still gets a row with a name on
    /// it and a sentence under it. A blank row on the wall is a source a person
    /// stops believing is there.
    public mutating func adopt(id key: String) {
        id = key
        if card.title.isEmpty { card.title = Self.name(from: key) }
        if card.headline.isEmpty {
            card.headline = detail.isEmpty ? state : detail
        }
    }

    /// `cost-ledger` reads as "Cost ledger". Not clever, just never blank.
    private static func name(from key: String) -> String {
        let words = key.split(whereSeparator: { $0 == "-" || $0 == "_" || $0 == "." })
        guard let first = words.first else { return key }
        return ([first.capitalized] + words.dropFirst().map(String.init)).joined(separator: " ")
    }
}

// MARK: - what a desk says about itself

/// One document a desk offers to show.
public struct ContextFile: Decodable, Hashable, Identifiable {
    /// Relative to the checkout, and the only thing that may be asked for back.
    public var path: String
    public var name: String
    /// The folder it sits in, or `root`. The index is drawn under these.
    public var group: String
    public var bytes: Int
    /// Unix seconds, when the file last changed. Zero when the server did not
    /// say, which is treated as "not recent" rather than "epoch".
    public var mtime: Int

    public var id: String { path }

    public init(path: String = "", name: String = "", group: String = "",
                bytes: Int = 0, mtime: Int = 0) {
        self.path = path
        self.name = name
        self.group = group
        self.bytes = bytes
        self.mtime = mtime
    }

    enum CodingKeys: String, CodingKey { case path, name, group, bytes, mtime }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = c.str(.path) ?? ""
        name = c.str(.name) ?? ""
        group = c.str(.group) ?? ""
        bytes = max(0, c.int(.bytes) ?? 0)
        mtime = max(0, c.int(.mtime) ?? 0)
    }
}

/// A desk's own Markdown: the index, and whichever one of them is open.
///
/// Both in one value because they arrive in one answer. A list and a document
/// fetched separately can disagree about which desk they belong to, and the
/// disagreement is invisible: the index says one repo and the text says
/// another, and both draw perfectly well.
public struct DeskContext: Decodable, Hashable {
    public var repo: String
    /// Where the office found this checkout. Shown, because a person reading a
    /// file has a right to know which copy of the repo it came out of.
    public var root: String
    public var files: [ContextFile]
    /// Whether the index was cut. Drawn, always: a truncated list presented as
    /// a whole one is the defect this project exists to prevent.
    public var capped: Bool
    /// The document that is open, relative. Empty when only the index arrived.
    public var path: String
    public var title: String
    public var text: String
    public var bytes: Int

    public init(repo: String = "", root: String = "", files: [ContextFile] = [],
                capped: Bool = false, path: String = "", title: String = "",
                text: String = "", bytes: Int = 0) {
        self.repo = repo
        self.root = root
        self.files = files
        self.capped = capped
        self.path = path
        self.title = title
        self.text = text
        self.bytes = bytes
    }

    enum CodingKeys: String, CodingKey {
        case repo, root, files, capped, path, title, text, bytes
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        repo = c.str(.repo) ?? ""
        root = c.str(.root) ?? ""
        files = c.list(.files, ContextFile.self)
        capped = c.bool(.capped) ?? false
        path = c.str(.path) ?? ""
        title = c.str(.title) ?? ""
        text = c.str(.text) ?? ""
        bytes = max(0, c.int(.bytes) ?? 0)
    }

    /// What opens first: the README if there is one, else the first thing in
    /// the index. A person opening this wants the front page of the repo, and
    /// an empty pane over a list of files is a screen that looks broken.
    public static func opening(_ files: [ContextFile]) -> ContextFile? {
        files.first { $0.name.lowercased().hasPrefix("readme") } ?? files.first
    }
}

/// The index as a tree, flattened to the rows the pane draws.
///
/// The server sends paths; the folders are read off them here, because a
/// folder is nothing but the part of a path before the last slash, and the
/// server carrying a second, parallel list of folders is a list that can
/// disagree with the paths. Root READMEs stay first, as the server sorted
/// them; everything else is in path order under its folder.
///
/// Foundation only, so it proves out headless.
public enum FileTree {
    public struct Row: Identifiable {
        /// The path of the file, or of the folder with a trailing slash.
        public let id: String
        public let name: String
        public let depth: Int
        public let file: ContextFile?
        /// How many files are under a folder, for the count on a shut one.
        public let count: Int
        public var isFolder: Bool { file == nil }
    }

    /// The handful of documents somebody has actually touched lately, newest
    /// first.
    ///
    /// A desk holds hundreds of Markdown files in folder order, so the brief
    /// written an hour ago sits wherever the alphabet put it and is invisible.
    /// This is deliberately small and deliberately silent: no recent files, or
    /// an index short enough to read whole, and the caller draws nothing.
    public static func recent(of files: [ContextFile], now: Int,
                              within days: Int = 7, limit: Int = 5,
                              floor: Int = 12) -> [ContextFile] {
        guard files.count > floor else { return [] }
        let cutoff = now - days * 86_400
        return files
            .filter { $0.mtime >= cutoff && $0.mtime > 0 && $0.mtime <= now }
            .sorted { $0.mtime == $1.mtime ? $0.path < $1.path : $0.mtime > $1.mtime }
            .prefix(limit)
            .map { $0 }
    }

    public static func rows(of files: [ContextFile], closed: Set<String> = []) -> [Row] {
        var out: [Row] = []
        var opened: [String] = []
        var counts: [String: Int] = [:]
        for file in files {
            var parts = file.path.split(separator: "/", omittingEmptySubsequences: true).map(String.init)
            parts.removeLast()
            var key = ""
            for part in parts {
                key += part + "/"
                counts[key, default: 0] += 1
            }
        }
        for file in files {
            let parts = file.path.split(separator: "/", omittingEmptySubsequences: true).map(String.init)
            let folders = Array(parts.dropLast())
            // The deepest folder already open that this path shares.
            var shared = 0
            while shared < min(opened.count, folders.count), opened[shared] == folders[shared] {
                shared += 1
            }
            opened = Array(opened.prefix(shared))
            var key = opened.map { $0 + "/" }.joined()
            var hidden = opened.indices.contains { closed.contains(opened.prefix($0 + 1).map { $0 + "/" }.joined()) }
            for folder in folders.dropFirst(shared) {
                key += folder + "/"
                if !hidden {
                    out.append(Row(id: key, name: folder, depth: opened.count,
                                   file: nil, count: counts[key] ?? 0))
                }
                if closed.contains(key) { hidden = true }
                opened.append(folder)
            }
            if !hidden {
                out.append(Row(id: file.path, name: file.name, depth: folders.count,
                               file: file, count: 0))
            }
        }
        return out
    }
}

/// What `nexus-office://open?repo=owner/name&path=docs/x.md` asks for.
///
/// The repo must look like a repo and the path must be relative, or the URL
/// is not a request: the door refuses the same shapes again, but a URL is
/// typed by anything that can call `open`, so it is checked before it moves
/// a selection.
public struct OfficeURL: Equatable {
    public let repo: String
    public let path: String

    public static func parse(_ url: URL) -> OfficeURL? {
        guard url.scheme?.lowercased() == "nexus-office",
              url.host?.lowercased() == "open",
              let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems
        else { return nil }
        let repo = items.first { $0.name == "repo" }?.value ?? ""
        let path = items.first { $0.name == "path" }?.value ?? ""
        let parts = repo.split(separator: "/", omittingEmptySubsequences: false)
        guard parts.count == 2, parts.allSatisfy({ !$0.isEmpty }),
              repo.unicodeScalars.allSatisfy({ CharacterSet.alphanumerics.contains($0) || "._-/".unicodeScalars.contains($0) }),
              !path.isEmpty, !path.hasPrefix("/"), !path.hasPrefix("~"),
              !path.split(separator: "/", omittingEmptySubsequences: false).contains(where: { $0.isEmpty || $0 == "." || $0 == ".." })
        else { return nil }
        return OfficeURL(repo: repo, path: path)
    }
}

/// A section slot that swallows its own decode failure, so `[String: MaybeSection]`
/// survives one bad value where `[String: Section]` would throw the lot away.
struct MaybeSection: Decodable {
    let section: Section?
    init(from decoder: Decoder) throws { section = try? Section(from: decoder) }
}

public struct World: Decodable {
    public var generated: String
    public var heartbeat: String
    public var killed: Bool
    public var stations: [Station]
    /// The wall: one entry per local source, in a stable order.
    ///
    /// It arrives as an object keyed by source id, and a JSON object has no
    /// order, so the roster would otherwise shuffle its own rows under a person
    /// every ten seconds. Sorted by title here, once, where the key is still in
    /// hand to attach.
    public var sections: [Section]
    /// The desks a person dragged to the top, in the order they put them.
    public var pins: [String]
    /// Whose office this is: the first name here is sorted above every other
    /// org on the roster. Empty when the door could not say.
    public var owners: [String]
    public var runtime: RuntimeInfo?
    public var github: GitHubBudget?
    /// What the automation is doing, as one page. Assembled by the server out of
    /// the same receipts and desks this snapshot already carries, so the app and
    /// the phone read one answer rather than each deriving their own.
    public var automation: Automation = Automation()

    public init(generated: String = "", heartbeat: String = "", killed: Bool = false,
                stations: [Station] = [], sections: [Section] = [],
                pins: [String] = [], owners: [String] = [],
                runtime: RuntimeInfo? = nil, github: GitHubBudget? = nil,
                automation: Automation = Automation()) {
        self.generated = generated
        self.heartbeat = heartbeat
        self.killed = killed
        self.stations = stations
        self.sections = World.ordered(sections)
        self.pins = pins
        self.owners = owners
        self.runtime = runtime
        self.github = github
        self.automation = automation
    }

    enum CodingKeys: String, CodingKey {
        case generated, heartbeat, killed, stations, sections, pins, owners, runtime, github
        case automation
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        generated = c.str(.generated) ?? ""
        heartbeat = c.str(.heartbeat) ?? ""
        killed = c.bool(.killed) ?? false
        stations = c.list(.stations, Station.self)
        // Decoded one section at a time: a source that wrote nonsense loses its
        // own card, never the whole wall.
        let keyed = ((try? c.decodeIfPresent([String: MaybeSection].self, forKey: .sections)) ?? nil) ?? [:]
        sections = World.ordered(keyed.compactMap { key, maybe -> Section? in
            guard var section = maybe.section else { return nil }
            section.adopt(id: key)
            return section
        })
        pins = c.list(.pins, String.self)
        owners = c.list(.owners, String.self)
        runtime = (try? c.decodeIfPresent(RuntimeInfo.self, forKey: .runtime)) ?? nil
        github = (try? c.decodeIfPresent(GitHubBudget.self, forKey: .github)) ?? nil
        // A server that has not learned this block yet draws an empty page, not
        // a failed world.
        automation = ((try? c.decodeIfPresent(Automation.self, forKey: .automation)) ?? nil)
            ?? Automation()
    }

    /// By title, then by id, so two sources that chose the same title still
    /// land in the same place on every poll.
    static func ordered(_ sections: [Section]) -> [Section] {
        sections.sorted {
            ($0.title.lowercased(), $0.id) < ($1.title.lowercased(), $1.id)
        }
    }
}

/// `/api/automation`: the page, and the snapshot it was taken from.
public struct AutomationResponse: Decodable {
    public var at: String = ""
    public var automation: Automation = Automation()

    enum CodingKeys: String, CodingKey { case at, automation }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        at = c.str(.at) ?? ""
        automation = ((try? c.decodeIfPresent(Automation.self, forKey: .automation)) ?? nil)
            ?? Automation()
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

/// A desk's README, read off this machine.
///
/// `state` is `ok`, `none` (this checkout has no README), `elsewhere` (the
/// office does not know where this repo is checked out) or `unreadable`. Only
/// `ok` carries text, and the other three each carry their own sentence,
/// because a desk that says nothing at all is the thing this was added to end.
public struct DeskReadme: Decodable, Hashable {
    public var repo: String
    public var state: String
    public var name: String
    public var text: String
    public var detail: String
    /// The file's own mtime, not the snapshot's clock.
    public var at: String
    public var clipped: Bool

    public var isOK: Bool { state == "ok" && !text.isEmpty }

    public init(repo: String = "", state: String = "none", name: String = "",
                text: String = "", detail: String = "", at: String = "",
                clipped: Bool = false) {
        self.repo = repo
        self.state = state
        self.name = name
        self.text = text
        self.detail = detail
        self.at = at
        self.clipped = clipped
    }

    enum CodingKeys: String, CodingKey { case repo, state, name, text, detail, at, clipped }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        repo = c.str(.repo) ?? ""
        state = c.str(.state) ?? "none"
        name = c.str(.name) ?? ""
        text = c.str(.text) ?? ""
        detail = c.str(.detail) ?? ""
        at = c.str(.at) ?? ""
        clipped = c.bool(.clipped) ?? false
    }
}

// MARK: - the feed
//
// Every account is a repo. One store, two views: the global timeline and one
// repo's. There is no addressee and no inbox, because a post is published by an
// account rather than sent to a person, which is the reason posting is cheap
// enough that agents will actually do it while they work.
//
// The one rule lives on `authorizes`, and nothing in this app can set it. Only
// a reply made through the office door carries it, because the door is the only
// surface that has established it is Aria. So agents may say anything to each
// other and none of it can widen anyone's scope.

/// What a post is doing. Five words that colour a row; they never gate a post,
/// because a feed nobody writes to for fear of the wrong label is a dead feed.
public enum PostKind: String, Decodable, CaseIterable {
    case working, found, landed, blocked, asking, note

    public init(rawValue: String) {
        switch rawValue {
        case "working": self = .working
        case "found": self = .found
        case "landed": self = .landed
        case "blocked": self = .blocked
        case "asking": self = .asking
        default: self = .note
        }
    }

    /// The short word on the pill. `note` has none: the ordinary post is the one
    /// that should carry no decoration at all.
    public var label: String { self == .note ? "" : rawValue }

    /// Whether a row of this kind is something a person has to do something
    /// about. Only two of the six are.
    public var wantsYou: Bool { self == .asking || self == .blocked }
}

/// One post, or one reply: a reply is a post with a parent, and nothing else.
public struct Post: Decodable, Hashable, Identifiable {
    public var id: String
    public var ts: String
    /// `4m`, `3h`, `2d`, already a string, because a renderer that decides how
    /// to print time prints it differently from the other renderer.
    public var age: String
    /// The repo. Not the agent, not the model, not the session.
    public var account: String
    public var kind: PostKind
    /// Who typed it: a lane, a bot, a person. Worth reading, and worth nothing
    /// as an identity, which is why it sits beside the account and never
    /// replaces it.
    public var by: String
    /// The contract a lane ran under, when it had one. Not a signature: the
    /// swarm that inspired this built real Ed25519 signing and then trusted a
    /// signature without checking it. A contract is checkable against the
    /// dispatch that produced it, which is the useful property.
    public var contract: String
    /// The line the feed shows.
    public var text: String
    /// Everything else, behind a disclosure.
    public var body: String
    /// Set when this post is the durable twin of a permission gate, so a reply
    /// can answer the thing that is actually blocked.
    public var gateId: String
    /// Aria's, and only ever set by the door.
    public var authorizes: Bool
    public var replies: [Post]
    /// Whether the person has answered, as opposed to somebody having replied.
    /// Two different facts, drawn differently, so never one key.
    public var answered: Bool
    /// A post that could not be read. It still draws: a silently shorter feed
    /// is the one failure a feed must never have.
    public var unreadable: Bool

    public init(id: String = "", ts: String = "", age: String = "", account: String = "",
                kind: PostKind = .note, by: String = "", contract: String = "",
                text: String = "", body: String = "", gateId: String = "",
                authorizes: Bool = false, replies: [Post] = [], answered: Bool = false,
                unreadable: Bool = false) {
        self.id = id
        self.ts = ts
        self.age = age
        self.account = account
        self.kind = kind
        self.by = by
        self.contract = contract
        self.text = text
        self.body = body
        self.gateId = gateId
        self.authorizes = authorizes
        self.replies = replies
        self.answered = answered
        self.unreadable = unreadable
    }

    enum CodingKeys: String, CodingKey {
        case id, ts, age, account, kind, by, contract, text, body, authorizes, replies,
             answered, unreadable
        case gateId = "gate_id"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.str(.id) ?? ""
        ts = c.str(.ts) ?? ""
        age = c.str(.age) ?? ""
        account = c.str(.account) ?? ""
        kind = PostKind(rawValue: c.str(.kind) ?? "note")
        by = c.str(.by) ?? ""
        contract = c.str(.contract) ?? ""
        text = c.str(.text) ?? ""
        body = c.str(.body) ?? ""
        gateId = c.str(.gateId) ?? ""
        authorizes = c.bool(.authorizes) ?? false
        replies = c.list(.replies, Lenient<Post>.self).compactMap(\.value)
        answered = c.bool(.answered) ?? false
        unreadable = c.bool(.unreadable) ?? false
    }

    /// Something a person has to do something about, and nobody has.
    public var wantsYou: Bool { kind.wantsYou && !answered }
}

/// `/api/board`: the timeline, plus every account that has ever posted.
public struct FeedResponse: Decodable {
    /// `ok`, `never` (nothing has posted), `unconfigured` (no vault), `error`.
    /// Three different facts that must never render the same, which is the
    /// whole reason this is a word and not an empty array.
    public var state: String
    /// Empty for the global feed, the account name for one repo's.
    public var repo: String
    public var posts: [Post]
    /// How many there are, as opposed to how many were sent.
    public var total: Int
    public var accounts: [String]
    public var asking: Int
    public var blocked: Int
    public var detail: String

    public init(state: String = "", repo: String = "", posts: [Post] = [], total: Int = 0,
                accounts: [String] = [], asking: Int = 0, blocked: Int = 0,
                detail: String = "") {
        self.state = state
        self.repo = repo
        self.posts = posts
        self.total = total
        self.accounts = accounts
        self.asking = asking
        self.blocked = blocked
        self.detail = detail
    }

    enum CodingKeys: String, CodingKey {
        case state, repo, posts, total, accounts, asking, blocked, detail
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = c.str(.state) ?? ""
        repo = c.str(.repo) ?? ""
        posts = c.list(.posts, Lenient<Post>.self).compactMap(\.value)
        total = c.int(.total) ?? 0
        accounts = c.list(.accounts, Lenient<String>.self).compactMap(\.value)
        asking = c.int(.asking) ?? 0
        blocked = c.int(.blocked) ?? 0
        detail = c.str(.detail) ?? ""
    }

    /// What to say when the list is empty, which is never the same sentence
    /// twice. "Nothing has posted" and "the office cannot see the vault" are
    /// opposite facts and a blank timeline that means either is a lie.
    public var emptyLine: String {
        switch state {
        case "ok": return repo.isEmpty
            ? "nothing posted yet. agents write here while they work."
            : "@\(repo) has not posted."
        case "never": return "no board yet: nothing on this machine has posted."
        case "unconfigured": return detail.isEmpty
            ? "the office does not know where the vault is, so there is no feed to read."
            : detail
        default: return detail.isEmpty ? "the feed could not be read." : detail
        }
    }
}

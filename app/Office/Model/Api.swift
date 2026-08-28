import Foundation

/// The only file in this app that touches the network.
///
/// The office server is a python process on loopback and it is the app's entire
/// backend. Everything above this line deals in `Models.swift` values and has no
/// idea whether they came off a socket or off a fixture, which is exactly what
/// makes `--demo` worth having: the demo floor exercises the real views, not a
/// parallel set of them.
///
/// Writes are `application/json` because the server refuses anything else, and
/// carry no Origin header because a native app has none, which the server reads
/// as same-origin.

public struct ApiError: LocalizedError {
    public let status: Int
    public let message: String
    public var errorDescription: String? { message }
    /// A gate answer that lost its race. Shown, never retried with another id.
    public var isConflict: Bool { status == 409 }
    public var isMissing: Bool { status == 404 }
}

public enum ApiSource {
    case live(URL)
    case demo(URL)
}

public final class Api {
    public let source: ApiSource
    private let session: URLSession
    private let demo: DemoFloor?

    public init(source: ApiSource) {
        self.source = source
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 20
        config.waitsForConnectivity = false
        self.session = URLSession(configuration: config)
        if case .demo(let url) = source {
            self.demo = DemoFloor(url: url)
        } else {
            self.demo = nil
        }
    }

    public var isDemo: Bool { demo != nil }

    public var describedSource: String {
        switch source {
        case .live(let url): return url.absoluteString
        case .demo(let url): return "demo: \(url.lastPathComponent)"
        }
    }

    /// Where the office is, in the order a person would expect: an explicit
    /// `--url`, then `OFFICE_URL`, then the one address it is ever on.
    public static func resolve(arguments: [String] = CommandLine.arguments,
                               environment: [String: String] = ProcessInfo.processInfo.environment) -> Api {
        if let path = flag("--demo", in: arguments) ?? environment["OFFICE_DEMO"], !path.isEmpty {
            return Api(source: .demo(URL(fileURLWithPath: (path as NSString).expandingTildeInPath)))
        }
        let raw = flag("--url", in: arguments) ?? environment["OFFICE_URL"] ?? "http://127.0.0.1:8790"
        let url = URL(string: raw) ?? URL(string: "http://127.0.0.1:8790")!
        return Api(source: .live(url))
    }

    public static func flag(_ name: String, in arguments: [String]) -> String? {
        guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else { return nil }
        let value = arguments[index + 1]
        return value.hasPrefix("--") ? nil : value
    }

    public static func hasFlag(_ name: String, in arguments: [String] = CommandLine.arguments) -> Bool {
        arguments.contains(name)
    }

    // MARK: - reads

    public func bots() async throws -> BotsResponse {
        if let demo { return try demo.bots() }
        return try await get("/api/bots", as: BotsResponse.self)
    }

    /// Every hand in the air, oldest first.
    ///
    /// The floor is a room and not a queue of one, so this is what the app
    /// polls. `gate()` below is the same door's older, single answer, kept for
    /// exactly one purpose: a server that has not learned this route yet must
    /// still be able to show a person a raised hand.
    public func gates() async throws -> GatesResponse {
        if let demo { return try demo.gates() }
        return try await get("/api/gates", as: GatesResponse.self)
    }

    public func gate() async throws -> Gate {
        if let demo { return try demo.gate() }
        return try await get("/api/gate", as: Gate.self)
    }

    public func world() async throws -> World {
        if let demo { return try demo.world() }
        return try await get("/api/world", as: WorldResponse.self).world
    }

    public func chat(bot: String) async throws -> [ChatTurn] {
        if let demo { return try demo.chat(bot: bot) }
        let escaped = bot.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? bot
        return try await get("/api/chat?bot=\(escaped)", as: ChatResponse.self).turns
    }

    /// Marks the fixture wants drawn, as thread → turn index → reaction name.
    ///
    /// **Always empty on the real door, and that is the whole design.** A
    /// reaction is local to this Mac: it never travels to the door, the harness
    /// or GitHub, so there is no endpoint here to call and there is not going to
    /// be one. This exists only so `--demo` can put a mark on a bubble, because
    /// marks live in `UserDefaults` and `shoot.sh` runs a fresh app against a
    /// JSON floor — without it every framing would photograph a thread with
    /// nothing on it and the harness could never tell this feature working from
    /// this feature deleted.
    ///
    /// Indexed by position rather than by turn key, because a fixture is a
    /// static file a person edits and the keys are hashes nobody can type. The
    /// position is resolved against the loaded turns before anything is stored,
    /// so nothing keyed by position ever reaches disk.
    public func reactionSeeds() -> [String: [Int: String]] {
        demo?.reactionSeeds() ?? [:]
    }

    /// The automation, as one page. Off the snapshot the server already built,
    /// never a fresh measurement: a page that re-measures on open disagrees with
    /// the card that sent you to it.
    public func automation() async throws -> Automation {
        if let demo { return try demo.automation() }
        return try await get("/api/automation", as: AutomationResponse.self).automation
    }

    /// Every agent running on this machine, or the ones at one desk.
    public func sessions(repo: String = "") async throws -> SessionRoster {
        if let demo { return try demo.sessions(repo: repo) }
        guard !repo.isEmpty else { return try await get("/api/sessions", as: SessionRoster.self) }
        let escaped = repo.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? repo
        return try await get("/api/sessions?repo=\(escaped)", as: SessionRoster.self)
    }

    /// A desk's README, read off this machine by the door.
    ///
    /// Its own route rather than a field on the station: it is read from disk
    /// and not from GitHub, it is only wanted when a desk has nothing open on
    /// it, and putting every repo's front page in every snapshot would grow the
    /// poll by the size of the office's documentation.
    public func readme(repo: String) async throws -> DeskReadme {
        if let demo { return try demo.readme(repo: repo) }
        let escaped = repo.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? repo
        return try await get("/api/readme?repo=\(escaped)", as: DeskReadme.self)
    }

    public func sessionTranscript(name: String, last: Int = 10) async throws -> SessionTranscript {
        if let demo { return try demo.sessionTranscript(name: name) }
        let escaped = name.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? name
        return try await get("/api/session?name=\(escaped)&last=\(last)", as: SessionTranscript.self)
    }

    /// Which desks a person has put away. The whole list every time, never a
    /// delta: a list can be reconciled against the world, a delta can only be
    /// believed.
    public func hiddenDesks() async throws -> [String] {
        if let demo { return try demo.hiddenDesks() }
        return try await get("/api/desks", as: DesksResponse.self).hidden
    }

    // MARK: - writes

    /// Returns as soon as the server has taken the message. A turn is a whole
    /// agent run, so the reply arrives by the roster changing under the poll
    /// loop, not by this call finishing.
    ///
    /// A picture rides as `attachments`, and only ever as a list of one: the
    /// door refuses two rather than trimming to one, because a quietly dropped
    /// attachment is a turn about a screenshot nobody sent. A turn without a
    /// picture grows no new key at all, so nothing already talking to the
    /// harness has to learn a shape to keep working.
    public func send(bot: String, message: String,
                     attachment: PreparedImage? = nil) async throws {
        if let demo {
            try demo.send(bot: bot, message: message, attachment: attachment)
            return
        }
        var payload: [String: Any] = ["bot": bot, "message": message]
        if let attachment {
            payload["attachments"] = [["name": attachment.name,
                                       "mime_type": attachment.mimeType,
                                       "data_base64": attachment.base64]]
        }
        _ = try await post("/api/chat", payload)
    }

    /// Answer the raised hand, always by the id of the question that was shown.
    public func answerGate(id: String, answer: String, always: Bool) async throws -> Ack {
        if let demo { return try demo.answerGate(id: id, answer: answer, always: always) }
        return try await post("/api/gate", ["question_id": id, "answer": answer, "always": always])
    }

    /// Put a desk away, or bring it back. Answers with the whole put-away list,
    /// which is what the roster reconciles its optimistic flip against.
    public func setDesk(repo: String, hidden: Bool) async throws -> [String] {
        if let demo { return try demo.setDesk(repo: repo, hidden: hidden) }
        return try await post("/api/desks", ["repo": repo, "hidden": hidden],
                              as: DesksResponse.self).hidden
    }

    /// Replace the pin order, whole. Answers with the order the door kept,
    /// which is what the roster reconciles its optimistic drop against.
    public func setPins(_ pins: [String]) async throws -> [String] {
        if let demo { return try demo.setPins(pins) }
        return try await post("/api/pins", ["pins": pins], as: PinsResponse.self).pins
    }

    /// Answer a running session without opening its terminal.
    ///
    /// A message, never a keystroke: it lands in the agent's queue and it reads
    /// it at its next hook. The server refuses one addressed to an agent that
    /// would never read it, and that refusal arrives here as a 409 with the
    /// server's own words, which is the only honest thing to put on screen.
    public func saySession(name: String, text: String) async throws -> Ack {
        if let demo { return try demo.saySession(name: name, text: text) }
        return try await post("/api/session/say", ["name": name, "text": text])
    }

    /// Open a new Claude Code or Codex session at a desk.
    ///
    /// This runs a real program with real credentials, so the engine and the
    /// place are both named rather than typed: `tool` is one of two values the
    /// server matches exactly, and `repo` is a desk the office already knows.
    public func startSession(tool: String, repo: String, prompt: String = "") async throws -> Ack {
        var payload: [String: Any] = ["tool": tool, "repo": repo]
        if !prompt.isEmpty { payload["prompt"] = prompt }
        if let demo { return try demo.startSession(payload) }
        return try await post("/api/session/start", payload)
    }

    public func decide(kind: String, repo: String, issue: String,
                       body: String? = nil, label: String? = nil, pr: Int? = nil) async throws -> Ack {
        var payload: [String: Any] = ["kind": kind, "repo": repo, "issue": issue]
        if let body { payload["body"] = body }
        if let label { payload["label"] = label }
        if let pr { payload["pr"] = pr }
        if let demo { return try demo.decide(payload) }
        return try await post("/api/decision", payload)
    }

    // MARK: - the socket

    private func base() throws -> URL {
        guard case .live(let url) = source else {
            throw ApiError(status: 0, message: "no server configured")
        }
        return url
    }

    private func get<T: Decodable>(_ path: String, as type: T.Type) async throws -> T {
        guard let url = URL(string: path, relativeTo: try base()) else {
            throw ApiError(status: 0, message: "bad path \(path)")
        }
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "accept")
        let (data, response) = try await run(request)
        try check(data: data, response: response)
        return try decode(data, as: type)
    }

    private func post(_ path: String, _ payload: [String: Any]) async throws -> Ack {
        try await post(path, payload, as: Ack.self)
    }

    private func post<T: Decodable>(_ path: String, _ payload: [String: Any],
                                    as type: T.Type) async throws -> T {
        guard let url = URL(string: path, relativeTo: try base()) else {
            throw ApiError(status: 0, message: "bad path \(path)")
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "content-type")
        request.setValue("application/json", forHTTPHeaderField: "accept")
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (data, response) = try await run(request)
        try check(data: data, response: response)
        return try decode(data, as: type)
    }

    private func run(_ request: URLRequest) async throws -> (Data, URLResponse) {
        do {
            return try await session.data(for: request)
        } catch {
            // A refused connection is the normal state of a server nobody
            // started. It is reported as itself, not as a mystery.
            throw ApiError(status: 0, message: shortReason(error))
        }
    }

    private func shortReason(_ error: Error) -> String {
        let ns = error as NSError
        if ns.domain == NSURLErrorDomain {
            switch ns.code {
            case NSURLErrorCannotConnectToHost, NSURLErrorCannotFindHost:
                return "the office server is not running"
            case NSURLErrorTimedOut:
                return "the office server did not answer"
            default: break
            }
        }
        return ns.localizedDescription
    }

    private func check(data: Data, response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            // The server's own words wherever it gave any. A rewritten failure
            // is a guess, and a guess about a refused write is the worst kind.
            let ack = try? decode(data, as: Ack.self)
            let said = [ack?.error, ack?.message, ack?.result]
                .compactMap { $0 }
                .first { !$0.isEmpty }
            throw ApiError(status: http.statusCode, message: said ?? "the server said \(http.statusCode)")
        }
    }

    private func decode<T: Decodable>(_ data: Data, as type: T.Type) throws -> T {
        do {
            return try JSONDecoder().decode(type, from: data)
        } catch {
            throw ApiError(status: 0, message: "could not read the answer: \(error.localizedDescription)")
        }
    }
}

// MARK: - the fake floor

/// `--demo <path>` reads a fixture instead of the network.
///
/// Every state appears in it at least once on purpose: a demo that only shows
/// the happy path is a demo that lets the ugly cases rot. Writes land in memory
/// so the demo floor answers a click the same way the real one does, which is
/// what makes it worth photographing.
private final class DemoFloor {
    private struct Fixture: Decodable {
        var bots: BotsResponse
        var world: World
        /// Every hand up, oldest first. A fixture written before the floor could
        /// hold two of them says `gate` instead, and one raised hand is a list
        /// of one: the old shape keeps working rather than photographing an
        /// office where nobody is waiting.
        var gates: [Gate]
        var chats: [String: [ChatTurn]]
        /// The agents running on the demo machine. Its own key rather than a
        /// corner of `world`, because on the real door it is its own route: it
        /// is measured by asking hcom, not by building a snapshot.
        var sessions: SessionRoster
        var transcripts: [String: SessionTranscript]
        /// thread → turn index → reaction name, for `--demo` only. The index
        /// stays a `String` here because that is what a JSON object key is:
        /// decoding it as `[Int: String]` makes `JSONDecoder` expect an array of
        /// alternating keys and values, not the object a person would write.
        var reactions: [String: [String: String]]
        /// A desk's front page, by repo. Absent for a desk the fixture does
        /// not have one for, which is the real "not checked out here" state.
        var readmes: [String: String]

        var gate: Gate { gates.first ?? .clear }

        enum CodingKeys: String, CodingKey {
            case bots, world, gate, gates, chats, sessions, transcripts, reactions, readmes
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            bots = try c.decode(BotsResponse.self, forKey: .bots)
            world = try c.decode(World.self, forKey: .world)
            let listed = c.list(.gates, Lenient<Gate>.self).compactMap(\.value)
            let single = ((try? c.decodeIfPresent(Gate.self, forKey: .gate)) ?? nil) ?? .clear
            gates = listed.isEmpty ? (single.isPending ? [single] : []) : listed
            chats = ((try? c.decodeIfPresent([String: [ChatTurn]].self, forKey: .chats)) ?? nil) ?? [:]
            // A fixture written before sessions existed photographs a machine
            // with no hcom on it, which is a real state and draws as one.
            sessions = ((try? c.decodeIfPresent(SessionRoster.self, forKey: .sessions)) ?? nil)
                ?? SessionRoster(state: "unavailable",
                                 detail: "this fixture has no sessions in it")
            transcripts = ((try? c.decodeIfPresent([String: SessionTranscript].self,
                                                   forKey: .transcripts)) ?? nil) ?? [:]
            reactions = ((try? c.decodeIfPresent([String: [String: String]].self,
                                                 forKey: .reactions)) ?? nil) ?? [:]
            readmes = ((try? c.decodeIfPresent([String: String].self, forKey: .readmes)) ?? nil) ?? [:]
        }

        /// An empty floor, for a fixture that would not load.
        init() {
            bots = BotsResponse(bots: [], runtime: "down")
            world = World()
            gates = []
            chats = [:]
            reactions = [:]
            sessions = SessionRoster(state: "unavailable", detail: "no fixture loaded")
            transcripts = [:]
            readmes = [:]
        }
    }

    private let lock = NSLock()
    private var fixture: Fixture

    init(url: URL) {
        // A fixture that will not load is a broken demo, not a broken app: it
        // falls back to the copy inside the bundle, then to an empty floor, and
        // the roster says the server is quiet rather than showing nothing.
        let onDisk = try? Data(contentsOf: url)
        let bundled = Bundle.main.url(forResource: "demo", withExtension: "json")
            .flatMap { try? Data(contentsOf: $0) }
        fixture = Self.load(onDisk) ?? Self.load(bundled) ?? Fixture()
    }

    private static func load(_ data: Data?) -> Fixture? {
        guard let data else { return nil }
        return try? JSONDecoder().decode(Fixture.self, from: freshened(data))
    }

    /// Slide every timestamp in the fixture forward so the newest one is now.
    ///
    /// A demo floor whose clocks are frozen in the month it was written reads as
    /// a dead office, and the roster's whole right hand column becomes a list of
    /// last year's dates. The offsets between events are what carry the meaning,
    /// so they are preserved exactly and only the origin moves.
    private static func freshened(_ data: Data) -> Data {
        guard let text = String(data: data, encoding: .utf8),
              let pattern = try? NSRegularExpression(
                pattern: "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
        else { return data }

        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let whole = NSRange(text.startIndex..., in: text)
        let matches = pattern.matches(in: text, range: whole)
        let stamps = matches.compactMap { match -> Date? in
            Range(match.range, in: text).flatMap { formatter.date(from: String(text[$0])) }
        }
        guard let newest = stamps.max() else { return data }
        let shift = Date().timeIntervalSince(newest)
        guard shift > 60 else { return data }

        var out = text
        for match in matches.reversed() {
            guard let range = Range(match.range, in: out),
                  let when = formatter.date(from: String(out[range])) else { continue }
            out.replaceSubrange(range, with: formatter.string(from: when.addingTimeInterval(shift)))
        }
        return Data(out.utf8)
    }

    func bots() throws -> BotsResponse { lock.withLock { fixture.bots } }
    func gate() throws -> Gate { lock.withLock { fixture.gate } }

    func gates() throws -> GatesResponse {
        lock.withLock { GatesResponse(at: Self.now(), gates: fixture.gates) }
    }
    func world() throws -> World { lock.withLock { fixture.world } }

    func chat(bot: String) throws -> [ChatTurn] {
        lock.withLock { fixture.chats[bot] ?? [] }
    }

    /// An index the fixture wrote as text becomes the number it meant. A key
    /// that is not a number is dropped rather than defaulted to zero, which
    /// would silently mark the first turn of the thread.
    func reactionSeeds() -> [String: [Int: String]] {
        lock.withLock {
            fixture.reactions.mapValues { rows in
                // `uniquingKeysWith` rather than `uniqueKeysWithValues`, which
                // traps on a collision: "2" and "02" are two JSON keys and one
                // integer, and a fixture typo must cost a mark, never the room
                // this fixture exists to photograph.
                Dictionary(rows.compactMap { key, value in Int(key).map { ($0, value) } },
                           uniquingKeysWith: { first, _ in first })
            }
        }
    }

    func automation() throws -> Automation { lock.withLock { fixture.world.automation } }

    /// The same filtering the real door does, so the demo floor cannot show a
    /// desk a session that is not sitting at it.
    func sessions(repo: String) throws -> SessionRoster {
        lock.withLock {
            guard !repo.isEmpty else { return fixture.sessions }
            var out = fixture.sessions
            out.sessions = out.sessions.filter { $0.repo == repo }
            out.live = out.sessions.filter(\.isAlive).count
            out.blocked = out.sessions.filter(\.isBlocked).count
            if out.state == "ok" && out.sessions.isEmpty { out.state = "empty" }
            return out
        }
    }

    /// Two of the four answers the real door gives, so the demo floor cannot
    /// photograph a desk saying nothing.
    ///
    /// `none` and `unreadable` are not reachable from a fixture, which is a gap
    /// stated rather than hidden: a JSON floor has no folder to be missing a
    /// README from. They are pinned in `tests/test_sessions.py` instead, which
    /// is the weaker proof of the two and the only one available here.
    func readme(repo: String) throws -> DeskReadme {
        lock.withLock {
            guard let text = fixture.readmes[repo], !text.isEmpty else {
                return DeskReadme(repo: repo, state: "elsewhere",
                                  detail: "\(repo) is not checked out on this machine")
            }
            return DeskReadme(repo: repo, state: "ok", name: "README.md", text: text)
        }
    }

    func sessionTranscript(name: String) throws -> SessionTranscript {
        lock.withLock { fixture.transcripts[name] ?? SessionTranscript() }
    }

    /// Answering on the demo floor lands in the transcript, so the fixture
    /// behaves like the door: a reply that vanished on the next poll would make
    /// the demo lie about the one control this view has.
    func saySession(name: String, text: String) throws -> Ack {
        lock.withLock {
            var script = fixture.transcripts[name] ?? SessionTranscript()
            script.exchanges.append(SessionTranscript.Exchange(
                position: script.exchanges.count + 1, you: text,
                them: "(the demo floor takes the message and runs nothing)",
                at: Self.now()))
            fixture.transcripts[name] = script
            return Ack(ok: true, result: "queued on the demo floor")
        }
    }

    /// The demo floor starts nothing. It says so, rather than answering `ok` to
    /// a launch that never happened: a fake green on the one control that runs a
    /// program is the worst place in this app to have one.
    func startSession(_ payload: [String: Any]) throws -> Ack {
        throw ApiError(status: 501,
                       message: "the demo floor cannot start a session; this is a fixture")
    }

    func hiddenDesks() throws -> [String] {
        lock.withLock { fixture.world.stations.filter(\.hidden).map(\.repo) }
    }

    /// The demo floor flips the fixture in memory, exactly as the real door
    /// flips its file. A put-away that springs back on the next poll would make
    /// the demo lie about the one thing this control does.
    func setDesk(repo: String, hidden: Bool) throws -> [String] {
        lock.withLock {
            if let index = fixture.world.stations.firstIndex(where: { $0.repo == repo }) {
                fixture.world.stations[index].hidden = hidden
            }
            return fixture.world.stations.filter(\.hidden).map(\.repo)
        }
    }

    /// Same rule as `setDesk`: the fixture is the door, so the order sticks.
    func setPins(_ pins: [String]) throws -> [String] {
        lock.withLock {
            fixture.world.pins = pins
            for index in fixture.world.stations.indices {
                fixture.world.stations[index].pinned = pins.firstIndex(of: fixture.world.stations[index].repo)
            }
            return pins
        }
    }

    func send(bot: String, message: String, attachment: PreparedImage? = nil) throws {
        lock.withLock {
            var turns = fixture.chats[bot] ?? []
            turns.append(ChatTurn(role: "user", content: message, at: Self.now(),
                                  hasPhoto: attachment != nil))
            turns.append(ChatTurn(role: "assistant",
                                  content: "Demo floor: nothing is actually running, so this is the "
                                         + "only answer there is. Point the app at a live office to talk properly.",
                                  at: Self.now()))
            fixture.chats[bot] = turns
            if let index = fixture.bots.bots.firstIndex(where: { $0.id == bot }) {
                fixture.bots.bots[index].last = turns.last
            }
        }
    }

    /// Answer one hand, by its id, and leave the rest of the room alone.
    ///
    /// The answered question is taken out of the list and whatever was behind it
    /// is still up. A demo floor that cleared every gate on one click would make
    /// the one thing this fixture exists to show, two people waiting at once,
    /// unphotographable.
    func answerGate(id: String, answer: String, always: Bool) throws -> Ack {
        try lock.withLock {
            guard let index = fixture.gates.firstIndex(where: { $0.isPending && $0.id == id }) else {
                throw ApiError(status: 409, message: fixture.gates.contains(where: \.isPending)
                    ? "the agent has moved on; this answer was for an older question"
                    : "that question is gone; the agent stopped waiting")
            }
            let permission = fixture.gates[index].permission
            fixture.gates.remove(at: index)
            let onTop = fixture.gate
            fixture.world.runtime?.gate = onTop
            return Ack(ok: true, message: answer + (always ? " (always)" : "") + " for \(permission)")
        }
    }

    func decide(_ payload: [String: Any]) throws -> Ack {
        let kind = payload["kind"] as? String ?? "decision"
        let repo = payload["repo"] as? String ?? ""
        return Ack(ok: true, result: "demo floor: \(kind) on \(repo) was not sent anywhere")
    }

    private static func now() -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.string(from: Date())
    }
}

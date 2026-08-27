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

    // MARK: - writes

    /// Returns as soon as the server has taken the message. A turn is a whole
    /// agent run, so the reply arrives by the roster changing under the poll
    /// loop, not by this call finishing.
    public func send(bot: String, message: String) async throws {
        if let demo {
            try demo.send(bot: bot, message: message)
            return
        }
        _ = try await post("/api/chat", ["bot": bot, "message": message])
    }

    /// Answer the raised hand, always by the id of the question that was shown.
    public func answerGate(id: String, answer: String, always: Bool) async throws -> Ack {
        if let demo { return try demo.answerGate(id: id, answer: answer, always: always) }
        return try await post("/api/gate", ["question_id": id, "answer": answer, "always": always])
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
        return try decode(data, as: Ack.self)
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
        var gate: Gate
        var chats: [String: [ChatTurn]]

        enum CodingKeys: String, CodingKey { case bots, world, gate, chats }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            bots = try c.decode(BotsResponse.self, forKey: .bots)
            world = try c.decode(World.self, forKey: .world)
            gate = ((try? c.decodeIfPresent(Gate.self, forKey: .gate)) ?? nil) ?? .clear
            chats = ((try? c.decodeIfPresent([String: [ChatTurn]].self, forKey: .chats)) ?? nil) ?? [:]
        }

        /// An empty floor, for a fixture that would not load.
        init() {
            bots = BotsResponse(bots: [], runtime: "down")
            world = World()
            gate = .clear
            chats = [:]
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
    func world() throws -> World { lock.withLock { fixture.world } }

    func chat(bot: String) throws -> [ChatTurn] {
        lock.withLock { fixture.chats[bot] ?? [] }
    }

    func send(bot: String, message: String) throws {
        lock.withLock {
            var turns = fixture.chats[bot] ?? []
            turns.append(ChatTurn(role: "user", content: message, at: Self.now()))
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

    func answerGate(id: String, answer: String, always: Bool) throws -> Ack {
        try lock.withLock {
            guard fixture.gate.isPending else {
                throw ApiError(status: 409, message: "that question is gone; the agent stopped waiting")
            }
            guard fixture.gate.id == id else {
                throw ApiError(status: 409,
                               message: "the agent has moved on; this answer was for an older question")
            }
            let permission = fixture.gate.permission
            fixture.gate = .clear
            fixture.world.runtime?.gate = .clear
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

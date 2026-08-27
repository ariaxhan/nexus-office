import Foundation
import Observation
#if canImport(UserNotifications)
import UserNotifications
#endif

/// Everything on screen, and the loop that keeps it true.
///
/// The office server has no push channel, so the app polls: the roster and the
/// gate every two seconds because both are cheap and both are the difference
/// between a person noticing and a person not, and the world every ten because
/// it is a GitHub snapshot that only rebuilds once a minute anyway.
///
/// No network code lives here. Every call goes through `Api`.

public enum Selection: Hashable {
    case bot(String)
    case desk(String)
}

public enum DotState {
    case idle, working, needsYou

    var title: String {
        switch self {
        case .idle: return "the floor is quiet"
        case .working: return "someone is working"
        case .needsYou: return "an agent is waiting on you"
        }
    }
}

@MainActor
@Observable
public final class Store {
    /// One store for the whole process. The menu bar dot and the window are two
    /// views of the same office, and the dot has to keep working when the window
    /// is closed, so neither of them can own it.
    public static let shared = Store(api: Api.resolve())

    public let api: Api

    // what the server said
    public private(set) var bots: [Bot] = []
    public private(set) var runtimeUp = false
    public private(set) var botsNotice: String?
    public private(set) var stations: [Station] = []
    public private(set) var worldNotice: String?
    public private(set) var gate: Gate = .clear
    public private(set) var chats: [String: [ChatTurn]] = [:]

    // what the person did
    public var selection: Selection?
    public var query: String = ""
    public var needsOnly = false
    public var toast: String?
    public var gateNotice: String?

    /// Shot mode only. The gate sheet is the loudest thing this app does, and it
    /// would sit on top of every other framing, so the harness parks it for the
    /// three pictures that are not about it. Nothing else may ever set this.
    public var suppressGateSheet = false

    private var lastAnnouncedGate: String?
    private var loops: [Task<Void, Never>] = []

    public init(api: Api) {
        self.api = api
    }

    // MARK: - derived

    public var gateIsPending: Bool { gate.isPending }

    public var showsGateSheet: Bool { gate.isPending && !suppressGateSheet }

    public var dot: DotState {
        if gate.isPending { return .needsYou }
        if stations.contains(where: { StateRules.deskState($0) == .waiting }) { return .needsYou }
        if bots.contains(where: { $0.busy }) { return .working }
        if stations.contains(where: { StateRules.deskState($0) == .working }) { return .working }
        return .idle
    }

    public var visibleBots: [Bot] { StateRules.visibleBots(bots, query: query) }

    public var visibleDesks: [Station] {
        StateRules.visibleDesks(stations, query: query, needsOnly: needsOnly)
    }

    public var waitingCount: Int { StateRules.waitingCount(stations) }

    public func bot(_ id: String) -> Bot? { bots.first { $0.id == id } }
    public func station(_ repo: String) -> Station? { stations.first { $0.repo == repo } }

    /// The gate belongs to a bot's thread when the server says which bot raised
    /// it. When it does not, it still gets a desk, and it is still never hidden.
    public func gateBelongsTo(bot id: String) -> Bool {
        gate.isPending && gate.bot == id
    }

    // MARK: - the loop

    public func start() {
        guard loops.isEmpty else { return }
        loops.append(Task { await self.pollFast() })
        loops.append(Task { await self.pollWorld() })
        askToNotify()
    }

    public func stop() {
        loops.forEach { $0.cancel() }
        loops.removeAll()
    }

    private func pollFast() async {
        while !Task.isCancelled {
            await refreshBots()
            await refreshGate()
            await refreshSelectedChat()
            try? await Task.sleep(nanoseconds: 2_000_000_000)
        }
    }

    private func pollWorld() async {
        while !Task.isCancelled {
            await refreshWorld()
            try? await Task.sleep(nanoseconds: 10_000_000_000)
        }
    }

    public func refreshBots() async {
        do {
            let answer = try await api.bots()
            bots = answer.bots
            runtimeUp = answer.runtime == "up"
            botsNotice = answer.bots.isEmpty ? "bots: the server has no roster yet" : nil
        } catch let error as ApiError {
            // A server that predates the chatroom answers 404 here. That is a
            // floor with desks and no bots, not a broken app, and it says so.
            bots = []
            runtimeUp = false
            botsNotice = error.isMissing
                ? "bots: the server has no roster yet"
                : "bots: \(error.message)"
        } catch {
            bots = []
            botsNotice = "bots: \(error.localizedDescription)"
        }
    }

    public func refreshGate() async {
        do {
            let next = try await api.gate()
            gate = next
            if next.isPending {
                if lastAnnouncedGate != next.id {
                    lastAnnouncedGate = next.id
                    announce(next)
                }
            } else {
                lastAnnouncedGate = nil
                gateNotice = nil
            }
        } catch {
            // A gate that cannot be read is not a gate that is clear. The last
            // known state is kept rather than quietly downgraded to "fine".
            gateNotice = nil
        }
    }

    public func refreshWorld() async {
        do {
            let world = try await api.world()
            stations = StateRules.attachGate(stations: world.stations.sorted { $0.repo < $1.repo },
                                             runtime: world.runtime,
                                             gate: gate.isPending ? gate : world.runtime?.gate)
            worldNotice = world.killed ? "the kill switch is on: nothing will run" : nil
        } catch let error as ApiError {
            worldNotice = error.message
        } catch {
            worldNotice = error.localizedDescription
        }
    }

    public func refreshSelectedChat() async {
        guard case .bot(let id)? = selection else { return }
        await loadChat(id)
    }

    public func loadChat(_ id: String) async {
        do {
            chats[id] = try await api.chat(bot: id)
        } catch let error as ApiError where error.isMissing {
            chats[id] = []
        } catch {
            // Leave whatever was last read on screen. A thread that empties
            // itself because the harness blinked is a lie about the history.
        }
    }

    // MARK: - what a click does

    public func select(_ selection: Selection) {
        self.selection = selection
        if case .bot(let id) = selection {
            Task { await loadChat(id) }
        }
    }

    public func send(to id: String, message: String) async {
        let trimmed = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        // Show it immediately. The turn takes a whole agent run, and a message
        // that vanishes for ninety seconds reads as a message that was lost.
        chats[id, default: []].append(ChatTurn(role: "user", content: trimmed, at: nowISO()))
        do {
            try await api.send(bot: id, message: trimmed)
            await refreshBots()
            await loadChat(id)
        } catch let error as ApiError {
            toast = error.message
        } catch {
            toast = error.localizedDescription
        }
    }

    public func answerGate(_ answer: String, always: Bool) async {
        let id = gate.id
        guard !id.isEmpty else { return }
        do {
            let ack = try await api.answerGate(id: id, answer: answer, always: always)
            gateNotice = ack.spoken
            await refreshGate()
        } catch let error as ApiError {
            // A conflict means the agent moved on. It is shown and never retried
            // with another id: answering by anything but the id that was on
            // screen would approve a command nobody saw.
            gateNotice = error.message
            await refreshGate()
        } catch {
            gateNotice = error.localizedDescription
        }
    }

    public func decide(kind: String, repo: String, issue: String,
                       body: String? = nil, pr: Int? = nil) async -> Bool {
        do {
            let ack = try await api.decide(kind: kind, repo: repo, issue: issue, body: body, pr: pr)
            toast = ack.spoken
            await refreshWorld()
            return ack.ok
        } catch let error as ApiError {
            toast = error.message
            return false
        } catch {
            toast = error.localizedDescription
            return false
        }
    }

    // MARK: - the alert

    private func askToNotify() {
        #if canImport(UserNotifications)
        guard Bundle.main.bundleIdentifier != nil else { return }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        #endif
    }

    private func announce(_ gate: Gate) {
        #if canImport(UserNotifications)
        guard Bundle.main.bundleIdentifier != nil else { return }
        let content = UNMutableNotificationContent()
        content.title = "An agent is asking permission"
        content.subtitle = gate.permission
        content.body = StateRules.line(gate.target, limit: 160)
        content.sound = .default
        let request = UNNotificationRequest(identifier: "gate-\(gate.id)", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request) { _ in }
        #endif
    }

    private func nowISO() -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f.string(from: Date())
    }
}

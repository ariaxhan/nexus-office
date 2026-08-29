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
    /// One thing on the wall, by its source id. A section is not a repo and not
    /// a colleague, so it gets its own case rather than borrowing a desk's.
    case section(String)
}

/// The two halves of a desk: what is open on GitHub, and what the checkout on
/// this machine says about itself.
public enum DeskTab: String, Hashable, CaseIterable {
    case work, context

    public var label: String {
        switch self {
        case .work: return "Work"
        case .context: return "Context"
        }
    }
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
    public static let shared = Store(api: Api.resolve(), prefs: .shared)

    public let api: Api

    /// The choices that outlive the launch. In memory unless somebody hands
    /// this store the persisting one, so a test and a shoot cannot write to
    /// the machine they run on.
    public let prefs: Preferences

    // what the server said
    public private(set) var bots: [Bot] = []
    public private(set) var runtimeUp = false
    public private(set) var botsNotice: String?
    public private(set) var stations: [Station] = []
    /// The wall, as the world poll last reported it. Already in a stable order:
    /// the key order of a JSON object is not one.
    public private(set) var sections: [Section] = []
    public private(set) var worldNotice: String?
    /// When the snapshot on screen was built. A desk whose own `fetched_at` is
    /// older than this is showing last-good data, which is a thing the desk has
    /// to say out loud rather than a thing a person has to work out.
    public private(set) var worldGenerated: String = ""
    public private(set) var github: GitHubBudget?
    /// The pin order as the door last reported it.
    public private(set) var pins: [String] = []
    /// Whose office this is, first name first.
    public private(set) var owners: [String] = []
    /// Every hand in the air, oldest first, exactly as the door listed them.
    /// The room can hold more than one, and the second one is not allowed to
    /// wait invisibly behind the first.
    public private(set) var gates: [Gate] = []
    /// The door's own word for an empty floor: `clear`, or `down` when the
    /// harness is not there to ask. Only ever read when `gates` is empty.
    private var quiet: Gate = .clear
    public private(set) var chats: [String: [ChatTurn]] = [:]
    /// The automation, as one page. Off the world poll, so it is exactly as old
    /// as the room around it and never disagrees with the Pipeline card.
    public private(set) var automation: Automation = Automation()
    /// Every agent running on this machine, keyed by desk. Its own poll, because
    /// it is measured by asking hcom rather than by building a snapshot, and it
    /// changes on the scale of a tool call rather than of a GitHub budget.
    public private(set) var sessionsAtDesk: [String: SessionRoster] = [:]
    /// The whole machine, for the office-wide view.
    public private(set) var allSessions: SessionRoster = SessionRoster()
    /// One agent's conversation, loaded when a person opens it.
    public private(set) var sessionTranscripts: [String: SessionTranscript] = [:]
    /// Which agent's thread is open, if any.
    public var openSession: String?

    // MARK: - what a desk says about itself
    //
    // Per desk, all three of them, because they are three different answers and
    // a person looking at one desk must never see another desk's. Loading is
    // not "no files yet" and an error is not "no files at all": a desk the
    // office cannot place has to say so, and an empty pane says nothing.

    /// The last context read for a desk: its index, and whichever file is open.
    public private(set) var contexts: [String: DeskContext] = [:]
    public private(set) var contextLoading: Set<String> = []
    public private(set) var contextErrors: [String: String] = [:]
    /// Which half of a desk is on screen. Kept here rather than in the view for
    /// the same reason drafts are: switching desks and coming back should not
    /// undo a person's choice, and the shot harness has to be able to open it.
    public private(set) var deskTabs: [String: DeskTab] = [:]
    /// Whether the automation page is on screen. On the store rather than the
    /// view so the shot harness can open it, exactly like `putAwayOpen`.
    public var automationOpen = false

    // what the person did
    public var selection: Selection?
    public var query: String = ""
    public var needsOnly = false {
        didSet { prefs.set(needsOnly: needsOnly) }
    }
    /// How the desks are ordered. A view, never a fetch: nothing here changes
    /// what is polled or what a desk says.
    public var deskSort: StateRules.DeskSort = .owner {
        didSet { prefs.set(deskSort: deskSort) }
    }
    /// How much of the office is on screen. A view, never a fetch: no preset
    /// changes what is polled or what a desk says, and none of them can hide a
    /// raised hand, which arrives as a sheet over the whole window.
    public var layout: LayoutPreset = .focus {
        didSet { prefs.set(layout: layout) }
    }
    /// Which groups the roster draws.
    public var showBots = true {
        didSet { prefs.set(showBots: showBots) }
    }
    public var showWall = true {
        didSet { prefs.set(showWall: showWall) }
    }
    /// The reading type's multiplier. Stepped by Cmd + / Cmd -, reset by Cmd 0.
    public var typeScale: Double = 1 {
        didSet { prefs.set(typeScale: typeScale) }
    }

    public func biggerType() { prefs.stepTypeScale(+1); typeScale = prefs.typeScale }
    public func smallerType() { prefs.stepTypeScale(-1); typeScale = prefs.typeScale }
    public func resetType() { typeScale = 1 }

    /// What is open in the second detail pane, in `compare` only.
    ///
    /// Not persisted: which two desks you were reading is a fact about a
    /// sitting, and restoring a stale pair on launch is a window telling you
    /// something that stopped being true overnight. The preset persists; the
    /// contents of the panes do not.
    public var compared: Selection?

    public var toast: String?
    public var gateNotice: String?
    /// Whether the put-away section is open. On the store rather than the view
    /// because a person's answer to "show me what I put away" should survive
    /// clicking a different desk, and because the shot harness has to be able
    /// to open it to photograph it.
    public var putAwayOpen = false

    /// Which desk's face picker is open, by repo, or `nil`.
    ///
    /// On the store rather than in the row for the same reason `putAwayOpen` is:
    /// a `LazyVStack` throws a row's `@State` away the moment it scrolls off,
    /// and the shot harness has to be able to open the picker to photograph it.
    public var facePicker: String?

    /// Half-typed text, by where it was typed.
    ///
    /// These used to be `@State` inside the composer and the comment box, which
    /// meant SwiftUI tore them down with the view the moment the selection
    /// changed: two sentences into a message, click another name to check
    /// something, come back, gone. A draft is a thing the person made, so it
    /// belongs to the office and not to whichever view happens to be on screen.
    /// Sending clears its own key and nothing else.
    public var drafts: [String: String] = [:]

    /// The picture picked for a message not yet sent, under the same key as the
    /// message itself.
    ///
    /// Same reasoning as the draft and the same key on purpose: choosing a
    /// screenshot, going to look at what it was about, and coming back to an
    /// empty composer is the draft bug wearing a different hat. Sending clears
    /// this key and nobody else's.
    public var pendingAttachments: [String: PreparedImage] = [:]

    /// A message being written to a bot.
    public static func draftKey(bot id: String) -> String { "bot:\(id)" }

    /// A reply being written to a running agent, keyed by its name so two
    /// half-written answers to two agents do not overwrite each other.
    public static func draftKey(session name: String) -> String { "session:\(name)" }

    /// A comment being written on one issue at one desk. Keyed per issue, so
    /// two half-written comments at the same desk do not overwrite each other.
    public static func draftKey(repo: String, issue: Int) -> String { "\(repo)#\(issue)" }

    /// A person's answer to "put this away", believed immediately and checked
    /// against the next world poll. A row that waits ten seconds to move reads
    /// as a click that did not land.
    private var pendingHidden: [String: Bool] = [:]

    /// A pin order believed on the drop, and checked against the next world
    /// poll, for the same reason as `pendingHidden`.
    private var pendingPins: [String]?

    /// Shot mode only. The gate sheet is the loudest thing this app does, and it
    /// would sit on top of every other framing, so the harness parks it for the
    /// three pictures that are not about it. Nothing else may ever set this.
    public var suppressGateSheet = false

    /// The ids already announced, so a second hand going up while the first is
    /// still waiting gets its own alert and neither gets two. Kept as a list so
    /// the rule is provable rather than merely believed.
    public private(set) var announced: [String] = []
    private var announcedIds: Set<String> = []
    /// Where the runtime is working, kept from the last world poll so the gate
    /// poll can re-seat the raised hand without waiting ten seconds for one.
    private var runtime: RuntimeInfo?
    private var loops: [Task<Void, Never>] = []

    /// Whether the settings popover is open. On the store rather than the view
    /// for the same reason `putAwayOpen` is: the shot harness has to be able to
    /// open it to photograph it.
    public var settingsOpen = false

    /// `nil` preferences means an in-memory set, built here rather than as a
    /// default argument: a default is evaluated outside the actor, and a
    /// `@MainActor` type cannot be constructed there.
    public init(api: Api, prefs: Preferences? = nil) {
        self.api = api
        let prefs = prefs ?? Preferences()
        self.prefs = prefs
        // Swift does not run a property observer for a write inside the
        // initialiser, so this is a load and never a save back over the value
        // it just read.
        deskSort = prefs.deskSort
        needsOnly = prefs.needsOnly
        layout = prefs.layout
        showBots = prefs.showBots
        showWall = prefs.showWall
        typeScale = prefs.typeScale
    }

    // MARK: - derived

    /// The oldest hand up: what every surface built before the floor could hold
    /// two of them still reads. Derived from the list, never stored beside it,
    /// so the two can never disagree.
    public var gate: Gate { gates.first ?? quiet }

    public var gateIsPending: Bool { gate.isPending }

    public var showsGateSheet: Bool { gate.isPending && !suppressGateSheet }

    public var dot: DotState {
        if gate.isPending { return .needsYou }
        if stations.contains(where: { StateRules.deskState($0) == .waiting }) { return .needsYou }
        // A source on the wall saying five things want a person is the same
        // claim as a desk waiting on you, so it lights the same dot. Anything
        // else means a person has to open the window to find out, which is the
        // one job this dot has.
        if wallNeeds > 0 { return .needsYou }
        if bots.contains(where: { $0.busy }) { return .working }
        if stations.contains(where: { StateRules.deskState($0) == .working }) { return .working }
        return .idle
    }

    public var visibleBots: [Bot] { StateRules.visibleBots(bots, query: query) }

    public var visibleDesks: [Station] {
        StateRules.visibleDesks(stations, query: query, needsOnly: needsOnly, isHidden: isHidden)
    }

    /// The pin order, as this person last left it.
    public var pinOrder: [String] { pendingPins ?? pins }

    public func isPinned(_ station: Station) -> Bool { pinOrder.contains(station.repo) }

    /// The desks list, grouped: pinned first, then by owner.
    public var roster: [StateRules.RosterSection] {
        StateRules.roster(visibleDesks, pins: pinOrder, owners: owners, sort: deskSort)
    }

    /// Put away by a person: out of the desks list, still in the building.
    public var putAwayDesks: [Station] {
        StateRules.putAwayDesks(stations, isHidden: isHidden)
    }

    public var putAwayHeadline: String {
        StateRules.putAwayHeadline(stations, isHidden: isHidden)
    }

    /// **Hidden is never silent.** Something put away has started needing a
    /// person, and the collapsed header has to say so without being opened.
    public var putAwayNeedsSomeone: Bool {
        StateRules.putAwayNeedingAPerson(stations, isHidden: isHidden) > 0
    }

    public var polledLine: String { StateRules.polledLine(stations, isHidden: isHidden) }

    // MARK: - the wall

    public var visibleSections: [Section] {
        StateRules.visibleSections(sections, query: query, needsOnly: needsOnly)
    }

    /// How many things on the wall want a person right now.
    public var wallNeeds: Int { StateRules.wallNeeds(sections) }

    public var wallLine: String { StateRules.wallLine(sections) }

    public func section(_ id: String) -> Section? { sections.first { $0.id == id } }

    /// What the server says, unless this person has just said otherwise and the
    /// server has not caught up yet.
    public func isHidden(_ station: Station) -> Bool {
        pendingHidden[station.repo] ?? station.hidden
    }

    public var waitingCount: Int { StateRules.waitingCount(stations) }

    public func bot(_ id: String) -> Bot? { bots.first { $0.id == id } }
    public func station(_ repo: String) -> Station? { stations.first { $0.repo == repo } }

    /// The gate belongs to a bot's thread when the server says which bot raised
    /// it. When it does not, it still gets a desk, and it is still never hidden.
    public func gateBelongsTo(bot id: String) -> Bool {
        StateRules.gateBelongsTo(gates: gates, bot: id)
    }

    /// The hand THIS bot has up, which is not always the oldest one on the
    /// floor. A bot second in the queue still draws its own question in its own
    /// thread, because the alternative is a thread that says nothing is
    /// happening while its bot stands there waiting.
    public func gate(for bot: String) -> Gate? {
        StateRules.gate(in: gates, for: bot)
    }

    /// The quiet line under the oldest question: how many are up, and who is
    /// behind this one. Nil when there is only one.
    public var gateQueueLine: String? {
        StateRules.gateQueueLine(gates) { self.bot($0)?.name }
    }

    /// The one gate a desk may draw, and the only one it may answer.
    ///
    /// Every gate on screen comes from here, which is the two second
    /// `/api/gate` poll and nothing else. The copy of the gate carried in the
    /// ten second world snapshot is a photograph of a moment that has already
    /// passed: rendering it put an old question's text above a new question's
    /// buttons, which is how a person approves a command they never read.
    public func gateShown(at station: Station) -> Gate? {
        StateRules.gateShown(gates: gates, at: station, stations: stations)
    }

    // MARK: - the loop

    public func start() {
        guard loops.isEmpty else { return }
        loops.append(Task { await self.pollFast() })
        loops.append(Task { await self.pollWorld() })
        loops.append(Task { await self.pollSessions() })
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

    /// Its own loop, on its own clock. Five seconds, because an agent's status
    /// changes on the scale of a tool call: slower than the gate poll, which is
    /// a question waiting on a person, and faster than the world poll, which is
    /// a GitHub budget. It costs one local subprocess and no network at all.
    private func pollSessions() async {
        while !Task.isCancelled {
            await refreshSessions()
            if let open = openSession { await loadSessionTranscript(open) }
            try? await Task.sleep(nanoseconds: 5_000_000_000)
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
            let listed = try await api.gates()
            apply(listed.gates, quiet: listed.quiet)
        } catch let error as ApiError where error.isMissing {
            // A door that predates this route still knows about one raised
            // hand, and one raised hand nobody can see is the single failure
            // this surface is not allowed to have. So it is asked the old way
            // rather than left blank.
            do {
                let one = try await api.gate()
                apply(one.isPending ? [one] : [], quiet: one)
            } catch {
                // Same as below: last known beats a false all-clear.
            }
        } catch {
            // A gate that cannot be read is not a gate that is clear. The last
            // known state is kept rather than quietly downgraded to "fine".
        }
    }

    /// Take the gate the door just reported, and make the whole floor agree.
    ///
    /// One id at a time. When it changes, the notice about the old question goes
    /// with it, because an answer to a question that is gone reads as an answer
    /// to the one that replaced it, and the desks are re-seated on the spot
    /// rather than eight seconds later when the world poll happens to come round.
    private func apply(_ next: [Gate], quiet state: Gate) {
        let pending = next.filter(\.isPending)
        let moved = pending.first?.id != gates.first?.id
        gates = pending
        quiet = state
        if moved { gateNotice = nil }
        stations = StateRules.attachGates(stations: stations, runtime: runtime, gates: pending)

        // One alert per question, however many hands are up. A second bot
        // arriving while the first is still waiting is its own interruption and
        // gets its own alert; neither of them gets a second one.
        let live = Set(pending.map(\.id))
        announcedIds.formIntersection(live)
        for gate in pending where !announcedIds.contains(gate.id) {
            announcedIds.insert(gate.id)
            announced.append(gate.id)
            if announced.count > 20 { announced.removeFirst(announced.count - 20) }
            announce(gate)
        }
    }

    public func refreshWorld() async {
        do {
            let world = try await api.world()
            runtime = world.runtime
            worldGenerated = world.generated
            github = world.github
            pins = world.pins
            owners = world.owners
            if pendingPins == world.pins { pendingPins = nil }
            // Already ordered by the decode, where the keys were still in hand.
            sections = world.sections
            // The gate comes from the gate poll. `world.runtime.gate` is a
            // cached snapshot of it and is never read: it has been up to ten
            // seconds behind the truth, which is long enough for the question
            // on screen to be one the agent has already given up on.
            stations = StateRules.attachGates(stations: world.stations.sorted { $0.repo < $1.repo },
                                              runtime: world.runtime,
                                              gates: gates)
            // The server has now spoken about what is put away. Wherever it
            // agrees with the click that was believed early, the belief is
            // dropped; where it disagrees, the server wins, because it is the
            // one that decides what actually gets polled.
            for station in stations where pendingHidden[station.repo] == station.hidden {
                pendingHidden.removeValue(forKey: station.repo)
            }
            automation = world.automation
            worldNotice = world.killed ? "the kill switch is on: nothing will run" : nil
        } catch let error as ApiError {
            worldNotice = error.message
        } catch {
            worldNotice = error.localizedDescription
        }
    }

    /// Ask hcom what is running, and file each row under its desk.
    ///
    /// One call for the whole machine, never one per desk: a roster of 72 desks
    /// would otherwise be 72 subprocesses every few seconds to answer a question
    /// one subprocess already answers.
    public func refreshSessions() async {
        do {
            let roster = try await api.sessions()
            allSessions = roster
            var byDesk: [String: SessionRoster] = [:]
            for session in roster.sessions where !session.repo.isEmpty {
                var desk = byDesk[session.repo] ?? SessionRoster(state: "ok", at: roster.at)
                desk.sessions.append(session)
                byDesk[session.repo] = desk
            }
            for repo in byDesk.keys {
                guard var desk = byDesk[repo] else { continue }
                desk.live = desk.sessions.filter(\.isAlive).count
                desk.blocked = desk.sessions.filter(\.isBlocked).count
                byDesk[repo] = desk
            }
            sessionsAtDesk = byDesk
        } catch let error as ApiError {
            // The office could not ask. That is not "nothing is running", and
            // it must not empty a list a person is reading: the last roster
            // stays, and the state says the asking failed.
            allSessions = SessionRoster(state: "unreadable", detail: error.message,
                                        sessions: allSessions.sessions,
                                        live: allSessions.live, blocked: allSessions.blocked,
                                        at: allSessions.at)
        } catch {
            allSessions = SessionRoster(state: "unreadable", detail: error.localizedDescription,
                                        sessions: allSessions.sessions, at: allSessions.at)
        }
    }

    /// What one desk can see. Never invents an empty roster for a desk the
    /// office could not ask about: `canSee` is false and the view says so.
    public func sessions(at repo: String) -> SessionRoster {
        if let desk = sessionsAtDesk[repo] { return desk }
        guard allSessions.canSee else { return allSessions }
        return SessionRoster(state: "empty", at: allSessions.at)
    }

    // MARK: - a desk's own Markdown

    public func tab(at repo: String) -> DeskTab { deskTabs[repo] ?? .work }

    public func context(at repo: String) -> DeskContext? { contexts[repo] }

    public func contextError(at repo: String) -> String? { contextErrors[repo] }

    public func isLoadingContext(at repo: String) -> Bool { contextLoading.contains(repo) }

    /// Show the Context half of a desk, and read it if it has not been read.
    ///
    /// Cached per desk on purpose: the index is a walk of a checkout, and
    /// re-walking it every time somebody clicks back and forth is work nobody
    /// asked for. `reload` is the way to ask again.
    public func showContext(at repo: String) {
        deskTabs[repo] = .context
        guard contexts[repo] == nil, !contextLoading.contains(repo) else { return }
        Task { await loadContext(repo: repo) }
    }

    public func showWork(at repo: String) {
        deskTabs[repo] = .work
    }

    /// Open one Markdown file of one desk, the way `nexus open <file>` asks
    /// to: select the desk, flip it to Context, read that file. The path is
    /// still only a request; the door decides whether it is in the index.
    ///
    /// A repo with no desk on the floor is said out loud rather than drawn as
    /// an empty pane: the selection lands on it, so the moment a snapshot
    /// carries the desk it draws, and the toast says why it has not yet.
    public func open(repo: String, path: String) async {
        select(.desk(repo))
        deskTabs[repo] = .context
        if station(repo) == nil {
            toast = "\(repo) is not a desk in the office yet; the next snapshot may bring it"
        }
        await loadContext(repo: repo, path: path)
        if let said = contextErrors[repo] { toast = said }
    }

    /// Read one desk's context. `path` empty asks for the index, and then opens
    /// the README, or the first entry when there is no README: a list of files
    /// with an empty pane beside it is a screen that reads as broken.
    ///
    /// A failure leaves whatever was last read on screen and puts the reason
    /// beside it. A desk the office cannot place is the ordinary case here, and
    /// it must say so rather than draw as a desk with nothing written in it.
    public func loadContext(repo: String, path: String = "") async {
        contextLoading.insert(repo)
        defer { contextLoading.remove(repo) }
        do {
            var got = try await api.context(repo: repo, path: path)
            if path.isEmpty, let opening = DeskContext.opening(got.files) {
                // The index already arrived, so a failure to read the first
                // file costs the document and not the list.
                got = (try? await api.context(repo: repo, path: opening.path)) ?? got
            }
            contexts[repo] = got
            contextErrors[repo] = nil
        } catch let error as ApiError {
            contextErrors[repo] = error.message
        } catch {
            contextErrors[repo] = error.localizedDescription
        }
    }

    public func openSessionThread(_ name: String) {
        openSession = name
        Task { await loadSessionTranscript(name) }
    }

    public func loadSessionTranscript(_ name: String) async {
        do {
            sessionTranscripts[name] = try await api.sessionTranscript(name: name)
        } catch let error as ApiError {
            // Same rule as a chat thread: whatever was last read stays on
            // screen. A conversation that empties itself because hcom blinked is
            // a lie about what was said.
            if sessionTranscripts[name] == nil { toast = error.message }
        } catch {
            if sessionTranscripts[name] == nil { toast = error.localizedDescription }
        }
    }

    /// Answer a running agent. The server refuses a message to one that would
    /// never read it, and that refusal is shown in the server's own words: a
    /// "sent" over a message nothing will read is the false green this office
    /// exists to kill.
    public func replyToSession(_ name: String, text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        drafts[Self.draftKey(session: name)] = ""
        do {
            _ = try await api.saySession(name: name, text: trimmed)
            toast = "sent to \(name)"
            await loadSessionTranscript(name)
            await refreshSessions()
        } catch let error as ApiError {
            // Put the words back in the box. A refused message that also
            // vanished is a message a person has to retype from memory.
            drafts[Self.draftKey(session: name)] = trimmed
            toast = error.message
        } catch {
            drafts[Self.draftKey(session: name)] = trimmed
            toast = error.localizedDescription
        }
    }

    /// Open a new engine at a desk. This runs a program, so the toast carries
    /// the server's own words about what happened rather than a cheerful
    /// sentence written here.
    public func startSession(tool: String, at repo: String, prompt: String = "") async {
        do {
            let ack = try await api.startSession(tool: tool, repo: repo, prompt: prompt)
            toast = ack.result?.isEmpty == false ? "\(tool) at \(repo): \(ack.result!)"
                                                 : "\(tool) starting at \(repo)"
            await refreshSessions()
        } catch let error as ApiError {
            toast = error.message
        } catch {
            toast = error.localizedDescription
        }
    }

    public func refreshSelectedChat() async {
        guard case .bot(let id)? = selection else { return }
        await loadChat(id)
    }

    public func loadChat(_ id: String) async {
        do {
            chats[id] = marked(try await api.chat(bot: id), bot: id)
        } catch let error as ApiError where error.isMissing {
            chats[id] = []
        } catch {
            // Leave whatever was last read on screen. A thread that empties
            // itself because the harness blinked is a lie about the history.
        }
    }

    // MARK: - what a click does

    public func select(_ selection: Selection) {
        // The automation page is a whole screen and owns the detail pane while
        // it is open, so a click landing underneath it changed a selection
        // nobody could see and the room read as frozen. Choosing something to
        // look at is choosing to leave the page. Opening the page is not itself
        // a choice about anything, so it still leaves the selection alone.
        automationOpen = false
        // Two panes showing the same desk is a comparison with itself, and it
        // reads as a window that lost one of them.
        if compared == selection { compared = nil }
        self.selection = selection
        if case .bot(let id) = selection {
            Task { await loadChat(id) }
        }
    }

    /// Say something, with or without a picture.
    ///
    /// An empty message is allowed when a picture is going with it, because a
    /// screenshot on its own is a whole thing to say. Whether the door on the
    /// other end agrees is the door's business, and its refusal arrives as its
    /// own words in the notice line rather than as a rule guessed at here.
    public func send(to id: String, message: String,
                     attachment: PreparedImage? = nil) async {
        let trimmed = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty || attachment != nil else { return }
        // The box empties because the message moved into the transcript on the
        // line below, not because it went anywhere yet. Only this bot's draft
        // is cleared: a sentence half written to somebody else is untouched.
        let key = Self.draftKey(bot: id)
        drafts[key] = nil
        pendingAttachments[key] = nil
        // Show it immediately. The turn takes a whole agent run, and a message
        // that vanishes for ninety seconds reads as a message that was lost.
        chats[id, default: []].append(ChatTurn(role: "user", content: trimmed,
                                               at: nowISO(),
                                               hasPhoto: attachment != nil))
        if attachment != nil { remember(photoFor: id, message: trimmed) }
        do {
            try await api.send(bot: id, message: trimmed, attachment: attachment)
            await refreshBots()
            await loadChat(id)
        } catch let error as ApiError {
            toast = error.message
        } catch {
            toast = error.localizedDescription
        }
    }

    /// What this app sent a picture with, this run.
    ///
    /// The harness may echo `attachments` back on the turn, and when it does the
    /// mark is a fact off the wire and this is never consulted. When it echoes
    /// nothing, the mark comes from here instead: a weaker claim, because it is
    /// this app's own memory of what it sent and it does not survive a restart.
    /// Matching on the message text is what a transcript that has no turn ids
    /// allows; two identical messages one of which carried a photo will both be
    /// marked, which is the known cost of the weaker claim and the reason the
    /// mark says "with a photo" and never offers to show one.
    private var sentWithPhoto: [String: Set<String>] = [:]

    private func remember(photoFor bot: String, message: String) {
        sentWithPhoto[bot, default: []].insert(message)
    }

    private func marked(_ turns: [ChatTurn], bot: String) -> [ChatTurn] {
        guard let mine = sentWithPhoto[bot], !mine.isEmpty else { return turns }
        return turns.map { turn in
            guard turn.isUser, !turn.hasPhoto, mine.contains(turn.content) else { return turn }
            var out = turn
            out.hasPhoto = true
            return out
        }
    }

    /// Answer the question that was on screen, by its id, or answer nothing.
    ///
    /// `id` is the id of the gate the view actually drew. If the floor has moved
    /// to another question since it was drawn, this posts nothing at all and
    /// says so: sending the click to whatever gate happens to be live now would
    /// approve a command nobody ever read.
    public func answerGate(id: String, answer: String, always: Bool) async {
        switch GateAnswer.decide(displayedId: id, liveGates: gates) {
        case .movedOn:
            say(movedOn)
            return
        case .post(let asked):
            do {
                let ack = try await api.answerGate(id: asked, answer: answer, always: always)
                say(ack.spoken)
                await refreshGate()
            } catch let error as ApiError {
                // A conflict means the agent moved on. It is shown and never
                // retried with another id.
                say(error.message)
                await refreshGate()
            } catch {
                say(error.localizedDescription)
            }
        }
    }

    /// What happened to the gate, said in both places a person could be looking.
    /// The notice rides with the card and the sheet; the toast outlives them,
    /// which matters exactly when the gate they were drawing has gone.
    private func say(_ line: String) {
        gateNotice = line
        toast = line
    }

    private var movedOn: String {
        guard gate.isPending else { return "that question has moved on" }
        return "that question has moved on. the floor is now asking about "
            + StateRules.line(gate.target, limit: 80)
    }

    /// Put a desk away, or bring it back.
    ///
    /// The row moves on the click and the server is told afterwards. If the
    /// server refuses, the row moves back and says why: a click that silently
    /// did nothing is worse than one that visibly failed, because the person
    /// walks away believing the floor is smaller than it is.
    public func setDesk(repo: String, hidden: Bool) async {
        pendingHidden[repo] = hidden
        if hidden, selection == .desk(repo) { putAwayOpen = true }
        do {
            let away = Set(try await api.setDesk(repo: repo, hidden: hidden))
            pendingHidden[repo] = away.contains(repo)
            toast = hidden ? "\(repo) is put away" : "\(repo) is back on the floor"
            await refreshWorld()
        } catch let error as ApiError {
            pendingHidden.removeValue(forKey: repo)
            toast = error.message
        } catch {
            pendingHidden.removeValue(forKey: repo)
            toast = error.localizedDescription
        }
    }

    /// Pin a desk to the top, or take the pin out.
    public func setPin(repo: String, pinned: Bool) async {
        let order = pinned ? StateRules.moved(pins: pinOrder, repo: repo, before: nil)
                           : pinOrder.filter { $0 != repo }
        await savePins(order, toast: pinned ? "\(repo) is pinned" : "\(repo) is unpinned")
    }

    /// One pinned desk dropped onto another, or past the last one.
    public func movePin(repo: String, before target: String?) async {
        let order = StateRules.moved(pins: pinOrder, repo: repo, before: target)
        guard order != pinOrder else { return }
        await savePins(order, toast: nil)
    }

    /// The row moves on the drop and the server is told afterwards, exactly as
    /// putting a desk away does. A refusal puts the order back and says why.
    private func savePins(_ order: [String], toast note: String?) async {
        pendingPins = order
        do {
            pendingPins = try await api.setPins(order)
            if let note { toast = note }
            await refreshWorld()
        } catch let error as ApiError {
            pendingPins = nil
            toast = error.message
        } catch {
            pendingPins = nil
            toast = error.localizedDescription
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
        // An alert is a thing an app posts. Asking the notification centre for
        // one from a bare test bundle is not a quiet no-op, it is a crash, and
        // the rule about announcing each question once has to be provable
        // headlessly.
        guard Bundle.main.bundleIdentifier != nil,
              Bundle.main.bundleURL.pathExtension == "app" else { return }
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

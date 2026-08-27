import Foundation
import Observation

/// A mark a person puts on one turn of a conversation.
///
/// Deliberately not emoji. `Theme.GateMark` already settled this argument once:
/// an SF Symbol hand "renders as something a person reads as an emoji, and this
/// surface does not use emoji". So a reaction here is a *mark* — a tick, a
/// cross, a query — in the same language as every other glyph in the room, and
/// there is not a face or a hand among them.
///
/// The five carry the same meanings iMessage's do, which is the point of the
/// request, without importing its gestures:
///
/// | iMessage | here | what it says |
/// |---|---|---|
/// | ♥ | `heart.fill` | this one mattered |
/// | 👍 | `checkmark` | agreed, or go ahead |
/// | 👎 | `xmark` | no, or do not |
/// | ‼️ | `exclamationmark.2` | look at this one again |
/// | ❓ | `questionmark` | I do not follow |
public enum Reaction: String, CaseIterable, Codable, Sendable {
    case loved, agreed, refused, flagged, unclear

    /// The SF Symbol drawn on the bubble.
    public var symbol: String {
        switch self {
        case .loved: return "heart.fill"
        case .agreed: return "checkmark"
        case .refused: return "xmark"
        case .flagged: return "exclamationmark.2"
        case .unclear: return "questionmark"
        }
    }

    /// What the menu item reads. Lower case, like every other state in here.
    public var label: String {
        switch self {
        case .loved: return "loved"
        case .agreed: return "agreed"
        case .refused: return "no"
        case .flagged: return "look again"
        case .unclear: return "unclear"
        }
    }
}

/// Every reaction on this machine, and nothing else's business.
///
/// **Local only, on purpose.** A reaction never reaches the door, the harness or
/// GitHub. That is not a shortcut taken to avoid writing the server half: there
/// is no consumer for it on the other side, and a mark that travels to an agent
/// is a message, which this repo already has and calls a message. So this is a
/// note Aria leaves for Aria, it lives in `UserDefaults`, and losing it costs
/// nothing that was not already free.
///
/// Foundation and Observation only, so it proves out with no app host.
@MainActor
@Observable
public final class Reactions {
    /// turn key → reaction. The whole state.
    private var marks: [String: Reaction] = [:]

    /// Where it persists, or `nil` to keep it in memory and never write.
    /// The demo floor and the tests both pass `nil`: a screenshot run must not
    /// be able to edit what a real person reacted to, and a test that writes to
    /// `UserDefaults.standard` is a test that changes the machine it runs on.
    private let defaults: UserDefaults?

    private static let storeKey = "reactions.v1"

    /// The one every view reads.
    ///
    /// A screenshot run gets a store that never writes: `shoot.sh` opens the
    /// real app on this machine, and a harness that could edit what Aria
    /// actually reacted to would be a check that damages the thing it checks.
    public static let shared = Reactions(defaults: isDemoRun ? nil : .standard)

    /// Whether the app was launched against a fixture. Read from the arguments
    /// directly rather than from `Api`, so nothing in this file has to know what
    /// a door is and the whole of it stays testable with no app host.
    static var isDemoRun: Bool {
        CommandLine.arguments.contains("--demo")
            || !(ProcessInfo.processInfo.environment["OFFICE_DEMO"] ?? "").isEmpty
    }

    public init(defaults: UserDefaults? = .standard) {
        self.defaults = defaults
        load()
    }

    // MARK: - the key

    /// A name for one turn that survives the transcript being fetched again.
    ///
    /// **Never the row's index.** This repo has already paid for that lesson
    /// once, on gates: "answering by position instead of by id would approve a
    /// command nobody saw". A transcript is refetched every few seconds and a
    /// turn can arrive, so a mark keyed by position is a mark that slides onto
    /// somebody else's sentence. Keyed by what the turn *says*, it stays put.
    ///
    /// The one collision this admits: two turns in the same thread, from the
    /// same side, with identical text and no timestamp, share a mark. The
    /// harness stamps `at` on every turn in practice, so this needs a
    /// transcript that has lost its timestamps *and* a person who said the
    /// exact same thing twice — and when it happens, the mark shows on a turn
    /// identical to the one it was put on. That is a wart, not a lie, and it is
    /// the cheap end of the trade against marks that wander.
    public static func key(thread: String, turn: ChatTurn) -> String {
        let body = [turn.role, turn.at ?? "", turn.content].joined(separator: "\u{1F}")
        return thread + ":" + fingerprint(body)
    }

    /// FNV-1a, 64 bit, base 36.
    ///
    /// Written out rather than reached for, because Swift's own `Hasher` is
    /// seeded per process: `hashValue` is a different number every launch, so a
    /// key built from it would lose every reaction each time the app opened,
    /// and would do it silently. Anything persisted needs a hash that is the
    /// same tomorrow.
    static func fingerprint(_ text: String) -> String {
        var hash: UInt64 = 0xcbf2_9ce4_8422_2325
        for byte in text.utf8 {
            hash ^= UInt64(byte)
            hash = hash &* 0x100_0000_01b3
        }
        return String(hash, radix: 36)
    }

    // MARK: - reading and writing

    public func reaction(thread: String, turn: ChatTurn) -> Reaction? {
        marks[Self.key(thread: thread, turn: turn)]
    }

    /// Put a mark on, take it off, or swap it.
    ///
    /// Reacting with the mark that is already there removes it, which is what
    /// every other reaction UI does and what a person expects from pressing the
    /// lit one again.
    public func toggle(_ reaction: Reaction, thread: String, turn: ChatTurn) {
        let key = Self.key(thread: thread, turn: turn)
        if marks[key] == reaction {
            marks.removeValue(forKey: key)
        } else {
            marks[key] = reaction
        }
        save()
    }

    public func clear(thread: String, turn: ChatTurn) {
        marks.removeValue(forKey: Self.key(thread: thread, turn: turn))
        save()
    }

    /// How many marks exist. Only the tests and the demo floor ask.
    public var count: Int { marks.count }

    /// Fill an empty store from a fixture, for `--demo`.
    ///
    /// Without this a reaction could not be photographed: marks live in
    /// `UserDefaults` and `shoot.sh` runs a fresh app against a JSON floor, so
    /// every framing would show a thread with nothing on it and the harness
    /// could never tell a working feature from a deleted one. Refuses to
    /// overwrite anything already there, so it can never eat a real mark.
    public func seed(_ seeds: [String: Reaction]) {
        guard marks.isEmpty else { return }
        marks = seeds
    }

    /// Resolve a fixture's `index → name` against the turns actually loaded,
    /// and seed the result.
    ///
    /// The position is spent here and never stored: what lands in `marks` is a
    /// real turn key, so the moment the app is running the fixture's indices
    /// have stopped existing. An index past the end of the thread, or a name
    /// that is not one of the five, is dropped — a fixture with a typo in it
    /// should photograph one fewer mark, not crash the room it is there to
    /// photograph.
    public func seed(fixture rows: [Int: String], thread: String, turns: [ChatTurn]) {
        var resolved: [String: Reaction] = [:]
        for (index, name) in rows {
            guard turns.indices.contains(index), let mark = Reaction(rawValue: name) else { continue }
            resolved[Self.key(thread: thread, turn: turns[index])] = mark
        }
        guard !resolved.isEmpty else { return }
        seed(resolved)
    }

    // MARK: - disk

    private func load() {
        guard let defaults,
              let raw = defaults.dictionary(forKey: Self.storeKey) as? [String: String]
        else { return }
        // An unknown value is dropped rather than defaulted. A mark whose name
        // this version does not know is a mark from a newer one, and guessing
        // which of the five it meant would put the wrong glyph on a sentence.
        marks = raw.compactMapValues(Reaction.init(rawValue:))
    }

    private func save() {
        guard let defaults else { return }
        defaults.set(marks.mapValues(\.rawValue), forKey: Self.storeKey)
    }
}

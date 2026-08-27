import Foundation

/// Whether the answer a person just gave still belongs to the question they read.
///
/// Between a gate being drawn and a button being pressed the agent can time out
/// and a *different* gate can open under the same sheet. Answering the live gate
/// with a click that was aimed at the old one approves a command nobody saw,
/// which is the single worst thing this app could do.
///
/// So the id travels with the pixels. The view says which gate it is showing,
/// this decides whether that is still the question on the floor, and only a
/// match is ever posted. Foundation only, no `Store`, no view: the rule is
/// provable on its own.
public enum GateAnswer: Equatable {
    /// Post this exact id. It is the id that was on screen.
    case post(String)
    /// The floor has moved on. Say so; post nothing.
    case movedOn

    public static func decide(displayedId: String, liveGate: Gate?) -> GateAnswer {
        guard !displayedId.isEmpty,
              let live = liveGate,
              live.isPending,
              live.id == displayedId
        else { return .movedOn }
        return .post(displayedId)
    }
}

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
        decide(displayedId: displayedId, liveGates: liveGate.map { [$0] } ?? [])
    }

    /// The same rule, against every hand that is up.
    ///
    /// Two bots can be waiting at once, so "is this still the question on the
    /// floor" stopped being a question about the oldest one. It is a question
    /// about whether the id that was drawn is anywhere in the room: answering
    /// the second gate must land on the second gate, and answering a gate that
    /// has left must land on nothing at all, including on whatever moved up
    /// into its place.
    public static func decide(displayedId: String, liveGates: [Gate]) -> GateAnswer {
        guard !displayedId.isEmpty,
              liveGates.contains(where: { $0.isPending && $0.id == displayedId })
        else { return .movedOn }
        return .post(displayedId)
    }
}

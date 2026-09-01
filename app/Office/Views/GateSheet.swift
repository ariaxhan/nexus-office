import SwiftUI

/// The raised hand, as an interruption you cannot wave away.
///
/// Three properties this has to hold, and does:
///
///   * **The target is shown verbatim.** Never summarised, never truncated into
///     ambiguity. A gate you approve without reading the command is a rubber stamp.
///   * **The answer carries the question's id.** Between seeing a gate and
///     answering it the agent can time out and a different gate can open, so a
///     mismatched answer is refused out loud rather than approving a command
///     nobody ever saw.
///   * **It cannot be dismissed without an answer.** No close button, no click
///     outside, and Escape does nothing. The sheet leaves when the gate is no
///     longer pending, which is a fact about the agent and not about this window.
///
/// Two bots can be standing there at once. The sheet draws the oldest, says how
/// many are behind it and who is next, and answering one moves it to the one
/// after rather than closing: it leaves when the room is empty, not when one
/// question is.
struct GateSheet: View {
    @Bindable var store: Store
    @State private var elapsed: Double = 0
    @State private var sending = false

    private var gate: Gate { store.gate }

    private let clock = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                GateMark(size: 13)
                Text("An agent is asking permission")
                    .officeFont(size: 14, weight: .semibold)
                    .foregroundStyle(Theme.text)
                Spacer()
                Text("waiting \(StateRules.waited(seconds: waiting))")
                    .officeFont(size: 12, design: .monospaced)
                    .foregroundStyle(Theme.amber)
            }

            if let queue = store.gateQueueLine {
                // Quiet on purpose. It says how much is left, and answering the
                // question in front of you must never become a guess about how
                // many more there are.
                Text(queue)
                    .officeFont(size: 11.5)
                    .foregroundStyle(Theme.faint)
            }

            if !gate.permission.isEmpty {
                Text(gate.permission)
                    .officeFont(size: 13, weight: .medium)
                    .foregroundStyle(Theme.text)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("it wants to run")
                    .officeFont(size: 11)
                    .foregroundStyle(Theme.faint)
                ScrollView {
                    Text(gate.target)
                        .officeFont(size: 13, design: .monospaced)
                        .foregroundStyle(Theme.text)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12)
                }
                .frame(maxHeight: 180)
                .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Theme.well))
                .overlay(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .strokeBorder(Theme.hairline, lineWidth: 1)
                )
            }

            if !gate.detail.isEmpty {
                Text(gate.detail)
                    .officeFont(size: 12.5)
                    .foregroundStyle(Theme.dim)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let bot = gate.bot, let named = store.bot(bot) {
                HStack(spacing: 7) {
                    BotAvatar(color: named.color.isEmpty ? .derived(from: named.id)
                                                         : Color(hex: named.color), size: 18)
                    Text("asked by \(named.name)")
                        .officeFont(size: 11.5)
                        .foregroundStyle(Theme.faint)
                }
            }

            if let notice = store.gateNotice {
                // The server's own words. A 409 means the agent moved on, and the
                // sheet stays until the gate itself is gone.
                Text(notice)
                    .officeFont(size: 12)
                    .foregroundStyle(Theme.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(spacing: 9) {
                SheetButton(title: "Deny", tint: Theme.red, busy: sending) { answer("deny", false) }
                Spacer()
                SheetButton(title: "Allow always", tint: Theme.dim, busy: sending) { answer("allow", true) }
                SheetButton(title: "Allow once", tint: Theme.green, filled: true, busy: sending) {
                    answer("allow", false)
                }
            }
        }
        .padding(20)
        .frame(width: 520)
        .background(Theme.roster)
        .onReceive(clock) { _ in elapsed += 1 }
        // A new question under the same sheet is a new clock. Carrying the old
        // one over would say a question that opened a second ago has been
        // waiting four minutes, which is the sheet lying about the one number
        // it exists to keep running.
        .onChange(of: gate.id) { _, _ in
            elapsed = 0
            sending = false
        }
        // No close button, no click outside, and Escape does nothing. The only
        // way out of this sheet is an answer, or the agent giving up on its own.
        .interactiveDismissDisabled()
        .onExitCommand { }
    }

    private var waiting: Double { (gate.waitingS ?? 0) + elapsed }

    /// The id of the gate this sheet is drawing, not whatever is live when the
    /// answer lands. If those two have drifted apart, `answerGate` posts nothing
    /// and says the question moved on.
    private func answer(_ verdict: String, _ always: Bool) {
        let displayed = gate.id
        sending = true
        Task {
            await store.answerGate(id: displayed, answer: verdict, always: always)
            sending = false
        }
    }
}

struct SheetButton: View {
    let title: String
    let tint: Color
    var filled = false
    var busy = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(busy ? "sending" : title)
                .officeFont(size: 12.5, weight: .medium)
                .foregroundStyle(filled ? Theme.onFilled : tint)
                .padding(.horizontal, 14)
                .padding(.vertical, 7)
                .background(
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(filled ? tint : tint.opacity(0.15))
                )
        }
        .buttonStyle(.plain)
        .disabled(busy)
    }
}

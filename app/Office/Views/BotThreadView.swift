import SwiftUI

/// A conversation with one bot.
///
/// A turn is a whole agent run, thirty seconds to two minutes, so a sent message
/// appears at once and the room learns the reply landed by watching the roster
/// change under it. The working indicator is the honest picture of that wait.
struct BotThreadView: View {
    @Bindable var store: Store
    let bot: Bot

    @FocusState private var composing: Bool

    private var turns: [ChatTurn] { store.chats[bot.id] ?? [] }
    private var color: Color { bot.color.isEmpty ? .derived(from: bot.id) : Color(hex: bot.color) }

    /// The message being written, kept by the office.
    ///
    /// This was `@State`, so switching to another name and back threw it away:
    /// two sentences in, click Inbox to check something, come back to an empty
    /// box. A half-written message is work, and the view it happens to be
    /// displayed in is not where work should live.
    private var key: String { Store.draftKey(bot: bot.id) }

    private var draft: Binding<String> {
        Binding(get: { store.drafts[key] ?? "" }, set: { store.drafts[key] = $0 })
    }

    private var typed: String {
        draft.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        VStack(spacing: 0) {
            head
            Divider().overlay(Theme.hairline)
            transcript
            // A question that moved on takes its card with it, so what happened
            // to the answer has to survive the card. Without this the whole
            // reply to a click in here is a gate quietly vanishing.
            if let toast = store.toast {
                ToastBar(text: toast) { store.toast = nil }
            }
            composer
        }
        .background(Theme.ink)
    }

    // MARK: - head

    private var head: some View {
        HStack(spacing: 9) {
            BotAvatar(color: color, size: 22)
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 7) {
                    Text(bot.name)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Theme.text)
                    if bot.busy {
                        Text("working")
                            .font(.system(size: 11))
                            .foregroundStyle(Theme.blue)
                    }
                }
                // What to ask this one for. The paragraph of identity behind it
                // stays in `_meta/bots.json`: a person picking who to message
                // needs the job, not the briefing.
                if !bot.purpose.isEmpty {
                    Text(bot.purpose)
                        .font(.system(size: 11))
                        .foregroundStyle(Theme.faint)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            Spacer()
            if !store.runtimeUp {
                Text("the harness is not running")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.faint)
            }
        }
        .padding(.horizontal, 18)
        .frame(height: 44)
        .padding(.top, 8)
    }

    // MARK: - transcript

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if turns.isEmpty && !store.gateBelongsTo(bot: bot.id) {
                        // An empty thread is the one place a person is actually
                        // asking what this bot is for, so it answers that and
                        // then says how to start.
                        VStack(alignment: .leading, spacing: 6) {
                            if !bot.purpose.isEmpty {
                                Text(bot.purpose)
                                    .font(.system(size: 13))
                                    .foregroundStyle(Theme.dim)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            Text("Message \(bot.name) to start.")
                                .font(.system(size: 12.5))
                                .foregroundStyle(Theme.faint)
                        }
                        .frame(maxWidth: 520, alignment: .leading)
                        .padding(.top, 24)
                    }
                    ForEach(Array(turns.enumerated()), id: \.offset) { index, turn in
                        Bubble(turn: turn, color: color).id(index)
                    }
                    // The gate lands in the thread of the bot that asked, because
                    // that is where a person is already looking. The sheet is the
                    // interruption; this is the record.
                    if store.gateBelongsTo(bot: bot.id) {
                        GateCard(store: store, gate: store.gate)
                            .id("gate")
                    }
                    if let error = bot.error {
                        Label(error, systemImage: "exclamationmark.triangle.fill")
                            .font(.system(size: 12))
                            .foregroundStyle(Theme.red)
                            .padding(.vertical, 4)
                            .id("error")
                    }
                    if bot.busy {
                        WorkingBubble().id("working")
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 14)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollContentBackground(.hidden)
            .onChange(of: turns.count) { _, _ in
                withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
            }
        }
    }

    // MARK: - composer

    private var composer: some View {
        HStack(spacing: 8) {
            TextField("Message \(bot.name)", text: draft, axis: .vertical)
                .textFieldStyle(.plain)
                .font(.system(size: 13))
                .foregroundStyle(Theme.text)
                .lineLimit(1...5)
                .focused($composing)
                .onSubmit(send)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.raised)
                )
            Button(action: send) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 22))
                    .foregroundStyle(typed.isEmpty ? Theme.faint : color)
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.return, modifiers: [])
            .disabled(typed.isEmpty)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }

    /// Clearing on send stays, and it lives in the store with the draft: the
    /// box empties on the same line that puts the message in the transcript, so
    /// what a person sees is the sentence moving rather than disappearing.
    private func send() {
        let message = draft.wrappedValue
        guard !typed.isEmpty else { return }
        Task { await store.send(to: bot.id, message: message) }
    }
}

// MARK: - pieces

struct Bubble: View {
    let turn: ChatTurn
    let color: Color

    var body: some View {
        HStack {
            if turn.isUser { Spacer(minLength: 60) }
            Text(turn.content)
                .font(.system(size: 13))
                .foregroundStyle(Theme.text)
                .textSelection(.enabled)
                .padding(.horizontal, 13)
                .padding(.vertical, 9)
                .background(
                    RoundedRectangle(cornerRadius: 15, style: .continuous)
                        .fill(turn.isUser ? color.opacity(0.28) : Theme.raised)
                )
                .frame(maxWidth: 560, alignment: turn.isUser ? .trailing : .leading)
            if !turn.isUser { Spacer(minLength: 60) }
        }
    }
}

/// Three dots that actually move. A static "working" label is indistinguishable
/// from a frozen app, and a two minute turn gives it plenty of time to look like one.
struct WorkingBubble: View {
    @State private var phase = 0.0

    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(Theme.dim)
                    .frame(width: 5, height: 5)
                    .opacity(0.35 + 0.65 * abs(sin(phase + Double(index) * 0.7)))
            }
        }
        .padding(.horizontal, 13)
        .padding(.vertical, 11)
        .background(RoundedRectangle(cornerRadius: 15, style: .continuous).fill(Theme.raised))
        .onAppear {
            withAnimation(.linear(duration: 1.2).repeatForever(autoreverses: false)) {
                phase = .pi * 2
            }
        }
    }
}

/// The raised hand, in the thread. Same three properties as the sheet: the
/// target verbatim, the clock running, and an answer that carries the id.
///
/// The card is handed the gate it draws and answers that gate by its own id,
/// which is what makes the id on the buttons and the text above them the same
/// question. It never reaches for whatever gate is live at the moment of the
/// click, and there is nowhere else in this view to get one from.
struct GateCard: View {
    @Bindable var store: Store
    let gate: Gate

    private var notice: String? { store.gateNotice }

    private func answer(_ verdict: String, _ always: Bool) {
        Task { await store.answerGate(id: gate.id, answer: verdict, always: always) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 7) {
                GateMark(size: 11)
                Text("asking permission")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.amber)
                Spacer()
                if let waiting = gate.waitingS {
                    Text("waiting \(StateRules.waited(seconds: waiting))")
                        .font(.system(size: 11))
                        .foregroundStyle(Theme.faint)
                }
            }
            if !gate.permission.isEmpty {
                Text(gate.permission)
                    .font(.system(size: 12.5, weight: .medium))
                    .foregroundStyle(Theme.text)
            }
            Text(gate.target)
                .font(.system(size: 12.5, design: .monospaced))
                .foregroundStyle(Theme.text)
                .textSelection(.enabled)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Color.black))
            if !gate.detail.isEmpty {
                Text(gate.detail)
                    .font(.system(size: 12))
                    .foregroundStyle(Theme.dim)
            }
            HStack(spacing: 8) {
                GateButton(title: "Deny", tint: Theme.red) { answer("deny", false) }
                GateButton(title: "Allow once", tint: Theme.green) { answer("allow", false) }
                GateButton(title: "Allow always", tint: Theme.dim) { answer("allow", true) }
            }
            if let notice {
                Text(notice)
                    .font(.system(size: 11.5))
                    .foregroundStyle(Theme.faint)
            }
        }
        .padding(13)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Theme.amber.opacity(0.07))
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .strokeBorder(Theme.amber.opacity(0.35), lineWidth: 1)
                )
        )
        .frame(maxWidth: 620, alignment: .leading)
    }
}

struct GateButton: View {
    let title: String
    let tint: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(tint)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(
                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                        .fill(tint.opacity(0.14))
                )
        }
        .buttonStyle(.plain)
    }
}

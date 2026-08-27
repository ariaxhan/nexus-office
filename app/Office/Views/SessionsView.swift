import SwiftUI

/// The agents running at a desk, and the way to answer one without a terminal.
///
/// A desk in this office has always been a repo seen from GitHub's side. This is
/// the same folder seen from this machine's side: what is running in it right
/// now, what it just said, and a box to reply in.
///
/// **A reply is a message, never a keystroke.** It lands in the agent's queue and
/// it reads it at its next hook. There is no control anywhere in this file that
/// types into a live terminal, because a message arriving mid-prompt would be
/// submitted into whatever was half-typed there.
///
/// **The office cannot see every session, and says so.** Only sessions bound to
/// hcom are visible. `canSee` false means "we do not know", and that draws as
/// its own sentence rather than as an empty list, because an empty list is a
/// claim that nothing is running.
struct SessionsView: View {
    @Bindable var store: Store
    let repo: String

    private var roster: SessionRoster { store.sessions(at: repo) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            head
            if !roster.canSee {
                blind
            } else if roster.sessions.isEmpty {
                empty
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(roster.sessions) { session in
                        SessionRow(store: store, session: session)
                        if session.id != roster.sessions.last?.id {
                            Rectangle().fill(Theme.hairline).frame(height: 0.5)
                        }
                    }
                }
                .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Theme.raised))
            }
            starters
        }
    }

    private var head: some View {
        HStack(spacing: 8) {
            Text("sessions here")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Theme.faint)
            if roster.blocked > 0 {
                Pill(text: "\(roster.blocked) waiting on you", color: Theme.amber)
            } else if roster.live > 0 {
                Pill(text: "\(roster.live) running", color: Theme.green)
            }
        }
    }

    /// hcom is not there, or would not answer. NOT an empty desk: the difference
    /// is the whole reason this branch exists.
    private var blind: some View {
        Text(roster.detail.isEmpty
             ? "the office cannot see the sessions on this machine right now"
             : roster.detail)
            .font(.system(size: 12))
            .foregroundStyle(Theme.amber)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: 620, alignment: .leading)
    }

    private var empty: some View {
        Text("nothing running in this folder. Only sessions connected to hcom are visible here.")
            .font(.system(size: 12))
            .foregroundStyle(Theme.faint)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: 620, alignment: .leading)
    }

    /// Two buttons that each start a real agent with real credentials, so
    /// neither is labelled with anything but the name of the program it runs.
    private var starters: some View {
        HStack(spacing: 8) {
            Text("start")
                .font(.system(size: 11))
                .foregroundStyle(Theme.faint)
            ForEach(["claude", "codex"], id: \.self) { tool in
                Button(tool) {
                    Task { await store.startSession(tool: tool, at: repo) }
                }
                .buttonStyle(.plain)
                .font(.system(size: 11.5, weight: .medium))
                .foregroundStyle(Theme.text)
                .padding(.horizontal, 10)
                .padding(.vertical, 4)
                .background(Capsule().fill(Theme.well))
            }
            Text("opens a terminal in this folder")
                .font(.system(size: 11))
                .foregroundStyle(Theme.faint)
        }
    }
}

/// One running agent: what it is, what it is doing, and the box to answer it.
///
/// The thread opens in place rather than in a sheet: the point of #38 is not
/// having to go somewhere else to answer, and a sheet is somewhere else.
struct SessionRow: View {
    @Bindable var store: Store
    let session: Session

    private var open: Bool { store.openSession == session.name }

    private var dot: Color {
        switch session.status {
        case "blocked": return Theme.amber
        case "active": return Theme.green
        case "listening": return Theme.blue
        case "inactive": return Theme.faint
        default: return Theme.dim
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                if open { store.openSession = nil } else { store.openSessionThread(session.name) }
            } label: {
                header
            }
            .buttonStyle(.plain)

            if open {
                thread
                composer
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(dot).frame(width: 8, height: 8).padding(.top, 4)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 7) {
                    Text(session.name)
                        .font(.system(size: 12.5, weight: .medium))
                        .foregroundStyle(Theme.text)
                    if !session.tool.isEmpty {
                        Pill(text: session.tool, color: Theme.faint)
                    }
                    if session.isBlocked {
                        Pill(text: "waiting on you", color: Theme.amber)
                    }
                    if session.unread > 0 {
                        Pill(text: "\(session.unread) unread", color: Theme.blue)
                    }
                    if !session.branch.isEmpty {
                        Text(session.branch)
                            .font(.system(size: 11))
                            .foregroundStyle(Theme.faint)
                    }
                }
                Text(session.doing.isEmpty ? session.status : session.doing)
                    .font(.system(size: 11.5))
                    .foregroundStyle(Theme.dim)
                    .lineLimit(1)
                // The tool call under way. One line, already clipped by the
                // server: a bash heredoc in here would be the whole desk.
                if !session.detail.isEmpty && open {
                    Text(session.detail)
                        .font(.system(size: 11).monospaced())
                        .foregroundStyle(Theme.faint)
                        .lineLimit(3)
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 6, style: .continuous)
                            .fill(Theme.well))
                }
            }
            Spacer(minLength: 8)
            Text(open ? "hide" : "open")
                .font(.system(size: 11))
                .foregroundStyle(Theme.dim)
        }
        .contentShape(Rectangle())
    }

    /// The last few exchanges, oldest first, so the newest is nearest the box
    /// you are about to type in.
    @ViewBuilder private var thread: some View {
        let script = store.sessionTranscripts[session.name]
        if let script, !script.exchanges.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(script.exchanges.suffix(6)) { turn in
                    VStack(alignment: .leading, spacing: 4) {
                        if !turn.you.isEmpty {
                            Text(turn.you)
                                .font(.system(size: 12))
                                .foregroundStyle(Theme.text)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if !turn.them.isEmpty {
                            Text(turn.them)
                                .font(.system(size: 12))
                                .foregroundStyle(Theme.dim)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(10)
            .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Theme.well))
        } else if script != nil {
            Text("nothing said yet")
                .font(.system(size: 11.5))
                .foregroundStyle(Theme.faint)
        }
    }

    /// The box. Disabled, with the reason written on it, when the agent would
    /// never read what was typed: a send button over a dead session is a button
    /// that lies about where the words went.
    private var composer: some View {
        let key = Store.draftKey(session: session.name)
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                TextField("answer \(session.name)",
                          text: Binding(get: { store.drafts[key] ?? "" },
                                        set: { store.drafts[key] = $0 }),
                          axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.system(size: 12.5))
                    .lineLimit(1...5)
                    .padding(8)
                    .background(RoundedRectangle(cornerRadius: 7, style: .continuous)
                        .fill(Theme.well))
                    .disabled(!session.reachable)
                    .onSubmit { send(key) }
                Button("send") { send(key) }
                    .buttonStyle(.plain)
                    .font(.system(size: 11.5, weight: .medium))
                    .foregroundStyle(session.reachable ? Theme.onFilled : Theme.faint)
                    .padding(.horizontal, 11)
                    .padding(.vertical, 5)
                    .background(Capsule().fill(session.reachable ? Theme.blue : Theme.well))
                    .disabled(!session.reachable)
            }
            if !session.reachable {
                Text("\(session.name) is \(session.status), so it would never read this")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.faint)
            }
        }
    }

    private func send(_ key: String) {
        let text = store.drafts[key] ?? ""
        Task { await store.replyToSession(session.name, text: text) }
    }
}

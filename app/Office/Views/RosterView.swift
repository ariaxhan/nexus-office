import SwiftUI

/// The left column: who you can talk to, then what is on the floor.
///
/// Bots come first because they are colleagues you message, and the desks are
/// one more thing in the list rather than the whole point. Same row shape for
/// both, so the eye reads one list and not two widgets.
struct RosterView: View {
    @Bindable var store: Store

    var body: some View {
        VStack(spacing: 0) {
            search
            ScrollViewReader { scroll in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 1) {
                    header("bots", trailing: nil)
                    if let notice = store.botsNotice {
                        notary(notice)
                    }
                    ForEach(store.visibleBots) { bot in
                        BotRow(bot: bot,
                               selected: store.selection == .bot(bot.id),
                               hasGate: store.gateBelongsTo(bot: bot.id))
                            .contentShape(Rectangle())
                            .onTapGesture { store.select(.bot(bot.id)) }
                    }

                    header("desks", trailing: needsToggle)
                        .padding(.top, 14)
                    if !store.stations.isEmpty {
                        // Put away means not polled, so this is the honest
                        // denominator rather than the size of the list.
                        counter(store.polledLine)
                    }
                    if let notice = store.worldNotice {
                        notary(notice)
                    }
                    ForEach(store.visibleDesks) { station in
                        deskRow(station)
                    }
                    if store.visibleDesks.isEmpty && store.worldNotice == nil {
                        notary(store.needsOnly ? "nothing needs you right now" : "no desks yet")
                    }

                    putAway
                }
                .padding(.horizontal, 8)
                .padding(.bottom, 16)
            }
            .scrollContentBackground(.hidden)
            // The drawer sits under seventy-two desks. Opening a section you
            // then have to go looking for is the same as it not opening.
            .onChange(of: store.putAwayOpen) { _, opened in
                guard opened else { return }
                withAnimation { scroll.scrollTo(putAwayAnchor, anchor: .bottom) }
            }
            }
        }
        .frame(width: Theme.rosterWidth)
        .background(Theme.roster)
    }

    // MARK: - parts

    private func deskRow(_ station: Station) -> some View {
        DeskRow(station: station,
                gate: store.gateShown(at: station),
                selected: store.selection == .desk(station.repo),
                away: store.isHidden(station))
            .contentShape(Rectangle())
            .onTapGesture { store.select(.desk(station.repo)) }
            .contextMenu {
                if store.isHidden(station) {
                    Button("Bring back") {
                        Task { await store.setDesk(repo: station.repo, hidden: false) }
                    }
                } else {
                    Button("Put away") {
                        Task { await store.setDesk(repo: station.repo, hidden: true) }
                    }
                }
                if let url = URL(string: "https://github.com/\(station.repo)") {
                    Link("Open on GitHub", destination: url)
                }
            }
    }

    private var putAwayAnchor: String { "put-away-drawer" }

    /// The desks a person moved out of the way.
    ///
    /// Shut by default and never removed: a put-away desk is still polled for
    /// nothing and still holds its last data, and if one of them starts needing
    /// a person the header says so with the section still closed. Hidden is
    /// allowed to be quiet. It is not allowed to be silent.
    @ViewBuilder private var putAway: some View {
        let away = store.putAwayDesks
        if !away.isEmpty {
            Button {
                store.putAwayOpen.toggle()
            } label: {
                HStack(spacing: 5) {
                    Image(systemName: store.putAwayOpen ? "chevron.down" : "chevron.right")
                        .font(.system(size: 8, weight: .semibold))
                    Text(store.putAwayHeadline)
                        .font(.system(size: 11, weight: .semibold))
                    Spacer()
                }
                .foregroundStyle(store.putAwayNeedsSomeone ? Theme.amber : Theme.faint)
                .padding(.horizontal, 10)
                .padding(.vertical, 4)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(.top, 14)

            if store.putAwayOpen {
                ForEach(away) { station in
                    deskRow(station)
                }
            }
            // The anchor is the bottom of the drawer, not the top of it, so
            // opening it brings the desks into view rather than just the header
            // a person already clicked.
            Color.clear.frame(height: 1).id(putAwayAnchor)
        }
    }

    private var search: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(Theme.faint)
            TextField("Search", text: $store.query)
                .textFieldStyle(.plain)
                .font(.system(size: 12.5))
                .foregroundStyle(Theme.text)
            if !store.query.isEmpty {
                Button {
                    store.query = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 11))
                        .foregroundStyle(Theme.faint)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 8)
        .frame(height: 26)
        .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Theme.raised))
        .padding(.horizontal, 12)
        .padding(.top, 38)
        .padding(.bottom, 10)
    }

    private func header(_ title: String, trailing: AnyView?) -> some View {
        HStack {
            Text(title)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Theme.faint)
            Spacer()
            trailing
        }
        .padding(.horizontal, 10)
        .padding(.bottom, 4)
    }

    private var needsToggle: AnyView? {
        AnyView(
            Button {
                store.needsOnly.toggle()
            } label: {
                Text("needs me")
                    .font(.system(size: 11, weight: store.needsOnly ? .semibold : .regular))
                    .foregroundStyle(store.needsOnly ? Theme.amber : Theme.faint)
            }
            .buttonStyle(.plain)
            .help("Show only the desks a person has to touch. A raised hand is never hidden by it.")
        )
    }

    private func notary(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 11.5))
            .foregroundStyle(Theme.faint)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
    }

    private func counter(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 10.5))
            .foregroundStyle(Theme.faint)
            .padding(.horizontal, 10)
            .padding(.bottom, 5)
    }
}

// MARK: - rows

struct BotRow: View {
    let bot: Bot
    let selected: Bool
    let hasGate: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            BotAvatar(color: bot.color.isEmpty ? .derived(from: bot.id) : Color(hex: bot.color),
                      busy: bot.busy)
            VStack(alignment: .leading, spacing: 1) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(bot.name)
                        .font(.system(size: 13.5, weight: .medium))
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)
                    if hasGate { GateMark(size: 9) }
                    Spacer(minLength: 4)
                    Text(StateRules.stamp(bot.last?.at ?? ""))
                        .font(.system(size: 11))
                        .foregroundStyle(Theme.faint)
                }
                second
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 7)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(selected ? Theme.selected : Color.clear)
        )
    }

    @ViewBuilder private var second: some View {
        if hasGate {
            Text("waiting for you to answer")
                .font(.system(size: 12)).foregroundStyle(Theme.amber).lineLimit(1)
        } else if let error = bot.error {
            Text(StateRules.line(error, limit: 60))
                .font(.system(size: 12)).foregroundStyle(Theme.red).lineLimit(1)
        } else if bot.busy {
            Text("working")
                .font(.system(size: 12)).foregroundStyle(Theme.blue).lineLimit(1)
        } else {
            // The last thing it said, or, before it has said anything, what it
            // is FOR. A column of rows all reporting an empty transcript
            // tells a person nothing about which colleague to open.
            Text(StateRules.botSubtitle(bot: bot, limit: 64))
                .font(.system(size: 12))
                .foregroundStyle(Theme.dim)
                .lineLimit(1)
                .truncationMode(.tail)
        }
    }
}

struct DeskRow: View {
    let station: Station
    /// The live gate, handed in from the gate poll. The row never reads the
    /// copy attached to the station: that one is as old as the world snapshot.
    let gate: Gate?
    let selected: Bool
    /// Put away. Drawn dimmer, drawn the same shape, never drawn as broken.
    var away = false

    private var state: DeskState { StateRules.deskState(station: station, gate: gate) }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            StateDot(state: state)
            VStack(alignment: .leading, spacing: 1) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(station.repo)
                        .font(.system(size: 13.5, weight: .medium))
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Spacer(minLength: 4)
                    Text(StateRules.stamp(station.at))
                        .font(.system(size: 11))
                        .foregroundStyle(Theme.faint)
                }
                HStack(spacing: 5) {
                    Text(state.label)
                        .foregroundStyle(Color(hex: state.hex))
                    if !detail.isEmpty {
                        Text(detail).foregroundStyle(Theme.dim)
                    }
                }
                .font(.system(size: 12))
                .lineLimit(1)
                .truncationMode(.tail)
            }
        }
        .opacity(away ? 0.62 : 1)
        .padding(.horizontal, 8)
        .padding(.vertical, 7)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(selected ? Theme.selected : Color.clear)
        )
    }

    /// What the desk is doing, from the data it has.
    ///
    /// A failed pull is not a desk state. The row keeps saying what the last
    /// good answer said, and the fact that the answer is old is said once, in
    /// the thread, where there is room to say when. A row that turns red every
    /// time GitHub coughs is a roster that cries wolf.
    private var detail: String {
        if state == .gated { return StateRules.line(gate?.permission ?? "", limit: 40) }
        if !station.detail.isEmpty { return StateRules.line(station.detail, limit: 48) }
        return StateRules.line(station.problems.first ?? "", limit: 48)
    }
}

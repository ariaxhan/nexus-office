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
                    if let notice = store.worldNotice {
                        notary(notice)
                    }
                    ForEach(store.visibleDesks) { station in
                        DeskRow(station: station, selected: store.selection == .desk(station.repo))
                            .contentShape(Rectangle())
                            .onTapGesture { store.select(.desk(station.repo)) }
                    }
                    if store.visibleDesks.isEmpty && store.worldNotice == nil {
                        notary(store.needsOnly ? "nothing needs you right now" : "no desks yet")
                    }
                }
                .padding(.horizontal, 8)
                .padding(.bottom, 16)
            }
            .scrollContentBackground(.hidden)
        }
        .frame(width: Theme.rosterWidth)
        .background(Theme.roster)
    }

    // MARK: - parts

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
            Text(StateRules.lastLine(bot: bot, limit: 64).isEmpty
                 ? "no messages yet"
                 : StateRules.lastLine(bot: bot, limit: 64))
                .font(.system(size: 12))
                .foregroundStyle(Theme.dim)
                .lineLimit(1)
                .truncationMode(.tail)
        }
    }
}

struct DeskRow: View {
    let station: Station
    let selected: Bool

    private var state: DeskState { StateRules.deskState(station) }

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
        .padding(.horizontal, 8)
        .padding(.vertical, 7)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(selected ? Theme.selected : Color.clear)
        )
    }

    private var detail: String {
        if state == .gated { return StateRules.line(station.gate?.permission ?? "", limit: 40) }
        if let error = station.issuesError { return StateRules.line(error, limit: 48) }
        return StateRules.line(station.detail, limit: 48)
    }
}

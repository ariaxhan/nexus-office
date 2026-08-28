import SwiftUI
import UniformTypeIdentifiers

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

                    header("desks", trailing: deskControls)
                        .padding(.top, 14)
                    if !store.stations.isEmpty {
                        // Put away means not polled, so this is the honest
                        // denominator rather than the size of the list.
                        counter(store.polledLine)
                    }
                    if let notice = store.worldNotice {
                        notary(notice)
                    }
                    ForEach(store.roster, id: \.header) { group in
                        groupHeader(group.header ?? "")
                        ForEach(group.desks) { station in
                            if group.header == StateRules.pinnedHeader {
                                pinnedRow(station)
                            } else {
                                deskRow(station)
                            }
                        }
                        if group.header == StateRules.pinnedHeader {
                            // Past the last pin. Dropping onto a row lands
                            // before it, so the only way to the bottom of the
                            // list is a target that is nobody.
                            Color.clear.frame(height: 6)
                                .contentShape(Rectangle())
                                .pinDrop { repo in
                                    Task { await store.movePin(repo: repo, before: nil) }
                                }
                        }
                    }
                    if store.visibleDesks.isEmpty && store.worldNotice == nil {
                        notary(store.needsOnly ? "nothing needs you right now" : "no desks yet")
                    }

                    putAway

                    wall
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
            // The wall is under every desk on the floor, so a wall row can be
            // selected while sitting a long way below what a person is looking
            // at. A selected row nobody can see reads as a click that did not
            // land, which is the same failure the drawer anchor above exists
            // to prevent.
            .onChange(of: store.selection) { _, now in
                guard case .section(let id)? = now else { return }
                withAnimation { scroll.scrollTo(rowID(section: id), anchor: .center) }
            }
            }
        }
        .frame(width: Theme.rosterWidth)
        .background(Theme.roster)
    }

    // MARK: - parts

    /// One desk. `pickUp` puts `.draggable` INSIDE the tap gesture rather than
    /// outside it, which is the whole difference between a row that can be
    /// dragged and one that cannot: a tap gesture attached closer to the
    /// content claims the mouse down and the drag never begins, so a `.draggable`
    /// wrapped around the outside of it is dead code that reads as a feature.
    private func deskRow(_ station: Station, pickUp: Bool = false) -> some View {
        DeskRow(station: station,
                gate: store.gateShown(at: station),
                selected: store.selection == .desk(station.repo),
                away: store.isHidden(station))
            .contentShape(Rectangle())
            .ifPickedUp(pickUp, repo: station.repo)
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
                if store.isPinned(station) {
                    Button("Unpin") {
                        Task { await store.setPin(repo: station.repo, pinned: false) }
                    }
                } else {
                    Button("Pin to top") {
                        Task { await store.setPin(repo: station.repo, pinned: true) }
                    }
                }
                if let url = URL(string: "https://github.com/\(station.repo)") {
                    Link("Open on GitHub", destination: url)
                }
            }
    }

    /// A pinned desk can be picked up and dropped on another: the dropped one
    /// lands just above the row it was dropped on. Order is the whole point of
    /// the group, so it is the one place on the roster a drag means anything.
    private func pinnedRow(_ station: Station) -> some View {
        deskRow(station, pickUp: true)
            .pinDrop { repo in
                Task { await store.movePin(repo: repo, before: station.repo) }
            }
    }

    /// The name over one group of desks: "pinned", or an owner.
    private func groupHeader(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 10.5, weight: .semibold))
            .foregroundStyle(Theme.faint)
            .padding(.horizontal, 10)
            .padding(.top, 8)
            .padding(.bottom, 2)
    }

    private var putAwayAnchor: String { "put-away-drawer" }

    private func rowID(section id: String) -> String { "wall:" + id }

    /// The wall: everything this machine can say about itself that is not a
    /// repo and not a colleague.
    ///
    /// Every row here is drawn from the card its source wrote, and nothing in
    /// this file knows what any one of them measures. A new source is a new
    /// python file and no Swift at all, which is the only way six of them stay
    /// cheap to keep.
    @ViewBuilder private var wall: some View {
        if !store.sections.isEmpty {
            header("wall", trailing: wallCount)
                .padding(.top, 14)
            // The automation, as one page, above the cards it is assembled from.
            // It is not a section: a section is one source's card, and this is
            // the join across three of them plus every desk's receipts.
            automationRow
            ForEach(store.visibleSections) { section in
                SectionRow(section: section,
                           selected: store.selection == .section(section.id))
                    .contentShape(Rectangle())
                    .onTapGesture { store.select(.section(section.id)) }
                    .id(rowID(section: section.id))
            }
            if store.visibleSections.isEmpty {
                notary(store.needsOnly ? "nothing on the wall needs you" : "nothing matches")
            }
        }
    }

    /// The way in to the automation page.
    ///
    /// Its own row rather than a button in a header, because it is the answer to
    /// a question a person asks out loud ("what is the cron doing"), and a
    /// question with no visible place to click is a question that gets asked in
    /// a terminal instead.
    private var automationRow: some View {
        let page = store.automation
        return Button {
            store.automationOpen.toggle()
        } label: {
            HStack(spacing: 9) {
                Circle()
                    .fill(page.needsSomebody ? Theme.amber
                          : (page.now.running ? Theme.green : Theme.faint))
                    .frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 2) {
                    Text("automation")
                        .font(.system(size: 12.5, weight: .medium))
                        .foregroundStyle(Theme.text)
                    Text(page.headline.isEmpty ? "not read yet" : page.headline)
                        .font(.system(size: 11))
                        .foregroundStyle(page.needsSomebody ? Theme.amber.opacity(0.85) : Theme.dim)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 6)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(store.automationOpen ? Theme.selected : Color.clear)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var wallCount: AnyView? {
        let line = store.wallLine
        guard !line.isEmpty else { return nil }
        return AnyView(
            Text(line)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Theme.amber)
        )
    }

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

    /// The two things a person can do to the desks list: narrow it, and order
    /// it. Both live in the header because both are answers to "show me a
    /// different view of the same floor".
    private var deskControls: AnyView? {
        AnyView(
            HStack(spacing: 10) {
                sortMenu
                needsToggle
            }
        )
    }

    /// How the desks are ordered. Reordering hides nothing, so there is no
    /// escape hatch to write here: a raised hand is somewhere in every one of
    /// these orders.
    private var sortMenu: some View {
        Menu {
            ForEach(StateRules.DeskSort.allCases) { order in
                Button {
                    store.deskSort = order
                } label: {
                    if store.deskSort == order {
                        Label(order.label, systemImage: "checkmark")
                    } else {
                        Text(order.label)
                    }
                }
            }
        } label: {
            HStack(spacing: 3) {
                Image(systemName: "arrow.up.arrow.down")
                    .font(.system(size: 9, weight: .semibold))
                Text(store.deskSort.label)
                    .font(.system(size: 11, weight: store.deskSort == .owner ? .regular : .semibold))
            }
            .foregroundStyle(store.deskSort == .owner ? Theme.faint : Theme.text)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help("Order the desks. No order hides a desk.")
    }

    private var needsToggle: some View {
        Button {
                store.needsOnly.toggle()
            } label: {
                Text("needs me")
                    .font(.system(size: 11, weight: store.needsOnly ? .semibold : .regular))
                    .foregroundStyle(store.needsOnly ? Theme.amber : Theme.faint)
        }
        .buttonStyle(.plain)
        .help("Show only the desks a person has to touch. A raised hand is never hidden by it.")
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
                        .foregroundStyle(Theme.color(state))
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

/// One thing on the wall, drawn the same shape as a desk and a colleague.
///
/// Title, the source's own sentence under it, a count on the right when
/// something wants a person. Nothing in here is specific to any source: every
/// word on the row came out of the card.
struct SectionRow: View {
    let section: Section
    let selected: Bool

    private var mood: StateRules.SectionMood { StateRules.mood(section) }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            MoodDot(mood: mood)
            VStack(alignment: .leading, spacing: 1) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(section.title)
                        .font(.system(size: 13.5, weight: .medium))
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Spacer(minLength: 4)
                    // The count, or nothing. A column of noughts is a roster
                    // shouting that nothing is happening.
                    if let badge = StateRules.sectionBadge(section) {
                        Pill(text: badge, color: Theme.amber)
                    } else {
                        Text(StateRules.stamp(section.card.asOf))
                            .font(.system(size: 11))
                            .foregroundStyle(Theme.faint)
                    }
                }
                Text(StateRules.sectionSubtitle(section))
                    .font(.system(size: 12))
                    .foregroundStyle(section.isOK ? Theme.dim : Theme.amber.opacity(0.85))
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
}

extension View {
    /// The drag half of reordering a pin, applied only where a drag means
    /// something.
    ///
    /// `.onDrag`/`.onDrop` rather than `.draggable`/`.dropDestination`: the
    /// newer pair never started a drag from these rows, in any build, on any
    /// copy of the app. The older pair is the AppKit dragging session with a
    /// SwiftUI face on it, and it begins on a drag threshold rather than on a
    /// gesture that a tap can win.
    ///
    /// A modifier rather than an `if` in the body, because the two branches of
    /// an `if` are different view types and SwiftUI would rebuild the row when
    /// a desk is pinned, dropping its selection mid drag.
    @ViewBuilder
    func ifPickedUp(_ pickUp: Bool, repo: String) -> some View {
        if pickUp {
            self.onDrag {
                // The repo, as plain text. `NSItemProvider(object:)` is what a
                // drop written against `.utf8PlainText` actually receives.
                NSItemProvider(object: repo as NSString)
            }
        } else {
            self
        }
    }

    /// The drop half. `before` is the repo the dragged desk lands above, or nil
    /// for the end of the list.
    func pinDrop(_ accept: @escaping (String) -> Void) -> some View {
        onDrop(of: [.utf8PlainText], isTargeted: nil) { providers in
            guard let provider = providers.first else { return false }
            _ = provider.loadObject(ofClass: NSString.self) { value, _ in
                guard let repo = value as? String else { return }
                Task { @MainActor in accept(repo) }
            }
            return true
        }
    }
}

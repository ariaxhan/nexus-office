import SwiftUI
import UniformTypeIdentifiers

/// The left column: who you can talk to, what the machine knows, then the floor.
///
/// Bots come first because they are colleagues you message. The wall follows
/// with the machine-wide picture before the longer list of individual desks.
/// Everything keeps the same row shape, so the eye reads one list.
struct RosterView: View {
    @Bindable var store: Store

    var body: some View {
        VStack(spacing: 0) {
            top
            ScrollViewReader { scroll in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 1) {
                    homeRow

                    feedRow

                    bots

                    wall

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
                                .pinDrop(over: nil) { repo, _ in
                                    Task { await store.movePin(repo: repo, before: nil) }
                                }
                        }
                    }
                    if store.visibleDesks.isEmpty && store.worldNotice == nil {
                        notary(store.needsOnly ? "nothing needs you right now" : "no desks yet")
                    }

                    files

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
            // A selected wall row must remain visible when selection changes
            // from elsewhere, just as the put-away drawer scrolls to what it
            // revealed.
            .onChange(of: store.selection) { _, now in
                guard case .section(let id)? = now else { return }
                withAnimation { scroll.scrollTo(rowID(section: id), anchor: .center) }
            }
            }
        }
        // A range rather than one number, because the roster is a column of
        // other people's repo names and the right width for them is not a
        // decision this file gets to make once. `HSplitView` starts it at the
        // width it has always had and lets the divider move it from there;
        // `minimal` has no divider to drag, so it takes the whole window.
        .frame(minWidth: 140,
               idealWidth: Theme.rosterWidth,
               maxWidth: store.layout == .minimal ? .infinity : 560,
               maxHeight: .infinity)
        .background(Theme.roster)
    }

    // MARK: - parts

    /// One desk. `pickUp` puts `.draggable` INSIDE the tap gesture rather than
    /// outside it, which is the whole difference between a row that can be
    /// dragged and one that cannot: a tap gesture attached closer to the
    /// content claims the mouse down and the drag never begins, so a `.draggable`
    /// wrapped around the outside of it is dead code that reads as a feature.
    private func deskRow(_ station: Station, pickUp: Bool = true) -> some View {
        DeskRow(station: station,
                gate: store.gateShown(at: station),
                selected: store.selection == .desk(station.repo),
                away: store.isHidden(station),
                face: FaceBook.shared.hex(repo: station.repo))
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
                Button("Face colour\u{2026}") { store.facePicker = station.repo }
                if let url = URL(string: "https://github.com/\(station.repo)") {
                    Link("Open on GitHub", destination: url)
                }
            }
            // Anchored on the row so the well opens next to the face it is
            // changing, and driven off the store so a row scrolling out from
            // under a `LazyVStack` cannot take the open picker with it.
            .popover(isPresented: Binding(
                get: { store.facePicker == station.repo },
                set: { open in if !open, store.facePicker == station.repo { store.facePicker = nil } }
            ), arrowEdge: .trailing) {
                FacePicker(repo: station.repo, store: store)
            }
    }

    /// A pinned desk can be picked up and dropped on another: the dropped one
    /// lands just above the row it was dropped on. Order is the whole point of
    /// the group, so it is the one place on the roster a drag means anything.
    private func pinnedRow(_ station: Station) -> some View {
        deskRow(station, pickUp: true)
            .pinDrop(over: station.repo) { repo, before in
                Task { await store.movePin(repo: repo, before: before) }
            }
    }

    /// The name over one group of desks: "pinned", or an owner.
    ///
    /// The pinned name is also a drop target, so a desk from an owner group can
    /// be dragged into the pins and lands at the top of them. Dropping onto a
    /// row lands above that row, which leaves the very top unreachable
    /// otherwise, exactly as the spacer past the last pin covers the bottom.
    private func groupHeader(_ title: String) -> some View {
        Text(title)
            .officeFont(size: 10.5, weight: .semibold)
            .foregroundStyle(Theme.faint)
            .padding(.horizontal, 10)
            .padding(.top, 8)
            .padding(.bottom, 2)
            .contentShape(Rectangle())
            .ifDropsIntoPins(title == StateRules.pinnedHeader) { repo in
                Task { await store.movePin(repo: repo, before: store.pinOrder.first) }
            }
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
    /// The colleagues. Behind a toggle, like the wall: a person who never talks
    /// to a bot is scrolling past them all day. Turning the group off hides
    /// rows, never a raised hand: a gate comes from the runtime and opens a
    /// sheet over the whole window whatever this is set to.
    @ViewBuilder private var bots: some View {
        if store.showBots {
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
        }
    }

    @ViewBuilder private var wall: some View {
        if store.showWall, !store.sections.isEmpty {
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

    /// The way in to the home: everything waiting on a person, in one place.
    ///
    /// Above the feed, which is above everything else, because the feed answers
    /// "what has this machine been doing" and this answers "what is it waiting
    /// on me for", and only one of those two is a thing nobody else can do.
    private var homeRow: some View {
        // Things to open, not things to do: one card per issue and one row per
        // source that wants somebody. The same arithmetic the home's own header
        // does, because two counts of the same list that disagree on screen are
        // two counts nobody trusts.
        let waiting = StateRules.needsQueue(store.stations).count
                    + store.sections.filter { $0.needs > 0 }.count
        return Button {
            store.select(.home)
        } label: {
            HStack(spacing: 9) {
                Circle()
                    .fill(waiting > 0 ? Theme.red : Theme.faint)
                    .frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 2) {
                    Text("needs you")
                        .officeFont(size: 12.5, weight: .medium)
                        .foregroundStyle(Theme.text)
                    Text(waiting > 0
                         ? "\(waiting) waiting on you, across every desk"
                         : "nothing is waiting on you")
                        .officeFont(size: 11)
                        .foregroundStyle(waiting > 0 ? Theme.red.opacity(0.85) : Theme.dim)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 6)
                if waiting > 0 {
                    Pill(text: "\(waiting)", color: Theme.red)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(store.selection == .home ? Theme.selected : Color.clear)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    /// The way in to the global feed: every repo talking at once.
    ///
    /// Above the bots and the wall on purpose. It is the only row that answers
    /// "what has this machine been doing", which is the first question anyone
    /// walking up to the office actually has, and a row below the fold is a row
    /// nobody reads.
    ///
    /// The count is the two kinds that want a person, never the total: a badge
    /// that counts everything is a badge that means nothing within a day.
    private var feedRow: some View {
        let feed = store.feed()
        let wants = feed.asking + feed.blocked
        return Button {
            store.select(.feed)
        } label: {
            HStack(spacing: 9) {
                Circle()
                    .fill(wants > 0 ? Theme.amber : (feed.total > 0 ? Theme.green : Theme.faint))
                    .frame(width: 8, height: 8)
                VStack(alignment: .leading, spacing: 2) {
                    Text("the feed")
                        .officeFont(size: 12.5, weight: .medium)
                        .foregroundStyle(Theme.text)
                    Text(feed.posts.first?.text ?? feed.emptyLine)
                        .officeFont(size: 11)
                        .foregroundStyle(wants > 0 ? Theme.amber.opacity(0.85) : Theme.dim)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 6)
                if wants > 0 {
                    Pill(text: "\(wants)", color: Theme.amber)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(store.selection == .feed ? Theme.selected : Color.clear)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
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
                        .officeFont(size: 12.5, weight: .medium)
                        .foregroundStyle(Theme.text)
                    Text(page.headline.isEmpty ? "not read yet" : page.headline)
                        .officeFont(size: 11)
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
                .officeFont(size: 11, weight: .semibold)
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
                        .officeSymbol(size: 8, weight: .semibold)
                    Text(store.putAwayHeadline)
                        .officeFont(size: 11, weight: .semibold)
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
                    // The drawer has no order to change and a desk dragged out
                    // of it would be pinned and still in the drawer, since put
                    // away outranks a pin. So a drawer row is not picked up.
                    deskRow(station, pickUp: false)
                }
            }
            // The anchor is the bottom of the drawer, not the top of it, so
            // opening it brings the desks into view rather than just the header
            // a person already clicked.
            Color.clear.frame(height: 1).id(putAwayAnchor)
        }
    }

    /// The search field and the gear, on one line.
    ///
    /// The gear is here rather than behind a keystroke because every preference
    /// this app had was a shortcut nobody could find. The header controls stay
    /// where they are: they are the fast way to the same two settings, and this
    /// is the way you can see.
    private var top: some View {
        HStack(spacing: 8) {
            search
            gear
        }
        .padding(.horizontal, 12)
        .padding(.top, 38)
        .padding(.bottom, 10)
    }

    /// Settings, in a popover. Its own window, so the shot harness photographs
    /// it the same way it photographs the gate sheet.
    private var gear: some View {
        Button {
            store.settingsOpen.toggle()
        } label: {
            Image(systemName: "gearshape")
                .officeSymbol(size: 12, weight: .medium)
                .foregroundStyle(store.settingsOpen ? Theme.text : Theme.faint)
                .frame(width: 26, height: 26)
                .background(RoundedRectangle(cornerRadius: 7, style: .continuous)
                    .fill(Theme.raised))
        }
        .buttonStyle(.plain)
        .help("Settings. Everything here is a view of the same floor and persists.")
        .popover(isPresented: $store.settingsOpen, arrowEdge: .bottom) {
            SettingsPopover(store: store)
        }
    }

    private var search: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .officeSymbol(size: 11, weight: .medium)
                .foregroundStyle(Theme.faint)
            TextField("Search", text: $store.query)
                .textFieldStyle(.plain)
                .officeFont(size: 12.5)
                .foregroundStyle(Theme.text)
            if !store.query.isEmpty {
                Button {
                    store.query = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .officeSymbol(size: 11)
                        .foregroundStyle(Theme.faint)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 8)
        .frame(height: 26)
        .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Theme.raised))
    }

    private func header(_ title: String, trailing: AnyView?) -> some View {
        HStack {
            Text(title)
                .officeFont(size: 11, weight: .semibold)
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
                            .officeLabel()
                    } else {
                        Text(order.label)
                    }
                }
            }
        } label: {
            HStack(spacing: 3) {
                Image(systemName: "arrow.up.arrow.down")
                    .officeSymbol(size: 9, weight: .semibold)
                Text(store.deskSort.label)
                    .officeFont(size: 11, weight: store.deskSort == .owner ? .regular : .semibold)
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
                    .officeFont(size: 11, weight: store.needsOnly ? .semibold : .regular)
                    .foregroundStyle(store.needsOnly ? Theme.amber : Theme.faint)
        }
        .buttonStyle(.plain)
        .help("Show only the desks a person has to touch. A raised hand is never hidden by it.")
    }

    // MARK: - what the search found on this machine

    /// Every document on this machine that matches what is in the box.
    ///
    /// Under the desks rather than over them, because the three lists above are
    /// already filtered by the same word and a person looking for a repo should
    /// not have to scroll past forty files to see it. Nothing here can hide a
    /// desk: this is an addition to the roster, never a replacement for it.
    @ViewBuilder private var files: some View {
        if !store.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            ForEach(store.found.groups, id: \.kind) { group in
                header(group.kind.header, trailing: nil)
                    .padding(.top, 14)
                ForEach(group.hits) { hit in
                    fileRow(hit)
                }
            }
            searchNote
        }
    }

    /// The one line under the results, and the only place the search is allowed
    /// to be quiet about something. A scan that stopped early, a door with no
    /// vault, and "nothing matched, out of this many" are three different facts
    /// and none of them may render as an empty list.
    @ViewBuilder private var searchNote: some View {
        let typed = store.query.trimmingCharacters(in: .whitespacesAndNewlines)
        if typed.count < Store.searchFloor {
            EmptyView()
        } else if !store.searchError.isEmpty {
            notary(store.searchError)
        } else if store.searching && store.found.results.isEmpty {
            notary("looking\u{2026}")
        } else if store.found.results.isEmpty {
            notary(store.found.files > 0
                   ? "no file on this machine says that (\(store.found.files) read)"
                   : "no files to search")
        } else if store.found.truncated {
            counter("more matched than are shown")
        }
    }

    /// One found document. The name, where it lives, and for a mention the line
    /// it matched on, quoted. Clicking it opens that desk's Context at that
    /// file, which is the same door the Context pane uses.
    private func fileRow(_ hit: SearchHit) -> some View {
        Button {
            store.open(hit)
        } label: {
            HStack(alignment: .top, spacing: 9) {
                Image(systemName: hit.kind == .goto ? "arrow.right.circle" : "doc.text")
                    .officeSymbol(size: 11, weight: .medium)
                    .foregroundStyle(hit.kind == .goto ? Theme.blue : Theme.faint)
                    .frame(width: 14)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: 2) {
                    Text(hit.name)
                        .officeFont(size: 12.5, weight: .medium)
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Text(hit.place)
                        .officeFont(size: 10.5)
                        .foregroundStyle(Theme.faint)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    if !hit.snippet.isEmpty {
                        Text("\(hit.line)  \(hit.snippet)")
                            .officeFont(size: 10.5, design: .monospaced)
                            .foregroundStyle(Theme.dim)
                            .lineLimit(2)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("\(hit.repo) \u{00b7} \(hit.path)")
    }

    private func notary(_ text: String) -> some View {
        Text(text)
            .officeFont(size: 11.5)
            .foregroundStyle(Theme.faint)
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
    }

    private func counter(_ text: String) -> some View {
        Text(text)
            .officeFont(size: 10.5)
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
                        .officeFont(size: 13.5, weight: .medium)
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)
                    if hasGate { GateMark(size: 9) }
                    Spacer(minLength: 4)
                    Text(StateRules.stamp(bot.last?.at ?? ""))
                        .officeFont(size: 11)
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
                .officeFont(size: 12).foregroundStyle(Theme.amber).lineLimit(1)
        } else if let error = bot.error {
            Text(StateRules.line(error, limit: 60))
                .officeFont(size: 12).foregroundStyle(Theme.red).lineLimit(1)
        } else if bot.busy {
            Text("working")
                .officeFont(size: 12).foregroundStyle(Theme.blue).lineLimit(1)
        } else {
            // The last thing it said, or, before it has said anything, what it
            // is FOR. A column of rows all reporting an empty transcript
            // tells a person nothing about which colleague to open.
            Text(StateRules.botSubtitle(bot: bot, limit: 64))
                .officeFont(size: 12)
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
    /// The colour this desk keeps, handed in from the face book. Status is the
    /// badge on the corner of it and never the disc itself, so a desk is
    /// recognisable whatever it happens to be doing.
    var face: String

    private var state: DeskState { StateRules.deskState(station: station, gate: gate) }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            DeskFace(repo: station.repo, state: state, hex: face)
            VStack(alignment: .leading, spacing: 1) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(station.repo)
                        .officeFont(size: 13.5, weight: .medium)
                        .foregroundStyle(Theme.text)
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Spacer(minLength: 4)
                    Text(StateRules.stamp(station.at))
                        .officeFont(size: 11)
                        .foregroundStyle(Theme.faint)
                }
                HStack(spacing: 5) {
                    Text(state.label)
                        .foregroundStyle(Theme.color(state))
                    if !detail.isEmpty {
                        Text(detail).foregroundStyle(Theme.dim)
                    }
                }
                .officeFont(size: 12)
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
                        .officeFont(size: 13.5, weight: .medium)
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
                            .officeFont(size: 11)
                            .foregroundStyle(Theme.faint)
                    }
                }
                Text(StateRules.sectionSubtitle(section))
                    .officeFont(size: 12)
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

/// Why the drag is written this way, after two attempts that did nothing.
///
/// The roster is a `LazyVStack` inside a `ScrollView`, and each row carries an
/// `.onTapGesture` for selection. That combination is the documented way to
/// stop a drag from ever starting: a tap gesture attached to a row claims the
/// mouse down, and the scroll view claims what is left, so `.draggable` never
/// reaches its threshold. Moving `.draggable` inside the tap did not fix it.
///
/// So this uses the pair that predates `Transferable` and is a thin face over
/// the AppKit dragging session: `.onDrag` to begin one, and `.onDrop` with a
/// `DropDelegate` rather than the closure form. The delegate matters: the
/// closure form of `.onDrop` only hears the drop itself, while `dropEntered`
/// fires while the pointer is still moving, which is what makes a row reorder
/// under the cursor instead of jumping once at the end.
///
/// `ROSTER_DRAG_LOG=1` prints each stage to stderr, because a drag that does
/// nothing is indistinguishable from a drag that never started, and the two
/// have different fixes.
enum DragLog {
    static let on = ProcessInfo.processInfo.environment["ROSTER_DRAG_LOG"] == "1"
    static func say(_ what: String) {
        guard on else { return }
        FileHandle.standardError.write(Data("roster-drag: \(what)\n".utf8))
    }
}

/// Reorders while the pointer is still moving, and lands the change on drop.
struct PinDropDelegate: DropDelegate {
    /// The row this delegate belongs to, or nil for the strip past the last pin.
    let over: String?
    let move: (String, String?) -> Void

    func validateDrop(info: DropInfo) -> Bool {
        info.hasItemsConforming(to: [.utf8PlainText])
    }

    func dropEntered(info: DropInfo) {
        DragLog.say("entered \(over ?? "the end")")
    }

    func dropUpdated(info: DropInfo) -> DropProposal? {
        DropProposal(operation: .move)
    }

    func performDrop(info: DropInfo) -> Bool {
        guard let item = info.itemProviders(for: [.utf8PlainText]).first else {
            DragLog.say("dropped on \(over ?? "the end") with nothing in it")
            return false
        }
        _ = item.loadObject(ofClass: NSString.self) { value, error in
            guard let repo = value as? String else {
                DragLog.say("could not read the dragged desk: \(error?.localizedDescription ?? "no value")")
                return
            }
            DragLog.say("dropped \(repo) before \(self.over ?? "the end")")
            Task { @MainActor in self.move(repo, self.over) }
        }
        return true
    }
}

/// A detail pane taking a desk dragged out of the roster.
///
/// Same `DropDelegate` shape as the pins and for the same reason: the closure
/// form of `.onDrop` cannot say "this pane will take it" while the pointer is
/// still moving, so the cursor shows a refusal right up until the release.
struct DeskPaneDropDelegate: DropDelegate {
    let open: (String) -> Void

    func validateDrop(info: DropInfo) -> Bool {
        info.hasItemsConforming(to: [.utf8PlainText])
    }

    func dropUpdated(info: DropInfo) -> DropProposal? {
        DropProposal(operation: .copy)
    }

    func performDrop(info: DropInfo) -> Bool {
        guard let item = info.itemProviders(for: [.utf8PlainText]).first else {
            DragLog.say("dropped on a pane with nothing in it")
            return false
        }
        _ = item.loadObject(ofClass: NSString.self) { value, error in
            guard let repo = value as? String else {
                DragLog.say("could not read the dragged desk: \(error?.localizedDescription ?? "no value")")
                return
            }
            DragLog.say("dropped \(repo) into a pane")
            Task { @MainActor in self.open(repo) }
        }
        return true
    }
}

extension View {
    /// This pane opens whatever desk is dropped on it. The way a second desk
    /// gets open at all in `compare`: clicking the roster drives the left pane,
    /// so the right one needs a gesture of its own.
    func deskDrop(_ open: @escaping (String) -> Void) -> some View {
        onDrop(of: [.utf8PlainText], delegate: DeskPaneDropDelegate(open: open))
    }

    /// The drag half, applied only where a drag means something.
    @ViewBuilder
    func ifPickedUp(_ pickUp: Bool, repo: String) -> some View {
        if pickUp {
            self.onDrag {
                DragLog.say("picked up \(repo)")
                return NSItemProvider(object: repo as NSString)
            }
        } else {
            self
        }
    }

    /// The drop half. `over` is the pinned desk the dragged one lands above,
    /// or nil for the strip past the last pin.
    func pinDrop(over: String?, _ move: @escaping (String, String?) -> Void) -> some View {
        onDrop(of: [.utf8PlainText],
               delegate: PinDropDelegate(over: over, move: move))
    }

    /// A drop destination, applied only where a drop means something. Same
    /// shape and same reason as `ifPickedUp`.
    @ViewBuilder
    func ifDropsIntoPins(_ accepts: Bool, drop: @escaping (String) -> Void) -> some View {
        if accepts {
            self.dropDestination(for: String.self) { repos, _ in
                guard let repo = repos.first else { return false }
                drop(repo)
                return true
            }
        } else { self }
    }
}

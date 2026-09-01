import SwiftUI

/// One thing on the wall, opened.
///
/// Every word on this screen came out of the card its source wrote. There is no
/// branch in this file for any particular source, and there must never be one:
/// six python files fill `world.sections` today and the next one has to be able
/// to arrive without a line of Swift. If something here wants a special case,
/// the card is the wrong shape and that is where it gets fixed.
struct SectionView: View {
    let section: Section

    private var mood: StateRules.SectionMood { StateRules.mood(section) }

    /// When the source last looked. Said in the header, beside the name, and
    /// only when it actually said: a wall that invents a time is a wall you
    /// cannot trust about anything else either.
    private var asOf: String {
        let when = StateRules.moment(section.card.asOf)
        return when.isEmpty ? "" : "as of \(when)"
    }

    var body: some View {
        VStack(spacing: 0) {
            head
            Divider().overlay(Theme.hairline)
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    headline
                    facts
                    detail
                    rows
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollContentBackground(.hidden)
        }
        .background(Theme.ink)
    }

    // MARK: - head

    private var head: some View {
        HStack(spacing: 9) {
            MoodDot(mood: mood, size: 22)
            Text(section.title)
                .officeFont(size: 13, weight: .medium)
                .foregroundStyle(Theme.text)
            // The state in the source's own word. `ok` is not worth a badge,
            // so only the states that mean something get one.
            if !section.isOK {
                Pill(text: section.state, color: Theme.color(mood))
            }
            if section.needs > 0 {
                Pill(text: "\(section.needs) need you", color: Theme.amber)
            }
            Spacer()
            if !asOf.isEmpty {
                Text(asOf)
                    .officeFont(size: 11)
                    .foregroundStyle(Theme.faint)
            }
        }
        .padding(.horizontal, 18)
        .frame(height: 44)
        .padding(.top, 8)
    }

    // MARK: - the sentence

    /// The one line the source wanted said. For a state that is not ok, this is
    /// what is wrong, in its own words rather than in a word this app guessed.
    private var headline: some View {
        Text(section.headline)
            .officeFont(size: 14)
            .foregroundStyle(section.isOK ? Theme.text : Theme.amber)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: 720, alignment: .leading)
    }

    // MARK: - the numbers

    /// Label on the left, value on the right, one row each.
    ///
    /// Two columns rather than a sentence because these are meant to be
    /// compared down the column, and a tone from the source rather than from
    /// here, because only the source knows whether three is good news.
    @ViewBuilder private var facts: some View {
        let rows = section.card.facts
        if !rows.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(rows.enumerated()), id: \.offset) { index, fact in
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        Text(fact.label)
                            .officeFont(size: 12.5)
                            .foregroundStyle(Theme.dim)
                            .lineLimit(2)
                        Spacer(minLength: 16)
                        Text(fact.value)
                            .officeFont(size: 12.5, weight: .medium)
                            .foregroundStyle(Theme.tone(StateRules.tone(fact)))
                            .lineLimit(1)
                    }
                    .padding(.vertical, 7)
                    if index < rows.count - 1 {
                        Rectangle()
                            .fill(Theme.hairline)
                            .frame(height: 0.5)
                    }
                }
            }
            .padding(.horizontal, 12)
            .background(
                RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Theme.raised)
            )
            .frame(maxWidth: 460, alignment: .leading)
        }
    }

    // MARK: - the long version

    /// Whatever else the source had to say. Quiet, under everything, and only
    /// when it is not the sentence already at the top of the screen.
    @ViewBuilder private var detail: some View {
        if !section.detail.isEmpty && section.detail != section.headline {
            Text(section.detail)
                .officeFont(size: 12)
                .foregroundStyle(Theme.faint)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: 720, alignment: .leading)
        }
    }

    // MARK: - the list

    /// The table under the numbers, for the sources whose whole point is the
    /// list: 630 learnings, 45 scheduled jobs.
    ///
    /// There is no branch here for either of them, and there must never be one.
    /// The source decided the order, the headings, the badge and the tone; this
    /// draws whatever arrived. Lazy because a shelf is hundreds of rows long and
    /// a `VStack` would build every one of them before the first is on screen.
    @ViewBuilder private var rows: some View {
        let groups = SectionRows.grouped(section.card.rows)
        if !groups.isEmpty {
            LazyVStack(alignment: .leading, spacing: 18, pinnedViews: [.sectionHeaders]) {
                ForEach(groups) { group in
                    SwiftUI.Section {
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(Array(group.rows.enumerated()), id: \.offset) { index, row in
                                SectionRowView(row: row)
                                if index < group.rows.count - 1 {
                                    Rectangle().fill(Theme.hairline).frame(height: 0.5)
                                }
                            }
                        }
                        .background(
                            RoundedRectangle(cornerRadius: 9, style: .continuous)
                                .fill(Theme.raised)
                        )
                    } header: {
                        if !group.name.isEmpty {
                            HStack(spacing: 7) {
                                Text(group.name)
                                    .officeFont(size: 11, weight: .semibold)
                                    .foregroundStyle(Theme.faint)
                                Text("\(group.rows.count)")
                                    .officeFont(size: 11)
                                    .foregroundStyle(Theme.faint.opacity(0.7))
                                Spacer(minLength: 0)
                            }
                            .padding(.vertical, 5)
                            .background(Theme.ink)
                        }
                    }
                }
            }
            .frame(maxWidth: 720, alignment: .leading)
        }
    }
}

/// Rows, under the headings the source asked for.
///
/// First-appearance order, never sorted here: the source already put the
/// failing jobs above the paused ones, and a second opinion in this file would
/// silently disagree with the count on the card above it.
enum SectionRows {
    struct Group: Identifiable {
        let name: String
        let rows: [SectionRowItem]
        var id: String { name }
    }

    static func grouped(_ rows: [SectionRowItem]) -> [Group] {
        var order: [String] = []
        var byName: [String: [SectionRowItem]] = [:]
        for row in rows {
            if byName[row.group] == nil { order.append(row.group) }
            byName[row.group, default: []].append(row)
        }
        return order.map { Group(name: $0, rows: byName[$0] ?? []) }
    }
}

/// One line of whatever this is.
///
/// The link is the only decision made here, and it is made by asking the model
/// rather than by reading the string: `https` and `file` are places to go, and
/// everything else stays text. A row that draws a button which does nothing is
/// worse than a row that draws no button.
struct SectionRowView: View {
    let row: SectionRowItem

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                title
                if !row.subtitle.isEmpty {
                    Text(row.subtitle)
                        .officeFont(size: 11.5)
                        .foregroundStyle(Theme.dim)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !row.detail.isEmpty {
                    Text(row.detail)
                        .officeFont(size: 11)
                        .foregroundStyle(Theme.faint)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 10)
            if !row.badge.isEmpty {
                Pill(text: row.badge, color: Theme.tone(StateRules.tone(row.tone)))
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .textSelection(.enabled)
    }

    @ViewBuilder private var title: some View {
        if let destination = row.destination {
            Link(row.title, destination: destination)
                .officeFont(size: 12.5, weight: .medium)
                .foregroundStyle(Theme.blue)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            Text(row.title)
                .officeFont(size: 12.5, weight: .medium)
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

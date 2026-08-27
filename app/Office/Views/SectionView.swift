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
                .font(.system(size: 13, weight: .medium))
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
                    .font(.system(size: 11))
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
            .font(.system(size: 14))
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
                            .font(.system(size: 12.5))
                            .foregroundStyle(Theme.dim)
                            .lineLimit(2)
                        Spacer(minLength: 16)
                        Text(fact.value)
                            .font(.system(size: 12.5, weight: .medium))
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
                .font(.system(size: 12))
                .foregroundStyle(Theme.faint)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: 720, alignment: .leading)
        }
    }
}

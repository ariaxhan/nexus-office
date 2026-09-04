import SwiftUI

/// The home: everything waiting on a person, across every desk, in one place.
///
/// The mirror of the phone page at `/`. The office already answers "what is
/// this desk doing" twelve times over, and answered "what needs me" nowhere: a
/// person had to click twelve desks to find the two with a question on them,
/// which is the same as not being asked.
///
/// The order is the order a person can act in, and it is the phone's order for
/// the same reason both exist: a stated question with buttons, then a fix
/// already written and waiting to be merged, then the parks nobody can do
/// anything about yet, then the wall.
///
/// It reuses `IssueCard` rather than drawing its own. Two renderers of the same
/// issue agree on the day they are written and disagree the first time a button
/// is added to one of them.
struct NeedsView: View {
    @Bindable var store: Store

    var body: some View {
        let gates = store.gates
        return VStack(spacing: 0) {
            head(waiting: gates.count)
            Divider().overlay(Theme.hairline)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    CatchUpCard(store: store, waiting: gates.count)
                    ForEach(gates, id: \.id) { gate in
                        GateNeed(gate: gate)
                    }
                    if gates.isEmpty {
                        Text("Nothing needs you. Agents keep working around ordinary failures.")
                            .officeFont(size: 12.5)
                            .foregroundStyle(Theme.faint)
                            .padding(.top, 24)
                    }
                }
                .padding(16)
            }
            .scrollContentBackground(.hidden)
        }
        .background(Theme.ink)
    }

    private func head(waiting: Int) -> some View {
        HStack(spacing: 9) {
            Text("needs you")
                .officeFont(size: 14, weight: .semibold)
                .foregroundStyle(Theme.text)
            if waiting > 0 {
                Pill(text: "\(waiting)", color: Theme.amber)
            }
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 11)
        .background(Theme.roster)
    }

}

private struct GateNeed: View {
    let gate: Gate
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(gate.permission.isEmpty ? "agent permission" : gate.permission)
                .officeFont(size: 12.5, weight: .medium).foregroundStyle(Theme.text)
            Text(gate.target).officeFont(size: 11.5, design: .monospaced)
                .foregroundStyle(Theme.dim).lineLimit(3)
        }
        .padding(13).frame(maxWidth: 720, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.raised))
    }
}

/// The one line about what happened while nobody was looking, so the list below
/// is a set of decisions and not also a changelog.
///
/// The stamp is this Mac's own and never leaves it. An install that has never
/// opened this screen has no window to count over, and says so rather than
/// counting everything that ever happened as news.
struct CatchUpCard: View {
    @Bindable var store: Store
    let waiting: Int

    var body: some View {
        let since = store.homeSince
        let got = StateRules.catchUp(store.automation, since: since)
        return VStack(alignment: .leading, spacing: 6) {
            Text(since.map { "since \(StateRules.moment($0))" } ?? "since you were last here")
                .officeFont(size: 11)
                .foregroundStyle(Theme.faint)
            HStack(spacing: 14) {
                if since != nil {
                    count("\(got.worked)", "worked")
                    count("\(got.landed)", "landed")
                    count("\(got.asked)", "asked you")
                }
                count("\(waiting)", "needs you", tone: waiting > 0 ? Theme.amber : Theme.dim)
                Spacer()
            }
        }
        .padding(13)
        .frame(maxWidth: 720, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.raised))
    }

    private func count(_ number: String, _ label: String,
                       tone: Color = Theme.text) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 5) {
            Text(number)
                .officeFont(size: 15, weight: .semibold, design: .monospaced)
                .foregroundStyle(tone)
            Text(label)
                .officeFont(size: 11)
                .foregroundStyle(Theme.dim)
        }
    }
}

/// A desk whose waiting issues carry no question yet. A count and a way in,
/// never a wall of rows: there is nothing to decide on one of these until the
/// runner states the choice, and it is never hidden either, because a park the
/// office does not draw is one nobody nudges.
struct ParkCount: View {
    @Bindable var store: Store
    let repo: String
    let count: Int

    var body: some View {
        Button { store.select(.desk(repo)) } label: {
            HStack(spacing: 9) {
                Circle().fill(Theme.amber.opacity(0.55)).frame(width: 6, height: 6)
                Text(repo)
                    .officeFont(size: 12.5, weight: .medium)
                    .foregroundStyle(Theme.text)
                Text(count == 1 ? "1 issue waiting on you" : "\(count) issues waiting on you")
                    .officeFont(size: 11)
                    .foregroundStyle(Theme.dim)
                Spacer(minLength: 8)
                Pill(text: "\(count)", color: Theme.amber)
            }
            .padding(.horizontal, 13)
            .padding(.vertical, 9)
            .frame(maxWidth: 720, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.raised))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// One thing on the wall that wants a person, and the way into its card.
struct WallNeed: View {
    @Bindable var store: Store
    let section: Section

    var body: some View {
        Button { store.select(.section(section.id)) } label: {
            HStack(spacing: 9) {
                Circle().fill(Theme.amber).frame(width: 6, height: 6)
                VStack(alignment: .leading, spacing: 2) {
                    Text(section.title)
                        .officeFont(size: 12.5, weight: .medium)
                        .foregroundStyle(Theme.text)
                    Text(section.headline)
                        .officeFont(size: 11)
                        .foregroundStyle(Theme.dim)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 8)
                Pill(text: "\(section.needs)", color: Theme.amber)
            }
            .padding(.horizontal, 13)
            .padding(.vertical, 9)
            .frame(maxWidth: 720, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.raised))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

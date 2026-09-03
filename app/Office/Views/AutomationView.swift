import SwiftUI

/// The automation, as one page.
///
/// The answer to "there is an hourly cron, show me how it works, when it is
/// processing, which issues, and links to the comments". Everything on it was
/// measured by the server; this file arranges and never derives, for the reason
/// `SectionView` gives about cards: two renderers each deciding whether a number
/// is bad is two places for it to go wrong, in two languages.
///
/// The order down the page is the order a person asks:
///
///   1. is it working right now
///   2. when does it look, and when did it last finish
///   3. is the other way in (the webhook) alive
///   4. WHAT DID IT TOUCH, with a link to the comment it left
///   5. how does the whole thing work, for the first time you read this
///
/// The activity list is the point. Four is under five because a mechanism you
/// have already read is a thing you scroll past forever.
struct AutomationView: View {
    @Bindable var store: Store

    private var page: Automation { store.automation }

    private var mood: Color {
        if page.state != "ok" && page.state != "off" { return Theme.red }
        if page.needsSomebody { return Theme.amber }
        if page.now.running { return Theme.green }
        return Theme.faint
    }

    var body: some View {
        VStack(spacing: 0) {
            head
            Divider().overlay(Theme.hairline)
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    headline
                    strip
                    conveyor
                    trigger
                    activity
                    how
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollContentBackground(.hidden)
        }
        .background(Theme.ink)
    }

    private var conveyor: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack { Text("delivery conveyor").officeFont(size: 11, weight: .semibold).foregroundStyle(Theme.faint)
                Pill(text: page.delivery.pipelineHealth,
                     color: page.delivery.blocked.isEmpty ? Theme.green : Theme.red) }
            deliveryGroup("running now", page.delivery.runningNow, Theme.green)
            deliveryGroup("next up", page.delivery.nextUp, Theme.blue)
            deliveryGroup("blocked", page.delivery.blocked, Theme.red)
            deliveryGroup("completed recently", page.delivery.completedRecently, Theme.faint)
        }
        .padding(12).frame(maxWidth: 720, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Theme.raised))
    }

    @ViewBuilder private func deliveryGroup(_ label: String, _ rows: [Automation.DeliveryRow], _ tone: Color) -> some View {
        if !rows.isEmpty {
            Text("\(label): " + rows.map { "\($0.repo)#\($0.pr) \($0.problems.first ?? ($0.next.isEmpty ? $0.phase : $0.next))" }.joined(separator: " · "))
                .officeFont(size: 12).foregroundStyle(tone).fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: - head

    private var head: some View {
        HStack(spacing: 9) {
            Circle().fill(mood).frame(width: 9, height: 9)
            Text("Automation")
                .officeFont(size: 13, weight: .medium)
                .foregroundStyle(Theme.text)
            if page.now.running {
                Pill(text: "running", color: Theme.green)
            }
            if page.schedule.killSwitch {
                Pill(text: "kill switch on", color: Theme.amber)
            }
            Spacer()
            Button("close") { store.automationOpen = false }
                .buttonStyle(.plain)
                .officeFont(size: 11)
                .foregroundStyle(Theme.dim)
        }
        .padding(.horizontal, 18)
        .frame(height: 44)
        .padding(.top, 8)
    }

    // MARK: - the sentence

    private var headline: some View {
        Text(page.headline.isEmpty ? "the office has not read the pipeline yet" : page.headline)
            .officeFont(size: 14)
            .foregroundStyle(page.needsSomebody ? Theme.amber : Theme.text)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: 720, alignment: .leading)
    }

    // MARK: - when it looks, and what it reached

    /// Four numbers, side by side, because they are only meaningful together: a
    /// fresh heartbeat over zero repos is a runner that woke up and did nothing,
    /// and neither number says that on its own.
    private var strip: some View {
        HStack(alignment: .top, spacing: 10) {
            Tile(label: "schedule",
                 value: page.schedule.line.isEmpty ? "unknown" : page.schedule.line,
                 tone: page.schedule.overdue ? Theme.amber : Theme.text)
            Tile(label: "last full sweep",
                 value: sweepLine,
                 tone: page.reached.repos == nil || page.reached.repos == 0 ? Theme.amber : Theme.text)
            Tile(label: "doing",
                 value: page.now.running
                     ? (page.now.doing.isEmpty ? "a run is in flight" : page.now.doing)
                     : (page.now.lastSaid.isEmpty ? "nothing" : "last: \(page.now.lastSaid)"),
                 tone: page.now.running ? Theme.green : Theme.faint)
            Tile(label: "power",
                 value: page.schedule.deferring
                     ? "on battery, deferring every run" : page.schedule.power,
                 tone: page.schedule.deferring ? Theme.amber : Theme.faint)
        }
        .frame(maxWidth: 900, alignment: .leading)
    }

    private var sweepLine: String {
        guard let repos = page.reached.repos else {
            return "receipts \(page.reached.state)"
        }
        let when = page.schedule.lastFullRun.isEmpty ? "never" : StateRules.moment(page.schedule.lastFullRun)
        return "\(when), \(repos) repos in \(page.reached.window)"
    }

    // MARK: - the other way in

    /// The webhook path, and above all WHY nothing is arriving when nothing is.
    ///
    /// This block is here because "quiet" and "nothing can reach us" read
    /// identically from inside a quiet room, and the second one lasts for weeks.
    @ViewBuilder private var trigger: some View {
        let hurt = !page.trigger.blockedBy.isEmpty
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text("webhooks")
                    .officeFont(size: 11, weight: .semibold)
                    .foregroundStyle(Theme.faint)
                Pill(text: page.trigger.state,
                     color: hurt ? Theme.red : (page.trigger.reachable ? Theme.green : Theme.amber))
                if page.trigger.queued > 0 {
                    Pill(text: "\(page.trigger.queued) queued", color: Theme.blue)
                }
            }
            if hurt {
                Text(page.trigger.blockedBy)
                    .officeFont(size: 12.5)
                    .foregroundStyle(Theme.red)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text(triggerLine)
                    .officeFont(size: 12.5)
                    .foregroundStyle(Theme.dim)
            }
        }
        .padding(12)
        .frame(maxWidth: 720, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Theme.raised))
    }

    private var triggerLine: String {
        let today = page.trigger.today ?? 0
        guard let age = page.trigger.lastAge else {
            return "\(today) today; nothing has ever arrived"
        }
        return "\(today) today, last \(StateRules.gap(age)) ago, \(page.trigger.runsToday ?? 0) runs"
    }

    // MARK: - what it touched

    @ViewBuilder private var activity: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text("what it touched")
                    .officeFont(size: 11, weight: .semibold)
                    .foregroundStyle(Theme.faint)
                // Never a silent cap. A list that quietly stops reads as "that
                // is everything that happened".
                if page.activityDropped > 0 {
                    Text("newest \(page.activity.count) of \(page.activity.count + page.activityDropped)")
                        .officeFont(size: 11)
                        .foregroundStyle(Theme.faint)
                }
            }
            if page.activity.isEmpty {
                Text("no issue touched in the last day. The sweeps that only counted "
                     + "open issues are not listed here.")
                    .officeFont(size: 12.5)
                    .foregroundStyle(Theme.faint)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 620, alignment: .leading)
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(page.activity) { row in
                        ActivityRow(row: row)
                        if row.id != page.activity.last?.id {
                            Rectangle().fill(Theme.hairline).frame(height: 0.5)
                        }
                    }
                }
                .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Theme.raised))
                .frame(maxWidth: 720, alignment: .leading)
            }
        }
    }

    // MARK: - how the whole thing works

    /// Last on the page on purpose. It is the thing you read once.
    @ViewBuilder private var how: some View {
        if !page.how.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("how it works")
                    .officeFont(size: 11, weight: .semibold)
                    .foregroundStyle(Theme.faint)
                ForEach(Array(page.how.enumerated()), id: \.offset) { index, line in
                    HStack(alignment: .top, spacing: 10) {
                        Text("\(index + 1)")
                            .officeFont(size: 11, weight: .semibold, monospacedDigits: true)
                            .foregroundStyle(Theme.faint)
                            .frame(width: 14, alignment: .trailing)
                        Text(line)
                            .officeFont(size: 12.5)
                            .foregroundStyle(Theme.dim)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .frame(maxWidth: 720, alignment: .leading)
        }
    }
}

/// One number with its name over it. Four of these across the top.
private struct Tile: View {
    let label: String
    let value: String
    var tone: Color = Theme.text

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .officeFont(size: 10.5, weight: .semibold)
                .foregroundStyle(Theme.faint)
            Text(value)
                .officeFont(size: 12.5)
                .foregroundStyle(tone)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Theme.raised))
    }
}

/// One thing the runner did, and the way to what it said about it.
///
/// The whole row is the link, and the label on it says WHICH link it is: "read
/// the comment" when the office knows exactly where the runner's words are, and
/// "open the issue" when a human has replied since, which moves the last comment
/// and makes a deep link point at the wrong words.
private struct ActivityRow: View {
    let row: Automation.Activity

    private var tone: Color {
        Theme.tone(StateRules.tone(row.tone))
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 7) {
                    Text("\(row.repo)#\(row.issue)")
                        .officeFont(size: 12.5, weight: .medium)
                        .foregroundStyle(Theme.text)
                    Pill(text: StateRules.outcomeLabel(row.outcome), color: tone)
                        .fixedSize(horizontal: true, vertical: false)
                    Text(row.ago)
                        .officeFont(size: 11)
                        .foregroundStyle(Theme.faint)
                }
                if !row.title.isEmpty {
                    Text(row.title)
                        .officeFont(size: 12)
                        .foregroundStyle(Theme.dim)
                        .lineLimit(1)
                }
                // What that word means, for anybody who has not read
                // dispatch.sh. The runner's own detail wins when it wrote one,
                // because it is about this issue and the other is about the word.
                Text(row.detail.isEmpty ? row.means : row.detail)
                    .officeFont(size: 11.5)
                    .foregroundStyle(Theme.faint)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 12)
            if let link = row.link {
                Link(row.hasComment ? "read the comment" : "open the issue", destination: link)
                    .officeFont(size: 11.5)
                    .foregroundStyle(Theme.blue)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
    }
}

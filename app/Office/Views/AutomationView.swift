import SwiftUI

struct AutomationView: View {
    @Bindable var store: Store
    private var board: RunBoard { store.automation.runs }
    private var work: WorkBoard { store.automation.work }
    private var tower: TowerBoard { store.automation.tower }

    var body: some View {
        VStack(spacing: 0) {
            head
            Divider().overlay(Theme.hairline)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    towerIssues
                    if work.state != "ok" {
                        notice(work.detail.isEmpty ? "product state is not available" : work.detail,
                               color: Theme.amber)
                    } else {
                        products
                    }
                    automationHistory
                }
                .padding(18)
                .frame(maxWidth: 820, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollContentBackground(.hidden)
        }
        .background(Theme.ink)
    }

    private var head: some View {
        HStack(spacing: 9) {
            Circle().fill(board.needs > 0 ? Theme.red : (board.active > 0 ? Theme.blue : Theme.green))
                .frame(width: 9, height: 9)
            Text("Tower").officeFont(size: 14, weight: .semibold).foregroundStyle(Theme.text)
            Text("issue work and automation").officeFont(size: 11).foregroundStyle(Theme.faint)
            Spacer()
            Button("close") { store.automationOpen = false }
                .buttonStyle(.plain).officeFont(size: 11).foregroundStyle(Theme.dim)
        }
        .padding(.horizontal, 18)
        .frame(height: 52)
    }

    private var products: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("What exists now").officeFont(size: 11, weight: .semibold).foregroundStyle(Theme.text)
            if work.products.isEmpty {
                notice("Every tracked product acceptance item is complete.", color: Theme.green)
            } else {
                ForEach(work.products) { product in ProductRow(product: product) }
            }
        }
    }

    private var towerIssues: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Tower · GitHub issues").officeFont(size: 12, weight: .semibold)
                    .foregroundStyle(Theme.text)
                Spacer()
                if tower.state == "ok" {
                    Text(towerCounts).officeFont(size: 11).foregroundStyle(Theme.dim)
                }
            }
            if tower.state != "ok" {
                notice(tower.detail.isEmpty ? "Tower state is unavailable" : tower.detail,
                       color: Theme.amber)
            } else {
                towerPulse
                if tower.activity == "idle" {
                    notice("Idle: no eligible Tower work.", color: Theme.green)
                }
                ForEach(tower.issues) { issue in TowerIssueRow(issue: issue, inspect: inspect(issue.attempt)) }
                if tower.dropped > 0 {
                    Text("\(tower.dropped) more issues are in the Tower ledger")
                        .officeFont(size: 11).foregroundStyle(Theme.faint)
                }
                completions
            }
        }
    }

    private var towerCounts: String {
        var parts = ["\(tower.working) working", "\(tower.queued) queued"]
        if !tower.oldestQueued.isEmpty { parts[1] += " (oldest \(tower.oldestQueued))" }
        parts += ["\(tower.held) held", "\(tower.retrying) retrying"]
        if tower.failed > 0 { parts.append("\(tower.failed) failed") }
        return parts.joined(separator: " · ")
    }

    /// One word for the whole of Tower, then whether its own receipts say it is alive.
    private var towerPulse: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Circle().fill(towerColor(tower.activity)).frame(width: 8, height: 8)
                Text(tower.activity.isEmpty ? "unknown" : tower.activity)
                    .officeFont(size: 13, weight: .semibold).foregroundStyle(Theme.text)
                Text(pulseLine).officeFont(size: 11).foregroundStyle(Theme.dim)
            }
            ForEach(tower.runs) { run in
                HStack(spacing: 6) {
                    Text("\(run.plan) · \(run.state) for \(run.age)")
                        .officeFont(size: 11).foregroundStyle(Theme.faint)
                    if let url = inspect(run.flight) {
                        Link("inspect", destination: url).officeFont(size: 11).foregroundStyle(Theme.blue)
                    }
                }
            }
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 9).fill(Theme.raised))
    }

    private var pulseLine: String {
        let live = tower.tower
        if !live.detail.isEmpty { return live.detail }
        return tower.tickAge.isEmpty ? "tower \(live.state)" : "tower \(live.state) · last tick \(tower.tickAge) ago"
    }

    private var completions: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("Verified recently").officeFont(size: 11, weight: .semibold).foregroundStyle(Theme.text)
                .padding(.top, 6)
            if tower.completions.isEmpty {
                Text("No landed, verified Tower result on record.")
                    .officeFont(size: 11).foregroundStyle(Theme.faint)
            }
            ForEach(tower.completions) { done in
                HStack(spacing: 6) {
                    Text("\(done.issue.isEmpty ? done.flight : done.issue) · landed \(String(done.sha.prefix(7))) · \(done.age) ago")
                        .officeFont(size: 11).foregroundStyle(Theme.dim)
                    if let url = inspect(done.flight) {
                        Link("inspect", destination: url).officeFont(size: 11).foregroundStyle(Theme.blue)
                    }
                }
            }
        }
    }

    private func inspect(_ flight: String) -> URL? { store.api.inspectURL(flight: flight) }

    private var automationHistory: some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider().overlay(Theme.hairline).padding(.vertical, 4)
            HStack {
                Text("Recent runs").officeFont(size: 11, weight: .semibold)
                    .foregroundStyle(Theme.text)
                Spacer()
                Text("\(board.active) running · \(board.done) active workflows completed")
                    .officeFont(size: 10.5).foregroundStyle(Theme.faint)
            }
            if board.state != "ok" {
                notice(board.detail.isEmpty ? "the run ledger is not available" : board.detail,
                       color: Theme.amber)
            } else {
                if board.recentRuns.isEmpty {
                    Text("No automation run in the last 24 hours.")
                        .officeFont(size: 12).foregroundStyle(Theme.faint)
                } else {
                    VStack(spacing: 0) {
                        ForEach(Array(board.recentRuns.enumerated()), id: \.element.id) { index, run in
                            RunRow(run: run, initiallyOpen: index == 0)
                        }
                    }
                    .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.raised))
                }
            }
        }
    }

    private func notice(_ text: String, color: Color) -> some View {
        Text(text).officeFont(size: 12.5).foregroundStyle(color).padding(13)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.raised))
    }
}

private func towerColor(_ state: String) -> Color {
    switch state {
    case "working": Theme.blue
    case "ready", "queued", "idle": Theme.green
    case "retrying", "waiting", "paused", "held": Theme.amber
    case "failed", "down", "erroring": Theme.red
    default: Theme.faint
    }
}

private struct TowerIssueRow: View {
    let issue: TowerBoard.Issue
    let inspect: URL?

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(towerColor(issue.state)).frame(width: 7, height: 7).padding(.top, 5)
            VStack(alignment: .leading, spacing: 4) {
                if let url = URL(string: issue.url) {
                    Link("\(issue.repo)#\(issue.number) · \(issue.title)", destination: url)
                        .officeFont(size: 12, weight: .medium).foregroundStyle(Theme.text)
                } else {
                    Text(issue.title).officeFont(size: 12, weight: .medium)
                }
                Text([issue.state, issue.detail, issue.next].filter { !$0.isEmpty }.joined(separator: " · "))
                    .officeFont(size: 11).foregroundStyle(Theme.dim)
                    .fixedSize(horizontal: false, vertical: true)
                if !issue.obligation.isEmpty {
                    Text("Next: \(issue.obligation)").officeFont(size: 11).foregroundStyle(Theme.dim)
                }
                HStack(spacing: 8) {
                    Text(timing).officeFont(size: 10.5).foregroundStyle(Theme.faint)
                    if let inspect {
                        Link("inspect \(issue.attempt)", destination: inspect)
                            .officeFont(size: 10.5).foregroundStyle(Theme.blue)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 9).fill(Theme.raised))
    }

    private var timing: String {
        var parts: [String] = []
        if !issue.phase.isEmpty { parts.append("phase \(issue.phase)") }
        if !issue.age.isEmpty { parts.append("for \(issue.age)") }
        parts.append(issue.progress.isEmpty ? "no attempt progress yet" : "last progress \(issue.progress) ago")
        return parts.joined(separator: " · ")
    }
}

private struct ProductRow: View {
    let product: WorkBoard.Product
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { open.toggle() } label: {
                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 8) {
                            Text(product.name).officeFont(size: 13, weight: .semibold)
                                .foregroundStyle(Theme.text)
                            Text(product.status).officeFont(size: 11.5).foregroundStyle(Theme.green)
                        }
                        Text(product.remaining == 1 ? "1 acceptance item remains" :
                             "\(product.remaining) acceptance items remain")
                            .officeFont(size: 11.5).foregroundStyle(Theme.amber)
                    }
                    Spacer()
                    Text(open ? "less" : "details").officeFont(size: 11).foregroundStyle(Theme.faint)
                }
                .padding(13).contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if open {
                VStack(alignment: .leading, spacing: 7) {
                    fact("changed", product.changed)
                    fact("not done", product.blocked)
                    fact("next", product.next)
                    if !product.proof.isEmpty {
                        HStack(spacing: 10) {
                            Text("proof").frame(width: 58, alignment: .leading)
                                .officeFont(size: 10.5, weight: .semibold).foregroundStyle(Theme.faint)
                            ForEach(product.proof) { proof in
                                if let url = URL(string: proof.url) {
                                    Link(proof.label + " ↗", destination: url)
                                        .officeFont(size: 11.5).foregroundStyle(Theme.blue)
                                }
                            }
                        }
                    }
                }
                .padding(.horizontal, 13).padding(.bottom, 13)
            }
        }
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.raised))
    }

    private func fact(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(label).frame(width: 58, alignment: .leading)
                .officeFont(size: 10.5, weight: .semibold).foregroundStyle(Theme.faint)
            Text(value.isEmpty ? "no verified change recorded" : value)
                .officeFont(size: 11.5).foregroundStyle(Theme.dim)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private struct RunRow: View {
    let run: RunBoard.Run
    @State private var open: Bool

    init(run: RunBoard.Run, initiallyOpen: Bool = false) {
        self.run = run
        _open = State(initialValue: initiallyOpen)
    }
    private var color: Color {
        switch run.state {
        case "landed", "produced": return Theme.green
        case "failed", "cancelled": return Theme.red
        default: return Theme.blue
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { open.toggle() } label: {
                HStack(alignment: .top, spacing: 9) {
                    Circle().fill(color).frame(width: 7, height: 7).padding(.top, 5)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(run.summary.isEmpty ? run.title : run.summary)
                            .officeFont(size: 12).foregroundStyle(Theme.text).lineLimit(2)
                            .multilineTextAlignment(.leading)
                        HStack(spacing: 7) {
                            Text(run.title).foregroundStyle(Theme.faint)
                            Pill(text: run.state, color: color)
                            Text(StateRules.gap(run.duration)).foregroundStyle(Theme.faint)
                        }
                        .officeFont(size: 10.5)
                    }
                    Spacer(minLength: 10)
                    Text(StateRules.moment(run.endedAt.isEmpty ? run.startedAt : run.endedAt))
                        .officeFont(size: 10.5).foregroundStyle(Theme.faint)
                    Text(open ? "hide" : "receipts")
                        .officeFont(size: 10.5).foregroundStyle(Theme.blue)
                }
                .padding(.horizontal, 12).padding(.vertical, 9).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                if run.transcript.isEmpty {
                    Text("This run left no readable transcript.")
                        .officeFont(size: 11.5).foregroundStyle(Theme.faint)
                } else {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(run.transcript) { block in
                            VStack(alignment: .leading, spacing: 3) {
                                Text(block.speaker).officeFont(size: 10.5, weight: .semibold)
                                    .foregroundStyle(block.speaker == "error" ? Theme.red : Theme.faint)
                                Text(Markdown.render(block.text)).officeFont(size: 12)
                                    .foregroundStyle(Theme.text).textSelection(.enabled)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                    .padding(11)
                    .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Theme.well))
                }
                Divider().overlay(Theme.hairline).padding(.top, 10)
            }
        }
    }
}

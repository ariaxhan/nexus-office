import SwiftUI

struct AutomationView: View {
    @Bindable var store: Store
    private var board: RunBoard { store.automation.runs }

    var body: some View {
        VStack(spacing: 0) {
            head
            Divider().overlay(Theme.hairline)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    summary
                    if board.state != "ok" {
                        notice(board.detail.isEmpty ? "the run ledger is not available" : board.detail,
                               color: Theme.amber)
                    } else if board.families.isEmpty {
                        notice("No automated run has finished in the last 24 hours.", color: Theme.faint)
                    } else {
                        group("Needs you", families: board.families.filter(\.needs), color: Theme.red)
                        group("In progress", families: board.families.filter { !$0.needs && $0.active > 0 },
                              color: Theme.blue, empty: "Nothing running right now.")
                        group("Still open", families: board.families.filter {
                            !$0.needs && $0.active == 0 && $0.open > 0
                        }, color: Theme.amber, empty: "No queued work.")
                        group("Recently done", families: board.families.filter {
                            !$0.needs && $0.active == 0 && $0.open == 0
                        }, color: Theme.green)
                    }
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
            Text("Work board").officeFont(size: 14, weight: .semibold).foregroundStyle(Theme.text)
            Text("last 24 hours").officeFont(size: 11).foregroundStyle(Theme.faint)
            Spacer()
            Button("close") { store.automationOpen = false }
                .buttonStyle(.plain).officeFont(size: 11).foregroundStyle(Theme.dim)
        }
        .padding(.horizontal, 18)
        .frame(height: 52)
    }

    private var summary: some View {
        HStack(spacing: 8) {
            Metric(value: board.done, label: "done", color: Theme.green)
            Metric(value: board.active, label: "running", color: Theme.blue)
            Metric(value: board.open, label: "open", color: Theme.text)
            Metric(value: board.needs, label: "needs you", color: board.needs > 0 ? Theme.red : Theme.faint)
        }
    }

    @ViewBuilder private func group(_ title: String, families: [RunBoard.Family],
                                    color: Color, empty: String? = nil) -> some View {
        if !families.isEmpty || empty != nil {
            VStack(alignment: .leading, spacing: 8) {
                Text(title).officeFont(size: 11, weight: .semibold).foregroundStyle(color)
                if families.isEmpty, let empty {
                    Text(empty).officeFont(size: 12).foregroundStyle(Theme.faint).padding(.vertical, 4)
                } else {
                    VStack(spacing: 0) {
                        ForEach(families) { family in
                            RunFamilyRow(family: family, color: color)
                            if family.id != families.last?.id {
                                Rectangle().fill(Theme.hairline).frame(height: 0.5)
                            }
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

private struct Metric: View {
    let value: Int
    let label: String
    let color: Color
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(value)").officeFont(size: 19, weight: .semibold, design: .monospaced)
                .foregroundStyle(color)
            Text(label).officeFont(size: 10.5, weight: .medium).foregroundStyle(Theme.faint)
        }
        .padding(.horizontal, 12).padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Theme.raised))
    }
}

private struct RunFamilyRow: View {
    let family: RunBoard.Family
    let color: Color
    @State private var open = false
    @State private var openRun: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { open.toggle() } label: {
                HStack(alignment: .top, spacing: 10) {
                    Circle().fill(color).frame(width: 7, height: 7).padding(.top, 5)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 7) {
                            Text(family.name).officeFont(size: 12.5, weight: .medium)
                                .foregroundStyle(Theme.text)
                            if family.count > 1 {
                                Text("\(family.count) runs").officeFont(size: 10.5)
                                    .foregroundStyle(Theme.faint)
                            }
                        }
                        Text(family.summary.isEmpty ? family.state : family.summary)
                            .officeFont(size: 11.5).foregroundStyle(Theme.dim).lineLimit(2)
                            .multilineTextAlignment(.leading)
                    }
                    Spacer(minLength: 10)
                    Text(open ? "hide" : "open").officeFont(size: 11).foregroundStyle(Theme.faint)
                }
                .padding(12).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                VStack(spacing: 0) {
                    ForEach(family.runs) { run in
                        RunRow(run: run, open: openRun == run.id) {
                            openRun = openRun == run.id ? nil : run.id
                        }
                    }
                }
                .padding(.horizontal, 12).padding(.bottom, 10)
            }
        }
    }
}

private struct RunRow: View {
    let run: RunBoard.Run
    let open: Bool
    let toggle: () -> Void
    private var color: Color {
        switch run.state {
        case "landed", "produced": return Theme.green
        case "failed", "cancelled": return Theme.red
        default: return Theme.blue
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Button(action: toggle) {
                HStack(spacing: 8) {
                    Pill(text: run.state, color: color)
                    Text(StateRules.moment(run.endedAt.isEmpty ? run.startedAt : run.endedAt))
                        .officeFont(size: 11).foregroundStyle(Theme.faint)
                    Text(StateRules.gap(run.duration)).officeFont(size: 11).foregroundStyle(Theme.faint)
                    Spacer()
                    Text(open ? "close transcript" : "open transcript")
                        .officeFont(size: 11).foregroundStyle(Theme.blue)
                }
                .padding(.vertical, 8).contentShape(Rectangle())
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
            }
        }
    }
}

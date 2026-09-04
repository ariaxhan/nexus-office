import SwiftUI

struct AutomationView: View {
    @Bindable var store: Store
    private var board: RunBoard { store.automation.runs }
    private var work: WorkBoard { store.automation.work }

    var body: some View {
        VStack(spacing: 0) {
            head
            Divider().overlay(Theme.hairline)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    if work.state != "ok" {
                        notice(work.detail.isEmpty ? "product state is not available" : work.detail,
                               color: Theme.amber)
                    } else {
                        products
                        recentChanges
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
            Text("Work").officeFont(size: 14, weight: .semibold).foregroundStyle(Theme.text)
            Text("what exists now").officeFont(size: 11).foregroundStyle(Theme.faint)
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

    private var recentChanges: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Recent changes").officeFont(size: 11, weight: .semibold).foregroundStyle(Theme.text)
            if work.changes.isEmpty {
                Text("No product change landed in the last 24 hours.")
                    .officeFont(size: 12).foregroundStyle(Theme.faint)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(work.changes.enumerated()), id: \.element.id) { index, change in
                        ChangeRow(store: store, change: change, initiallyOpen: index == 0)
                    }
                }
                .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.raised))
            }
        }
    }

    private var automationHistory: some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider().overlay(Theme.hairline).padding(.vertical, 4)
            HStack {
                Text("Automation history").officeFont(size: 11, weight: .semibold)
                    .foregroundStyle(Theme.faint)
                Spacer()
                Text("\(board.active) running · \(board.done) active workflows completed")
                    .officeFont(size: 10.5).foregroundStyle(Theme.faint)
            }
            if board.state != "ok" {
                notice(board.detail.isEmpty ? "the run ledger is not available" : board.detail,
                       color: Theme.amber)
            } else {
                group("", families: board.families, color: Theme.faint)
            }
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

private struct ChangeRow: View {
    @Bindable var store: Store
    let change: WorkBoard.Change
    @State private var open: Bool

    init(store: Store, change: WorkBoard.Change, initiallyOpen: Bool = false) {
        self.store = store
        self.change = change
        _open = State(initialValue: initiallyOpen)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { open.toggle() } label: {
                HStack(spacing: 9) {
                    Text("✓").officeFont(size: 12, weight: .semibold).foregroundStyle(Theme.green)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(change.summary).officeFont(size: 12).foregroundStyle(Theme.text).lineLimit(2)
                        Text(change.project).officeFont(size: 10.5).foregroundStyle(Theme.faint)
                    }
                    Spacer()
                    Text(StateRules.moment(change.at)).officeFont(size: 10.5).foregroundStyle(Theme.faint)
                    Text(open ? "hide" : "receipts").officeFont(size: 10.5).foregroundStyle(Theme.blue)
                }
                .padding(.horizontal, 12).padding(.vertical, 9).contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                VStack(alignment: .leading, spacing: 9) {
                    references
                    artifactGroup("chronicle", change.chronicles)
                    artifactGroup("documents", change.documents.filter { document in
                        !change.chronicles.contains(where: { $0.id == document.id })
                    })
                    fileGroup(change.files.filter { file in
                        !change.documents.contains(where: { $0.id == file.id })
                    })
                }
                .padding(.horizontal, 30).padding(.bottom, 12)
            }
        }
    }

    private var references: some View {
        HStack(spacing: 10) {
            if let url = URL(string: change.url) {
                Link("commit ↗", destination: url)
            }
            if let url = URL(string: change.pr.url), !change.pr.label.isEmpty {
                Link(change.pr.label + " ↗", destination: url)
            }
            ForEach(change.issues) { issue in
                if let url = URL(string: issue.url) { Link(issue.label + " ↗", destination: url) }
            }
            if change.issues.isEmpty {
                Text("no issue linked").foregroundStyle(Theme.faint)
            }
        }
        .officeFont(size: 11).foregroundStyle(Theme.blue)
    }

    @ViewBuilder private func artifactGroup(_ label: String, _ artifacts: [WorkBoard.Artifact]) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label).officeFont(size: 10.5, weight: .semibold).foregroundStyle(Theme.faint)
            if artifacts.isEmpty {
                Text("none generated").officeFont(size: 11.5).foregroundStyle(Theme.faint)
            } else {
                ForEach(artifacts) { artifact in
                    Button {
                        Task { await store.open(repo: artifact.repo, path: artifact.path) }
                    } label: {
                        HStack(spacing: 6) {
                            Text(artifact.name).lineLimit(1)
                            Text("open in desk ↗").foregroundStyle(Theme.blue)
                        }
                        .officeFont(size: 11.5).foregroundStyle(Theme.text)
                    }
                    .buttonStyle(.plain).help(artifact.path)
                }
            }
        }
    }

    @ViewBuilder private func fileGroup(_ files: [WorkBoard.Artifact]) -> some View {
        if !files.isEmpty {
            VStack(alignment: .leading, spacing: 5) {
                Text("files").officeFont(size: 10.5, weight: .semibold).foregroundStyle(Theme.faint)
                ForEach(files) { file in
                    if let url = URL(string: file.url) {
                        Link(file.name + " ↗", destination: url)
                            .officeFont(size: 11.5).foregroundStyle(Theme.blue).help(file.path)
                    }
                }
            }
        }
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

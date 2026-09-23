import SwiftUI

/// Every coordinator and every daily report, in one pane.
///
/// Reports first: they were written for Aria and used to land only inside five
/// separate bot chats. Then one row per coordinator with its health, what it is
/// doing, its lanes and what it shipped, and a line that goes into its inbox.
struct CoordinatorsView: View {
    let store: Store
    @State private var reports: [BotReport] = []
    @State private var rows: [CoordinatorRow] = []
    @State private var problem = ""
    @State private var open: Set<String> = []

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Coordinators & reports").officeFont(size: 13, weight: .medium).foregroundStyle(Theme.text)
                Spacer()
                if !problem.isEmpty { Text(problem).officeFont(size: 11).foregroundStyle(Theme.amber).lineLimit(1) }
            }
            .padding(.horizontal, 18).frame(height: 44).padding(.top, 8)
            Divider().overlay(Theme.hairline)
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    heading("Coordinators")
                    ForEach(rows) { row in CoordinatorCard(store: store, row: row) }
                    if rows.isEmpty { Text("Reading the coordinators…").officeFont(size: 12).foregroundStyle(Theme.dim) }
                    heading("Daily reports")
                    ForEach(reports) { report in reportCard(report) }
                }
                .padding(.horizontal, 18).padding(.vertical, 16)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollContentBackground(.hidden)
        }
        .background(Theme.ink)
        .task {
            while !Task.isCancelled {
                await load()
                try? await Task.sleep(for: .seconds(20))
            }
        }
    }

    private func load() async {
        do {
            async let coordinators = store.api.coordinators()
            async let latest = store.api.reports()
            rows = try await coordinators.coordinators
            reports = try await latest.reports
            problem = ""
        } catch {
            problem = error.localizedDescription
        }
    }

    private func heading(_ text: String) -> some View {
        Text(text.uppercased()).officeFont(size: 11, weight: .medium).foregroundStyle(Theme.faint)
    }

    private func reportCard(_ report: BotReport) -> some View {
        let expanded = open.contains(report.bot)
        let text = report.text ?? report.error ?? "No report yet."
        return VStack(alignment: .leading, spacing: 6) {
            Button {
                if expanded { open.remove(report.bot) } else { open.insert(report.bot) }
            } label: {
                HStack(alignment: .firstTextBaseline) {
                    Text(report.title).officeFont(size: 13.5, weight: .medium).foregroundStyle(Theme.text)
                    Text(StateRules.moment(report.at ?? "")).officeFont(size: 11).foregroundStyle(Theme.faint)
                    Spacer()
                    Text(expanded ? "less" : "read").officeFont(size: 11).foregroundStyle(Theme.dim)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            MarkdownText(raw: text, size: 12.5, limit: expanded ? nil : 2)
                .textSelection(.enabled)
        }
        .padding(12)
        .frame(maxWidth: 760, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.raised))
    }
}

/// One coordinator: the health line, the work, and the steering box.
struct CoordinatorCard: View {
    let store: Store
    let row: CoordinatorRow
    @State private var draft = ""
    @State private var said = ""
    @State private var sending = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Circle().fill(row.health == "running" ? Theme.blue : row.healthy ? Theme.green : Theme.red)
                    .frame(width: 8, height: 8)
                Text(row.name).officeFont(size: 14, weight: .medium).foregroundStyle(Theme.text)
            }
            Text(row.error ?? row.line).officeFont(size: 12)
                .foregroundStyle(row.healthy ? Theme.dim : Theme.red)
            if let doing = row.working_on, !doing.isEmpty {
                MarkdownText(raw: doing, size: 12.5, limit: 3).textSelection(.enabled)
            }
            if let lanes = row.lanes, !lanes.isEmpty {
                label("Open lanes")
                ForEach(lanes, id: \.self) { lane in
                    Text(lane).officeFont(size: 11.5).foregroundStyle(Theme.dim).lineLimit(1)
                }
            }
            if let commits = row.commits, !commits.isEmpty {
                label("Last shipped")
                ForEach(commits) { commit in
                    Text("\(commit.checkout) \(commit.sha.prefix(8)) \(commit.subject)")
                        .officeFont(size: 11.5).foregroundStyle(Theme.dim).lineLimit(1)
                }
            }
            HStack {
                TextField("Steer \(row.name)", text: $draft, axis: .vertical)
                    .textFieldStyle(.roundedBorder).lineLimit(1...4)
                    .onSubmit { send() }
                Button("Send") { send() }
                    .disabled(sending || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            if !said.isEmpty { Text(said).officeFont(size: 11).foregroundStyle(Theme.faint) }
        }
        .padding(12)
        .frame(maxWidth: 760, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Theme.raised))
    }

    private func label(_ text: String) -> some View {
        Text(text).officeFont(size: 11, weight: .medium).foregroundStyle(Theme.faint).padding(.top, 2)
    }

    private func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        sending = true
        let id = UUID().uuidString
        Task {
            do {
                _ = try await store.api.steer(coordinator: row.id, text: text, id: id)
                draft = ""
                said = "In \(row.name)'s inbox; it reads it next run."
            } catch {
                said = "Not sent: \(error.localizedDescription)"
            }
            sending = false
        }
    }
}

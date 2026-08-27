import SwiftUI

/// One repo, as the thing you actually do something about.
///
/// The issues are cards with buttons that apply on the spot, so a toast carries
/// what happened in the server's own words rather than a queue position. A PR
/// only offers merge when GitHub itself says MERGEABLE and the PR is not a
/// draft: UNKNOWN is "ask again in a moment", never permission.
struct DeskThreadView: View {
    @Bindable var store: Store
    let station: Station

    /// The live gate, if this desk is one of the desks it is shown at. Read from
    /// the two second gate poll, never from the world snapshot this station was
    /// decoded out of.
    private var gate: Gate? { store.gateShown(at: station) }

    private var state: DeskState { StateRules.deskState(station: station, gate: gate) }

    var body: some View {
        VStack(spacing: 0) {
            head
            Divider().overlay(Theme.hairline)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if let gate {
                        GateCard(store: store, gate: gate)
                    }
                    if let error = station.issuesError {
                        problem(error)
                    }
                    ForEach(station.issues) { issue in
                        IssueCard(store: store, repo: station.repo, issue: issue)
                    }
                    if !station.prs.isEmpty {
                        Text("pull requests")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(Theme.faint)
                            .padding(.top, 8)
                    }
                    if let error = station.prsError {
                        problem(error)
                    }
                    ForEach(station.prs) { pr in
                        PullRequestCard(store: store, repo: station.repo, pr: pr)
                    }
                    if station.issues.isEmpty && station.prs.isEmpty
                        && station.issuesError == nil && gate == nil {
                        Text(station.detail.isEmpty ? "Nothing open here." : station.detail)
                            .font(.system(size: 12.5))
                            .foregroundStyle(Theme.faint)
                            .padding(.top, 20)
                    }
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 14)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollContentBackground(.hidden)
            if let toast = store.toast {
                ToastBar(text: toast) { store.toast = nil }
            }
        }
        .background(Theme.ink)
    }

    private var head: some View {
        HStack(spacing: 9) {
            StateDot(state: state, size: 22)
            VStack(alignment: .leading, spacing: 1) {
                Text(station.repo)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(Theme.text)
                HStack(spacing: 5) {
                    Text(state.label).foregroundStyle(Color(hex: state.hex))
                    if !station.detail.isEmpty {
                        Text(station.detail).foregroundStyle(Theme.faint)
                    }
                }
                .font(.system(size: 11))
                .lineLimit(1)
            }
            Spacer()
            Text(StateRules.stamp(station.at))
                .font(.system(size: 11))
                .foregroundStyle(Theme.faint)
        }
        .padding(.horizontal, 18)
        .frame(height: 44)
        .padding(.top, 8)
    }

    private func problem(_ text: String) -> some View {
        Label(text, systemImage: "exclamationmark.triangle.fill")
            .font(.system(size: 12))
            .foregroundStyle(Theme.red)
    }
}

// MARK: - an issue

struct IssueCard: View {
    @Bindable var store: Store
    let repo: String
    let issue: Issue

    @State private var commenting = false
    @State private var draft = ""
    @State private var busy = false

    private var needsYou: Bool { StateRules.needsHuman(issue: issue) }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("#\(issue.number)")
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.faint)
                Text(issue.title)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                Text(StateRules.stamp(issue.updatedAt))
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.faint)
            }

            if needsYou || !issue.labels.isEmpty {
                HStack(spacing: 6) {
                    if needsYou {
                        // Not a label lookup. The bot had the last word, which is
                        // the same sentence the runner uses to decide.
                        HStack(spacing: 5) {
                            Circle().fill(Theme.red).frame(width: 6, height: 6)
                            Text("waiting on you").font(.system(size: 11, weight: .medium))
                        }
                        .foregroundStyle(Theme.red)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 2)
                        .background(Capsule().fill(Theme.red.opacity(0.14)))
                    }
                    ForEach(issue.labels, id: \.self) { label in
                        Pill(text: label)
                    }
                }
            }

            if !issue.body.isEmpty {
                Text(Markdown.render(issue.body))
                    .font(.system(size: 12.5))
                    .foregroundStyle(Theme.dim)
                    .textSelection(.enabled)
                    .tint(Theme.blue)
                    .lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(spacing: 7) {
                CardButton(title: commenting ? "cancel" : "comment", busy: busy) {
                    commenting.toggle()
                }
                CardButton(title: "close", busy: busy) { apply("close") }
                CardButton(title: "reopen", busy: busy) { apply("reopen") }
                CardButton(title: "nudge", busy: busy) { apply("nudge") }
                Spacer()
                if let url = URL(string: issue.url), url.scheme == "https" {
                    Link("open on GitHub", destination: url)
                        .font(.system(size: 11))
                        .foregroundStyle(Theme.faint)
                }
            }

            if commenting {
                VStack(alignment: .leading, spacing: 7) {
                    TextField("Answer it. A reply without the bot's marker is what re-queues it.",
                              text: $draft, axis: .vertical)
                        .textFieldStyle(.plain)
                        .font(.system(size: 12.5))
                        .foregroundStyle(Theme.text)
                        .lineLimit(2...8)
                        .padding(9)
                        .background(RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(Color.black))
                    HStack {
                        Spacer()
                        CardButton(title: "send comment", tint: Theme.green, busy: busy) {
                            apply("comment", body: draft)
                        }
                    }
                }
            }
        }
        .padding(13)
        .frame(maxWidth: 720, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Theme.raised)
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .strokeBorder(needsYou ? Theme.red.opacity(0.3) : Color.clear, lineWidth: 1)
                )
        )
    }

    private func apply(_ kind: String, body: String? = nil) {
        if kind == "comment" && (body ?? "").trimmingCharacters(in: .whitespaces).isEmpty { return }
        busy = true
        Task {
            _ = await store.decide(kind: kind, repo: repo, issue: String(issue.number), body: body)
            busy = false
            commenting = false
            draft = ""
        }
    }
}

// MARK: - a pull request

struct PullRequestCard: View {
    @Bindable var store: Store
    let repo: String
    let pr: PullRequest

    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("#\(pr.number)")
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundStyle(Theme.faint)
                Text(pr.title)
                    .font(.system(size: 13.5, weight: .medium))
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                Text(StateRules.stamp(pr.updatedAt))
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.faint)
            }
            HStack(spacing: 6) {
                Pill(text: "\(pr.head) into \(pr.base)")
                if pr.draft { Pill(text: "draft", color: Theme.dim) }
                Pill(text: pr.mergeable.lowercased(),
                     color: pr.canMerge ? Theme.green : Theme.amber)
            }
            HStack(spacing: 7) {
                if pr.canMerge {
                    CardButton(title: "merge", tint: Theme.green, busy: busy) {
                        busy = true
                        Task {
                            _ = await store.decide(kind: "merge", repo: repo,
                                                   issue: pr.closes.first.map(String.init) ?? "",
                                                   pr: pr.number)
                            busy = false
                        }
                    }
                } else {
                    Text(pr.draft ? "a draft is a statement that it is not ready"
                                  : "GitHub has not said this can merge")
                        .font(.system(size: 11))
                        .foregroundStyle(Theme.faint)
                }
                Spacer()
                if let url = URL(string: pr.url), url.scheme == "https" {
                    Link("open on GitHub", destination: url)
                        .font(.system(size: 11))
                        .foregroundStyle(Theme.faint)
                }
            }
        }
        .padding(13)
        .frame(maxWidth: 720, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.raised))
    }
}

// MARK: - small parts

struct CardButton: View {
    let title: String
    var tint: Color = Theme.dim
    var busy = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(busy ? "working" : title)
                .font(.system(size: 11.5, weight: .medium))
                .foregroundStyle(tint)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(tint.opacity(0.13)))
        }
        .buttonStyle(.plain)
        .disabled(busy)
    }
}

/// What actually happened, in the server's words. Applied on the spot means the
/// answer is real, so it is worth quoting rather than paraphrasing.
struct ToastBar: View {
    let text: String
    let dismiss: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Text(text)
                .font(.system(size: 12))
                .foregroundStyle(Theme.text)
                .lineLimit(2)
            Spacer()
            Button(action: dismiss) {
                Image(systemName: "xmark")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(Theme.faint)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Theme.raised)
    }
}

/// Issue bodies are written by anyone who can open an issue, so only http and
/// https survive as links. Everything else is left as plain text rather than
/// rendered into something clickable.
enum Markdown {
    static func render(_ raw: String) -> AttributedString {
        var attributed: AttributedString
        do {
            attributed = try AttributedString(
                markdown: raw,
                options: AttributedString.MarkdownParsingOptions(
                    allowsExtendedAttributes: true,
                    interpretedSyntax: .inlineOnlyPreservingWhitespace,
                    failurePolicy: .returnPartiallyParsedIfPossible))
        } catch {
            return AttributedString(raw)
        }
        let unsafe = attributed.runs.compactMap { run -> Range<AttributedString.Index>? in
            guard let link = run.link else { return nil }
            let scheme = link.scheme?.lowercased()
            return (scheme == "http" || scheme == "https") ? nil : run.range
        }
        for range in unsafe { attributed[range].link = nil }
        return attributed
    }
}

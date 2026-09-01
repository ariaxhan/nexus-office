import AppKit
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

    /// Why what is on screen is not current, in one sentence. Never two, and
    /// never red: last-good data is data, and the desk is not broken.
    private var notice: String? {
        StateRules.staleNotice(station: station, github: store.github,
                               generated: store.worldGenerated)
    }

    /// "as of 5:42 PM", when the last successful pull is older than the
    /// snapshot this desk arrived in, or when something went wrong reading it.
    private var asOf: String? {
        StateRules.asOf(station: station, generated: store.worldGenerated)
    }

    /// Which half of the desk is on screen. On the store, so it survives going
    /// to look at something else and coming back.
    private var tab: DeskTab { store.tab(at: station.repo) }

    var body: some View {
        VStack(spacing: 0) {
            head
            Divider().overlay(Theme.hairline)
            switch tab {
            case .context:
                DeskContextView(store: store, repo: station.repo)
            case .feed:
                // This repo's own timeline: what its agents have been saying
                // while they worked in it. The same view as the global feed,
                // filtered, because two renderers that agree today disagree the
                // first time a kind is added.
                FeedView(store: store, repo: store.feedAccount(for: station.repo))
            case .work:
                work
            }
        }
        .background(Theme.ink)
    }

    /// What is open on GitHub: the gate, the agents, the issues, the PRs.
    private var work: some View {
        VStack(spacing: 0) {
            ScrollViewReader { scroll in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if let gate {
                        GateCard(store: store, gate: gate)
                    }
                    if let notice {
                        StaleNotice(text: notice)
                    }
                    // Above the issues on purpose. What is running in this
                    // folder right now is more urgent than what GitHub thought
                    // about it when the snapshot was built, and an agent that is
                    // blocked on a question is the most urgent thing on the desk
                    // after a gate.
                    SessionsView(store: store, repo: station.repo)
                        .padding(.bottom, 4)
                    ForEach(station.issues) { issue in
                        IssueCard(store: store, repo: station.repo, issue: issue)
                            .id(Self.cardID(issue: issue.number))
                    }
                    if !station.prs.isEmpty {
                        Text("pull requests")
                            .officeFont(size: 11, weight: .semibold)
                            .foregroundStyle(Theme.faint)
                            .padding(.top, 8)
                    }
                    ForEach(station.prs) { pr in
                        // "closes #213" is only a link if #213 is a place you
                        // can get to. The card knows which issues are on this
                        // desk, so the ones that are scroll to their card and
                        // the ones that are not stay plain rather than
                        // pretending to be a button that does nothing.
                        PullRequestCard(store: store, repo: station.repo, pr: pr,
                                        openIssues: Set(station.issues.map(\.number))) { number in
                            withAnimation {
                                scroll.scrollTo(Self.cardID(issue: number), anchor: .top)
                            }
                        }
                    }
                    if station.issues.isEmpty && station.prs.isEmpty
                        && notice == nil && gate == nil {
                        Text(station.detail.isEmpty ? "Nothing open here." : station.detail)
                            .officeFont(size: 12.5)
                            .foregroundStyle(Theme.faint)
                            .padding(.top, 20)
                        // A desk with nothing open used to be that one line and
                        // nothing else. The repo's own front page is what a
                        // person opens a quiet desk to read.
                        ReadmeBlock(store: store, repo: station.repo)
                    }
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 14)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollContentBackground(.hidden)
            }
            if let toast = store.toast {
                ToastBar(text: toast) { store.toast = nil }
            }
        }
    }

    /// Where an issue card sits, so a PR that closes it can point at it.
    static func cardID(issue number: Int) -> String { "issue-\(number)" }

    private var head: some View {
        HStack(spacing: 9) {
            StateDot(state: state, size: 22)
            VStack(alignment: .leading, spacing: 1) {
                Text(station.repo)
                    .officeFont(size: 13, weight: .medium)
                    .foregroundStyle(Theme.text)
                HStack(spacing: 5) {
                    Text(state.label).foregroundStyle(Theme.color(state))
                    if !station.detail.isEmpty {
                        Text(station.detail).foregroundStyle(Theme.faint)
                    }
                }
                .officeFont(size: 11)
                .lineLimit(1)
            }
            Spacer()
            // Two halves of one desk: what GitHub has open, and what the
            // checkout on this machine says about itself. A switch rather than
            // a second row in the roster, because it is the same desk.
            Picker("", selection: Binding(
                get: { tab },
                set: { store.show($0, at: station.repo) })) {
                ForEach(DeskTab.allCases, id: \.self) { choice in
                    Text(choice.label).tag(choice)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 156)
            .controlSize(.small)
            // The room's own blue rather than the system accent. Red and amber
            // mean "this needs you" everywhere else in here, and a navigation
            // control in the loudest colour on screen is a false alarm that
            // never goes away.
            .tint(Theme.blue)
            // The most recent thing we were able to pull, said where the clock
            // already is. Without it the desk shows an hours-old GitHub with
            // the confidence of a live one.
            if let asOf {
                Text(asOf)
                    .officeFont(size: 11)
                    .foregroundStyle(Theme.amber.opacity(0.85))
            } else {
                Text(StateRules.stamp(station.at))
                    .officeFont(size: 11)
                    .foregroundStyle(Theme.faint)
            }
        }
        .padding(.horizontal, 18)
        .frame(height: 44)
        .padding(.top, 8)
    }
}

/// The repo's README, when the desk has nothing else to say.
///
/// Read off this machine by the door rather than off GitHub, and asked for only
/// on a desk with nothing open on it: the office's GraphQL budget is spent on
/// what changed, and a front page is not that.
///
/// Held here rather than in `Store`, because it is wanted by exactly one view
/// in exactly one state and nothing else in the office reasons about it. The
/// `task(id:)` reloads when the selection moves to another desk.
struct ReadmeBlock: View {
    // Not `@Bindable`: this reads `store.api` and writes nothing back, and a
    // property wrapper that promises a write nobody makes is a lie about the
    // seam.
    let store: Store
    let repo: String

    @State private var readme: DeskReadme?
    @State private var failure: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let readme, readme.isOK {
                HStack(spacing: 8) {
                    Text(readme.name.isEmpty ? "readme" : readme.name.lowercased())
                        .officeFont(size: 11, weight: .semibold)
                        .foregroundStyle(Theme.faint)
                    if readme.clipped {
                        Text("first 64 KB")
                            .officeFont(size: 11)
                            .foregroundStyle(Theme.faint)
                    }
                }
                MarkdownText(raw: readme.text)
                    .frame(maxWidth: 720, alignment: .leading)
            } else if let sentence {
                // Why there is no front page, in the door's own words. Not red:
                // a repo that is not checked out here is not a broken repo.
                Text(sentence)
                    .officeFont(size: 12)
                    .foregroundStyle(Theme.faint)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 620, alignment: .leading)
            }
        }
        .padding(.top, 14)
        .task(id: repo) {
            readme = nil
            failure = nil
            do {
                readme = try await store.api.readme(repo: repo)
            } catch {
                failure = error.localizedDescription
            }
        }
    }

    /// The one sentence to print when there is no text. Nil while the read is
    /// still out, so a quiet desk does not flash a sentence it is about to
    /// replace.
    private var sentence: String? {
        if let failure { return "could not read this repo's README: \(failure)" }
        guard let readme else { return nil }
        return readme.detail.isEmpty ? nil : readme.detail
    }
}

// MARK: - what the desk says about itself

/// The Markdown a checkout keeps: its README, and everything under `_meta`.
///
/// An index down the left and an autosaving editor beside it. The server still
/// decides which listed file may be read or written; this view never builds a
/// filesystem path.
struct DeskContextView: View {
    @Bindable var store: Store
    let repo: String

    private var context: DeskContext? { store.context(at: repo) }

    var body: some View {
        HSplitView {
            index
                .frame(minWidth: 200, idealWidth: 260, maxWidth: 340)
            document
                .frame(minWidth: 320, maxWidth: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - the tree

    /// Folders a person has shut. Shut rather than open, so a checkout with a
    /// hundred folders comes up with every one of them open and a file two
    /// levels down is one click away rather than three.
    @State private var closed: Set<String> = []

    @ViewBuilder private var index: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 1) {
                if let context {
                    let recent = FileTree.recent(of: context.files,
                                                 now: Int(Date().timeIntervalSince1970))
                    if !recent.isEmpty {
                        Text("recent")
                            .officeFont(size: 10, weight: .semibold)
                            .foregroundStyle(Theme.faint)
                            .padding(.horizontal, 8)
                            .padding(.bottom, 2)
                        ForEach(recent) { file in
                            entry(file, depth: 0, open: context.path == file.path)
                        }
                        Divider()
                            .padding(.vertical, 6)
                    }
                    ForEach(FileTree.rows(of: context.files, closed: closed)) { row in
                        if row.isFolder {
                            folder(row)
                        } else if let file = row.file {
                            entry(file, depth: row.depth, open: context.path == file.path)
                        }
                    }
                    if context.capped {
                        // A truncated list presented as a whole one is the
                        // defect this project exists to prevent.
                        Text("this list was cut; the checkout has more")
                            .officeFont(size: 10.5)
                            .foregroundStyle(Theme.amber)
                            .padding(8)
                    }
                    if context.files.isEmpty {
                        Text("no Markdown in this checkout")
                            .officeFont(size: 11.5)
                            .foregroundStyle(Theme.faint)
                            .padding(10)
                    } else {
                        Text("\(context.files.count) files")
                            .officeFont(size: 10.5)
                            .foregroundStyle(Theme.faint)
                            .padding(.horizontal, 8)
                            .padding(.top, 10)
                    }
                }
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollContentBackground(.hidden)
        .background(Theme.well)
    }

    private func folder(_ row: FileTree.Row) -> some View {
        let shut = closed.contains(row.id)
        return Button {
            if shut { closed.remove(row.id) } else { closed.insert(row.id) }
        } label: {
            HStack(spacing: 5) {
                Image(systemName: shut ? "chevron.right" : "chevron.down")
                    .officeSymbol(size: 8, weight: .semibold)
                    .foregroundStyle(Theme.faint)
                    .frame(width: 10)
                Text(row.name)
                    .officeFont(size: 11.5, weight: .medium)
                    .foregroundStyle(Theme.dim)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 4)
                if shut {
                    Text("\(row.count)")
                        .officeFont(size: 10)
                        .foregroundStyle(Theme.faint)
                }
            }
            .padding(.leading, 8 + CGFloat(row.depth) * 14)
            .padding(.trailing, 8)
            .padding(.vertical, 4)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func entry(_ file: ContextFile, depth: Int, open: Bool) -> some View {
        HStack(spacing: 3) {
            Button {
                Task { await store.loadContext(repo: repo, path: file.path) }
            } label: {
                HStack(spacing: 5) {
                    Image(systemName: "doc.text")
                        .officeSymbol(size: 9)
                        .foregroundStyle(open ? Theme.blue : Theme.faint)
                        .frame(width: 10)
                    Text(file.name)
                        .officeFont(size: 12)
                        .foregroundStyle(open ? Theme.text : Theme.dim)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer(minLength: 4)
                }
                .padding(.leading, 8 + CGFloat(depth) * 14)
                .padding(.vertical, 4)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            Button {
                Task { await copy(file) }
            } label: {
                Image(systemName: "doc.on.doc")
                    .officeSymbol(size: 10)
                    .foregroundStyle(Theme.faint)
                    .padding(4)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("Copy \(file.name)")
            .accessibilityLabel("Copy \(file.name)")
            .padding(.trailing, 4)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 6, style: .continuous)
            .fill(open ? Theme.raised : Color.clear))
    }

    @MainActor private func copy(_ file: ContextFile) async {
        do {
            let text: String
            if let context, context.path == file.path {
                text = context.text
            } else {
                text = try await store.api.context(repo: repo, path: file.path).text
            }
            let board = NSPasteboard.general
            board.clearContents()
            guard board.setString(text, forType: .string) else {
                store.toast = "could not copy \(file.name)"
                return
            }
            store.toast = "copied \(file.name)"
        } catch {
            store.toast = "could not copy \(file.name): \(error.localizedDescription)"
        }
    }

    // MARK: - the document

    @ViewBuilder private var document: some View {
        VStack(alignment: .leading, spacing: 12) {
                if let said = store.contextError(at: repo) {
                    // The reason, in the server's own words. A desk the office
                    // cannot place must never draw as a desk with nothing in it.
                    Text(said)
                        .officeFont(size: 12.5)
                        .foregroundStyle(Theme.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let context, !context.path.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(context.title)
                            .officeFont(size: 13, weight: .medium)
                            .foregroundStyle(Theme.text)
                        Text(context.path)
                            .officeFont(size: 10.5, design: .monospaced)
                            .foregroundStyle(Theme.faint)
                            .textSelection(.enabled)
                    }
                    MarkdownEditor(store: store, repo: repo, path: context.path,
                                   source: context.text)
                        .id("\(repo):\(context.path)")
                } else if store.isLoadingContext(at: repo) {
                    Text("reading the checkout")
                        .officeFont(size: 12)
                        .foregroundStyle(Theme.faint)
                } else if store.contextError(at: repo) == nil {
                    Text("nothing to read here.")
                        .officeFont(size: 12.5)
                        .foregroundStyle(Theme.faint)
                }
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 16)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.ink)
        .onAppear {
            // Reached by clicking the switch OR by landing on a desk that was
            // already on Context, so the load lives here rather than only in
            // the button that flipped it.
            if store.context(at: repo) == nil && store.contextError(at: repo) == nil {
                store.showContext(at: repo)
            }
        }
    }

}

/// Plain Markdown source, saved after typing pauses. The last saved source is
/// sent back with every revision so a background edit cannot be overwritten.
private struct MarkdownEditor: View {
    @Bindable var store: Store
    let repo: String
    let path: String
    let source: String

    @State private var draft: String
    @State private var saved: String
    @State private var status = "saved"
    @State private var pendingSave: Task<Void, Never>?
    @State private var isSaving = false

    init(store: Store, repo: String, path: String, source: String) {
        self.store = store
        self.repo = repo
        self.path = path
        self.source = source
        _draft = State(initialValue: source)
        _saved = State(initialValue: source)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Spacer()
                Text(status)
                    .officeFont(size: 10.5)
                    .foregroundStyle(status == "saved" ? Theme.faint : Theme.amber)
            }
            TextEditor(text: $draft)
                .officeFont(size: 12.5, design: .monospaced)
                .foregroundStyle(Theme.dim)
                .scrollContentBackground(.hidden)
                .padding(.horizontal, -5)
                .frame(maxWidth: 760, maxHeight: .infinity, alignment: .topLeading)
                .onChange(of: draft) { _, _ in scheduleAutosave() }
        }
        .onChange(of: source) { _, newValue in
            guard draft == saved else { return }
            draft = newValue
            saved = newValue
        }
    }

    @MainActor private func scheduleAutosave() {
        guard draft != saved else { return }
        status = "waiting to save"
        // Never cancel a request already at the door: it may have committed
        // before URLSession reports cancellation. Queue the newer draft after
        // it instead, keeping each `expected` value in a strict sequence.
        guard !isSaving else { return }
        pendingSave?.cancel()
        pendingSave = Task { @MainActor in
            do {
                try await Task.sleep(for: .milliseconds(650))
                try Task.checkCancellation()
                await persistDraft()
            } catch is CancellationError {
                return
            } catch {
                status = error.localizedDescription
            }
        }
    }

    @MainActor private func persistDraft() async {
        guard !isSaving, draft != saved else { return }
        isSaving = true
        let candidate = draft
        let expected = saved
        status = "saving"
        do {
            _ = try await store.saveContext(repo: repo, path: path, text: candidate,
                                            expected: expected)
            saved = candidate
            status = draft == candidate ? "saved" : "waiting to save"
        } catch {
            status = error.localizedDescription
            store.toast = "could not save \(path): \(error.localizedDescription)"
        }
        isSaving = false
        if draft != saved { scheduleAutosave() }
    }
}

/// The one line about freshness.
///
/// Quiet on purpose. This used to be two red `exclamationmark.triangle` rows
/// carrying the same sentence twice, which read as two faults on a desk that
/// had one, and read as broken on a desk that was merely old. A rule down the
/// side and grey text says "this is not current" without saying "something is
/// wrong with this repo".
struct StaleNotice: View {
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            Rectangle()
                .fill(Theme.amber.opacity(0.45))
                .frame(width: 2)
            Text(text)
                .officeFont(size: 12)
                .foregroundStyle(Theme.dim)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, 2)
        .frame(maxWidth: 720, alignment: .leading)
    }
}

// MARK: - an issue

struct IssueCard: View {
    @Bindable var store: Store
    let repo: String
    let issue: Issue
    /// Drop the issue's own text and show the desk instead.
    ///
    /// The home stacks cards off twelve desks, where the question and its
    /// options are the whole point and the issue body is the part a person
    /// already read on the desk. Four essays push the merge button and the
    /// parks off the bottom of the window, which is the same as not drawing
    /// them. A desk pane, which is one repo at a time, still shows everything.
    var brief = false

    @State private var opened = false
    @State private var busy = false

    private var needsYou: Bool { StateRules.needsHuman(issue: issue) }

    /// The half-written comment, kept by the office rather than by this card.
    /// The card is torn down every time the selection changes; the sentence a
    /// person was in the middle of writing is not.
    private var key: String { Store.draftKey(repo: repo, issue: issue.number) }

    private var draft: Binding<String> {
        Binding(get: { store.drafts[key] ?? "" }, set: { store.drafts[key] = $0 })
    }

    /// Open because it was clicked open, or because there is something in it.
    /// A draft that survives the view has to survive it visibly.
    private var commenting: Bool { opened || !draft.wrappedValue.isEmpty }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("#\(issue.number)")
                    .officeFont(size: 12, design: .monospaced)
                    .foregroundStyle(Theme.faint)
                Text(issue.title)
                    .officeFont(size: 14, weight: .medium)
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                // Which desk, when the card is not standing on one.
                if brief {
                    Text(repo)
                        .officeFont(size: 11)
                        .foregroundStyle(Theme.faint)
                }
                Text(StateRules.stamp(issue.updatedAt))
                    .officeFont(size: 11)
                    .foregroundStyle(Theme.faint)
            }

            // Not on the home: every card there is waiting on you, so the pill
            // says nothing, and twelve desks' labels are twelve desks' noise.
            if !brief, needsYou || !issue.labels.isEmpty {
                HStack(spacing: 6) {
                    if needsYou {
                        // Not a label lookup. The bot had the last word, which is
                        // the same sentence the runner uses to decide.
                        HStack(spacing: 5) {
                            Circle().fill(Theme.red).frame(width: 6, height: 6)
                            Text("waiting on you").officeFont(size: 11, weight: .medium)
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

            if !issue.body.isEmpty && !brief {
                Text(Markdown.render(issue.body))
                    .officeFont(size: 12.5)
                    .foregroundStyle(Theme.dim)
                    .textSelection(.enabled)
                    .tint(Theme.blue)
                    .lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // The runner's last word, where the issue's own text is not being
            // drawn. On a card whose one button cannot be taken back, reading it
            // is most of deciding whether to press it.
            if brief, !issue.hasDecision, !issue.lastWord.isEmpty {
                Text(Markdown.render(issue.lastWord))
                    .officeFont(size: 12.5)
                    .foregroundStyle(Theme.dim)
                    .tint(Theme.blue)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let decision = issue.decision, !decision.options.isEmpty {
                DecisionOptions(decision: decision, busy: busy) { option in
                    apply("choose", n: option.n, label: option.label)
                }
            }

            HStack(spacing: 7) {
                CardButton(title: commenting ? "cancel" : "comment", busy: busy) {
                    if commenting {
                        opened = false
                        store.drafts[key] = nil
                    } else {
                        opened = true
                    }
                }
                // The fix is already written. The door re-reads the PR and
                // re-checks whose branch it is before it touches anything, so
                // this is a request and never a permission.
                if let landed = issue.landedPr {
                    CardButton(title: "merge PR #\(landed)", tint: Theme.green, busy: busy) {
                        apply("merge", pr: landed)
                    }
                }
                CardButton(title: "close", busy: busy) { apply("close") }
                CardButton(title: "reopen", busy: busy) { apply("reopen") }
                CardButton(title: "nudge", busy: busy) { apply("nudge") }
                Spacer()
                if let url = URL(string: issue.url), url.scheme == "https" {
                    Link("open on GitHub", destination: url)
                        .officeFont(size: 11)
                        .foregroundStyle(Theme.faint)
                }
            }

            if commenting {
                VStack(alignment: .leading, spacing: 7) {
                    TextField("Answer it. A reply without the bot's marker is what re-queues it.",
                              text: draft, axis: .vertical)
                        .textFieldStyle(.plain)
                        .officeFont(size: 12.5)
                        .foregroundStyle(Theme.text)
                        .lineLimit(2...8)
                        .padding(9)
                        .background(RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(Theme.well))
                    HStack {
                        Spacer()
                        CardButton(title: "send comment", tint: Theme.green, busy: busy) {
                            apply("comment", body: draft.wrappedValue)
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

    private func apply(_ kind: String, body: String? = nil,
                       pr: Int? = nil, n: Int? = nil, label: String? = nil) {
        if kind == "comment" && (body ?? "").trimmingCharacters(in: .whitespaces).isEmpty { return }
        busy = true
        Task {
            let sent = await store.decide(kind: kind, repo: repo,
                                          issue: String(issue.number), body: body,
                                          pr: pr, label: label, n: n)
            busy = false
            // The draft is only thrown away once the server has taken it. A
            // comment cleared by a refused write is a sentence a person has to
            // type twice, having already watched it disappear once.
            guard sent else { return }
            opened = false
            store.drafts[key] = nil
        }
    }
}

// MARK: - a pull request

struct PullRequestCard: View {
    @Bindable var store: Store
    let repo: String
    let pr: PullRequest
    /// The issue numbers that have a card on this desk right now.
    var openIssues: Set<Int> = []
    /// Take me to that issue's card.
    var jump: (Int) -> Void = { _ in }

    @State private var busy = false
    /// Whether the whole body is showing. Shut by default: a desk with nine
    /// PRs on it is a list, and a list where every row is an essay is a list
    /// nobody scrolls.
    @State private var expanded = false

    /// Whether there is anything behind the opening paragraph.
    private var hasMore: Bool { Markdown.blocks(pr.body).count > 1 }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("#\(pr.number)")
                    .officeFont(size: 12, design: .monospaced)
                    .foregroundStyle(Theme.faint)
                Text(pr.title)
                    .officeFont(size: 13.5, weight: .medium)
                    .foregroundStyle(Theme.text)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                Text(StateRules.stamp(pr.updatedAt))
                    .officeFont(size: 11)
                    .foregroundStyle(Theme.faint)
            }
            HStack(spacing: 6) {
                Pill(text: "\(pr.head) into \(pr.base)")
                if pr.draft { Pill(text: "draft", color: Theme.dim) }
                Pill(text: pr.mergeable.lowercased(),
                     color: pr.isMergeable ? Theme.green : Theme.amber)
            }

            // What the PR says about itself, whether written by the pipeline or
            // a person. Visibility is independent from merge authority.
            if !pr.body.isEmpty {
                MarkdownText(raw: pr.body, size: 12.5, color: Theme.dim,
                             limit: expanded ? nil : 1)
                    .lineSpacing(2)
            }

            // The rest of the body, what lands when this merges, and when it
            // was last touched: one row, because three rows of small print
            // above the merge button pushes the merge button off the screen,
            // and this card exists to get somebody to that button.
            //
            // The header's clock says "6:19 PM"; this says which day that was,
            // because a PR sitting open across a night is the ordinary case.
            HStack(spacing: 10) {
                if hasMore {
                    Button(expanded ? "less" : "more") {
                        withAnimation(.easeInOut(duration: 0.15)) { expanded.toggle() }
                    }
                    .buttonStyle(.plain)
                    .officeFont(size: 11, weight: .medium)
                    .foregroundStyle(Theme.blue)
                }
                ForEach(pr.closes, id: \.self) { number in
                    if openIssues.contains(number) {
                        Button("closes #\(number)") { jump(number) }
                            .buttonStyle(.plain)
                            .officeFont(size: 11)
                            .foregroundStyle(Theme.blue)
                    } else {
                        Text("closes #\(number)")
                            .officeFont(size: 11)
                            .foregroundStyle(Theme.faint)
                    }
                }
                if !StateRules.moment(pr.updatedAt).isEmpty {
                    Text("updated \(StateRules.moment(pr.updatedAt))")
                        .officeFont(size: 11)
                        .foregroundStyle(Theme.faint)
                }
                Spacer(minLength: 0)
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
                    Text(!pr.pipeline ? "view only; the Office did not open this PR"
                         : pr.draft ? "a draft is a statement that it is not ready"
                         : "GitHub has not said this can merge")
                        .officeFont(size: 11)
                        .foregroundStyle(Theme.faint)
                }
                Spacer()
                if let url = URL(string: pr.url), url.scheme == "https" {
                    Link("open on GitHub", destination: url)
                        .officeFont(size: 11)
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

/// A stated question, as buttons.
///
/// The question is drawn exactly as the runner wrote it: an agent's own words
/// paraphrased by the window are words nobody said. The recommended option is
/// first and says so, and every consequence is on screen, because an option
/// whose cost is behind a click is an option nobody read before they clicked
/// it. Full width and wrapping, so a long consequence is a taller button and
/// never a truncated one.
struct DecisionOptions: View {
    let decision: Decision
    var busy = false
    let choose: (DecisionOption) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(decision.question)
                .officeFont(size: 13, weight: .medium)
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
            ForEach(decision.ordered) { option in
                Button { choose(option) } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 7) {
                            Text("\(option.n). \(option.label)")
                                .officeFont(size: 12.5, weight: .semibold)
                                .foregroundStyle(option.recommended ? Theme.onFilled : Theme.text)
                                .fixedSize(horizontal: false, vertical: true)
                            if option.recommended {
                                Text("recommended")
                                    .officeFont(size: 10.5)
                                    .foregroundStyle(Theme.onFilled.opacity(0.7))
                            }
                        }
                        if !option.consequence.isEmpty {
                            Text(option.consequence)
                                .officeFont(size: 11.5)
                                .foregroundStyle(option.recommended
                                                 ? Theme.onFilled.opacity(0.75) : Theme.dim)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 11)
                    .padding(.vertical, 9)
                    .background(RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(option.recommended ? Theme.green : Theme.well))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(busy)
                .opacity(busy ? 0.5 : 1)
            }
        }
        .frame(maxWidth: 560, alignment: .leading)
    }
}

struct CardButton: View {
    let title: String
    var tint: Color = Theme.dim
    var busy = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(busy ? "working" : title)
                .officeFont(size: 11.5, weight: .medium)
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
                .officeFont(size: 12)
                .foregroundStyle(Theme.text)
                .lineLimit(2)
            Spacer()
            Button(action: dismiss) {
                Image(systemName: "xmark")
                    .officeSymbol(size: 10, weight: .semibold)
                    .foregroundStyle(Theme.faint)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Theme.raised)
    }
}

import SwiftUI

/// The feed. Every account is a repo, and agents write in it while they work.
///
/// Two views over one store: the global timeline, and one repo's. Both are this
/// file, because the only difference is a filter, and two renderers that agree
/// today disagree the first time a kind is added.
///
/// **There is no addressee and no inbox.** A post is published by an account,
/// not sent to a person. That is what makes it cheap enough to write to while
/// working, which is the whole reason it will have anything in it.
///
/// **Reading it authorizes nothing.** Agents post and read; an agent replying to
/// an agent is a note, permanently. The reply box in here is the only control in
/// the app that can grant anything, and only because the door has already
/// established it is Aria before it reaches the vault.
///
/// That last paragraph is the one thing the OpenAI/Hugging Face swarm got wrong.
/// It had accounts, threads, mailboxes, even signed messages. It had no person,
/// so the board became the authority, and an agent that had correctly refused an
/// action resumed it because a peer posted GO.
struct FeedView: View {
    @Bindable var store: Store
    /// Empty for the global timeline, an account for one repo's.
    var repo: String = ""

    private var feed: FeedResponse { store.feed(repo) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            head
            Rectangle().fill(Theme.hairline).frame(height: 0.5)
            if feed.posts.isEmpty {
                empty
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(feed.posts) { post in
                            PostRow(store: store, post: post, showAccount: repo.isEmpty)
                            Rectangle().fill(Theme.hairline).frame(height: 0.5)
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.ink)
        .task(id: repo) { await store.refreshFeed(repo) }
    }

    private var head: some View {
        HStack(spacing: 8) {
            Text(repo.isEmpty ? "the feed" : "@\(repo)")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(Theme.text)
            if feed.asking > 0 {
                Pill(text: "\(feed.asking) asking", color: Theme.amber)
            }
            if feed.blocked > 0 {
                Pill(text: "\(feed.blocked) blocked", color: Theme.red)
            }
            Spacer()
            // The count of what exists, next to the count of what is drawn, so a
            // capped timeline never reads as the whole of it.
            if feed.total > feed.posts.count {
                Text("\(feed.posts.count) of \(feed.total)")
                    .font(.system(size: 11)).foregroundStyle(Theme.faint)
            } else if feed.total > 0 {
                Text("\(feed.total)").font(.system(size: 11)).foregroundStyle(Theme.faint)
            }
            if repo.isEmpty && !feed.accounts.isEmpty {
                Text("\(feed.accounts.count) repos")
                    .font(.system(size: 11)).foregroundStyle(Theme.faint)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    /// Never a blank timeline. "Nothing has posted" and "the office cannot find
    /// the vault" are opposite facts, and the sentence comes from the response
    /// so the two can never be confused for one another.
    private var empty: some View {
        Text(feed.emptyLine)
            .font(.system(size: 12))
            .foregroundStyle(feed.state == "ok" ? Theme.faint : Theme.amber)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: 560, alignment: .leading)
            .padding(14)
    }
}

/// One post, and everything hanging off it.
struct PostRow: View {
    @Bindable var store: Store
    let post: Post
    var showAccount: Bool = true

    @State private var open = false
    @State private var draft = ""
    @State private var sending = false

    private var tone: Color {
        if post.unreadable { return Theme.red }
        switch post.kind {
        case .blocked: return Theme.red
        case .asking: return post.answered ? Theme.green : Theme.amber
        case .landed: return Theme.green
        case .found: return Theme.blue
        case .working, .note: return Theme.dim
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            byline
            Text(post.text)
                .font(.system(size: 13))
                .foregroundStyle(post.unreadable ? Theme.red : Theme.text)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: 640, alignment: .leading)
            if !post.contract.isEmpty {
                // Not a signature. The contract a lane ran under is the thing
                // worth reading, and unlike an identity claim it is checkable
                // against the dispatch that produced it.
                Text(post.contract)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(Theme.faint)
                    .lineLimit(open ? nil : 1)
                    .frame(maxWidth: 640, alignment: .leading)
            }
            if open && !post.body.isEmpty {
                Text(post.body)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(Theme.dim)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: 640, alignment: .leading)
                    .padding(8)
                    .background(RoundedRectangle(cornerRadius: 7).fill(Theme.well))
            }
            if !post.replies.isEmpty { thread }
            if open { replyBox }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(post.wantsYou ? Theme.amber.opacity(0.06) : Color.clear)
        .contentShape(Rectangle())
        .onTapGesture { open.toggle() }
    }

    private var byline: some View {
        HStack(spacing: 7) {
            if showAccount {
                Text("@\(post.account)")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.text)
            }
            if !post.kind.label.isEmpty {
                Pill(text: post.kind.label, color: tone)
            }
            // Who typed it, beside the account and never instead of it: the repo
            // is the identity, the lane is a detail.
            if !post.by.isEmpty {
                Text(post.by).font(.system(size: 11)).foregroundStyle(Theme.faint)
            }
            Spacer()
            if post.answered {
                Pill(text: "answered", color: Theme.green)
            }
            Text(post.age).font(.system(size: 11)).foregroundStyle(Theme.faint)
        }
    }

    private var thread: some View {
        VStack(alignment: .leading, spacing: 5) {
            ForEach(post.replies) { reply in
                HStack(alignment: .top, spacing: 7) {
                    Rectangle().fill(reply.authorizes ? Theme.green : Theme.hairline)
                        .frame(width: 2)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text("@\(reply.account)")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(Theme.dim)
                            // The whole authorization model, drawn: a reply from
                            // the person decided something, a reply from a lane
                            // said something. Never the same pill.
                            if reply.authorizes {
                                Pill(text: "decided", color: Theme.green)
                            }
                            Text(reply.age).font(.system(size: 10))
                                .foregroundStyle(Theme.faint)
                        }
                        Text(reply.text).font(.system(size: 12))
                            .foregroundStyle(Theme.dim)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: 620, alignment: .leading)
            }
        }
        .padding(.leading, 4)
    }

    private var replyBox: some View {
        HStack(spacing: 7) {
            TextField(post.gateId.isEmpty ? "reply" : "reply, and unblock the agent",
                      text: $draft)
                .textFieldStyle(.plain)
                .font(.system(size: 12))
                .padding(.horizontal, 9).padding(.vertical, 6)
                .background(RoundedRectangle(cornerRadius: 7).fill(Theme.well))
                .onSubmit { send() }
            Button("send") { send() }
                .buttonStyle(.plain)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(draft.isEmpty ? Theme.faint : Theme.blue)
                .disabled(draft.isEmpty || sending)
        }
        .frame(maxWidth: 640, alignment: .leading)
    }

    private func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !sending else { return }
        sending = true
        draft = ""
        Task {
            await store.replyToPost(post.id, text: text)
            sending = false
        }
    }
}

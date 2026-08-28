import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// A conversation with one bot.
///
/// A turn is a whole agent run, thirty seconds to two minutes, so a sent message
/// appears at once and the room learns the reply landed by watching the roster
/// change under it. The working indicator is the honest picture of that wait.
struct BotThreadView: View {
    @Bindable var store: Store
    let bot: Bot

    @FocusState private var composing: Bool
    /// Whether something is being dragged over the composer right now. Purely
    /// so the drop target says it is one before the fingers let go.
    @State private var dropping = false
    /// The local key monitor that turns Cmd-V of a picture into an attachment.
    /// Kept so it can be taken down again: a monitor that outlives its view is
    /// a keystroke going somewhere nobody is looking.
    @State private var pasteWatch: Any?

    /// Where the bottom of the transcript is, and how tall the window onto it
    /// is, both in the scroll view's own coordinates. Their difference is how
    /// far from the newest turn a person is standing.
    @State private var contentBottom: Double = 0
    @State private var viewport: Double = 0
    /// How many turns landed while she was reading something further up.
    @State private var unread = 0

    private var turns: [ChatTurn] { store.chats[bot.id] ?? [] }
    private var color: Color { bot.color.isEmpty ? .derived(from: bot.id) : Color(hex: bot.color) }

    /// The message being written, kept by the office.
    ///
    /// This was `@State`, so switching to another name and back threw it away:
    /// two sentences in, click Inbox to check something, come back to an empty
    /// box. A half-written message is work, and the view it happens to be
    /// displayed in is not where work should live.
    private var key: String { Store.draftKey(bot: bot.id) }

    private var draft: Binding<String> {
        Binding(get: { store.drafts[key] ?? "" }, set: { store.drafts[key] = $0 })
    }

    private var typed: String {
        draft.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    var body: some View {
        VStack(spacing: 0) {
            head
            Divider().overlay(Theme.hairline)
            transcript
            // A question that moved on takes its card with it, so what happened
            // to the answer has to survive the card. Without this the whole
            // reply to a click in here is a gate quietly vanishing.
            if let toast = store.toast {
                ToastBar(text: toast) { store.toast = nil }
            }
            composer
        }
        .background(Theme.ink)
    }

    // MARK: - head

    private var head: some View {
        HStack(spacing: 9) {
            BotAvatar(color: color, size: 22)
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 7) {
                    Text(bot.name)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Theme.text)
                    if bot.busy {
                        Text("working")
                            .font(.system(size: 11))
                            .foregroundStyle(Theme.blue)
                    }
                }
                // What to ask this one for. The paragraph of identity behind it
                // stays in `_meta/bots.json`: a person picking who to message
                // needs the job, not the briefing.
                if !bot.purpose.isEmpty {
                    Text(bot.purpose)
                        .font(.system(size: 11))
                        .foregroundStyle(Theme.faint)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            Spacer()
            if !store.runtimeUp {
                Text("the harness is not running")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.faint)
            }
        }
        .padding(.horizontal, 18)
        .frame(height: 44)
        .padding(.top, 8)
    }

    // MARK: - transcript

    /// How far from the newest turn the thread is scrolled, in points. Zero
    /// when it is sitting on the bottom.
    private var distance: Double { max(0, contentBottom - viewport) }

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    if turns.isEmpty && !store.gateBelongsTo(bot: bot.id) {
                        // An empty thread is the one place a person is actually
                        // asking what this bot is for, so it answers that and
                        // then says how to start.
                        VStack(alignment: .leading, spacing: 6) {
                            if !bot.purpose.isEmpty {
                                Text(bot.purpose)
                                    .font(.system(size: 13))
                                    .foregroundStyle(Theme.dim)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            Text("Message \(bot.name) to start.")
                                .font(.system(size: 12.5))
                                .foregroundStyle(Theme.faint)
                        }
                        .frame(maxWidth: 520, alignment: .leading)
                        .padding(.top, 24)
                    }
                    ForEach(Array(turns.enumerated()), id: \.offset) { index, turn in
                        // The row is still drawn by position — that is only
                        // where it sits. The mark on it is keyed by what the
                        // turn says, so a turn arriving above this one moves the
                        // bubble and never the mark. See `Reactions.key`.
                        Bubble(turn: turn, color: color, thread: bot.id,
                               reactions: Reactions.shared).id(index)
                    }
                    // The gate lands in the thread of the bot that asked, because
                    // that is where a person is already looking. The sheet is the
                    // interruption; this is the record.
                    // This bot's own question, which is not always the oldest
                    // one on the floor. A bot second in the queue still draws
                    // its raised hand here, because a thread that says nothing
                    // is happening while its bot waits is the hand going
                    // missing.
                    if let raised = store.gate(for: bot.id) {
                        GateCard(store: store, gate: raised)
                            .id("gate")
                    }
                    if let error = bot.error {
                        Label(error, systemImage: "exclamationmark.triangle.fill")
                            .font(.system(size: 12))
                            .foregroundStyle(Theme.red)
                            .padding(.vertical, 4)
                            .id("error")
                    }
                    if bot.busy {
                        WorkingBubble().id("working")
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 14)
                .frame(maxWidth: .infinity, alignment: .leading)
                // Where the bottom of everything sits, measured against the
                // window onto it. `onScrollGeometryChange` would say this in one
                // line and is macOS 15; this is the same fact, read the way a
                // macOS 14 app has to read it.
                .background(
                    GeometryReader { inner in
                        Color.clear.preference(key: ThreadBottom.self,
                                               value: inner.frame(in: .named(Self.threadSpace)).maxY)
                    }
                )
            }
            .scrollContentBackground(.hidden)
            .coordinateSpace(name: Self.threadSpace)
            .background(
                GeometryReader { outer in
                    Color.clear.preference(key: ThreadViewport.self, value: outer.size.height)
                }
            )
            .onPreferenceChange(ThreadBottom.self) { contentBottom = $0 }
            .onPreferenceChange(ThreadViewport.self) { viewport = $0 }
            // Only ever does anything under `--demo`: the real door returns no
            // seeds, and a store with a mark already in it refuses them. This is
            // what lets a framing photograph a reaction at all.
            .onChange(of: turns.count, initial: true) {
                let seeds = store.api.reactionSeeds()
                guard let rows = seeds[bot.id] else { return }
                Reactions.shared.seed(fixture: rows, thread: bot.id, turns: turns)
            }
            // A reply is only allowed to move the screen when the screen is
            // already on the newest turn, or when the newest turn is hers. A
            // person four replies back reading why something was refused, and
            // an agent finishing a two minute run under her, must not be the
            // same event: yanking her to the bottom loses the sentence she was
            // halfway through and reads as the app losing her place.
            .onChange(of: turns.count) { was, now in
                guard now > was else {
                    unread = 0
                    return
                }
                if turns.last?.isUser == true
                    || StateRules.shouldFollow(distanceFromBottom: distance) {
                    unread = 0
                    withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
                } else {
                    unread += now - was
                }
            }
            // Scrolling back down by hand is the same answer as tapping the
            // pill, so the pill goes when she arrives rather than waiting to
            // be dismissed.
            .onChange(of: distance) { _, now in
                if StateRules.shouldFollow(distanceFromBottom: now) { unread = 0 }
            }
            // Another name is another thread. Nothing about this one carries.
            .onChange(of: bot.id) { _, _ in
                unread = 0
                proxy.scrollTo("bottom", anchor: .bottom)
            }
            .onAppear { proxy.scrollTo("bottom", anchor: .bottom) }
            .overlay(alignment: .bottom) {
                if let line = StateRules.newRepliesLine(unread) {
                    NewRepliesPill(text: line) {
                        unread = 0
                        withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
                    }
                    .padding(.bottom, 10)
                }
            }
        }
    }

    private static let threadSpace = "thread"

    // MARK: - composer

    private var composer: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Above the text field rather than inside it, because a picture is
            // not a word: it has a name, a size and a way out, and all three
            // have to be legible before the send that cannot be taken back.
            if let picked = store.pendingAttachments[key] {
                AttachmentChip(picked: picked) { store.pendingAttachments[key] = nil }
            }
            HStack(spacing: 8) {
                Button(action: pick) {
                    Image(systemName: "paperclip")
                        .font(.system(size: 15))
                        .foregroundStyle(Theme.faint)
                        .frame(width: 26, height: 26)
                }
                .buttonStyle(.plain)
                .help("Attach a picture. You can also drag one here, or paste one.")

                TextField("Message \(bot.name)", text: draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(.system(size: 13))
                    .foregroundStyle(Theme.text)
                    // Six lines and then it scrolls inside itself. A composer
                    // that keeps growing eats the transcript it is a reply to.
                    .lineLimit(1...6)
                    .focused($composing)
                    .onSubmit(send)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(
                        RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.raised)
                    )
                Button(action: send) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 22))
                        .foregroundStyle(canSend ? color : Theme.faint)
                }
                .buttonStyle(.plain)
                .keyboardShortcut(.return, modifiers: [])
                .disabled(!canSend)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(dropping ? color.opacity(0.55) : .clear, lineWidth: 1.5)
                .padding(.horizontal, 8)
        )
        // A file from the Finder, or the pixels themselves from anywhere that
        // will hand them over. Both end in the same place as the open panel.
        .onDrop(of: [.fileURL, .image], isTargeted: $dropping, perform: take)
        .onAppear(perform: watchForPastedPictures)
        .onDisappear(perform: stopWatchingForPastedPictures)
    }

    /// A picture on its own is a whole thing to say, so the button is live with
    /// one even when nothing was typed.
    private var canSend: Bool { !typed.isEmpty || store.pendingAttachments[key] != nil }

    /// Clearing on send stays, and it lives in the store with the draft: the
    /// box empties on the same line that puts the message in the transcript, so
    /// what a person sees is the sentence moving rather than disappearing.
    private func send() {
        let message = draft.wrappedValue
        let picked = store.pendingAttachments[key]
        guard !typed.isEmpty || picked != nil else { return }
        Task { await store.send(to: bot.id, message: message, attachment: picked) }
    }

    // MARK: - getting a picture in

    /// The open panel, offering exactly the formats `Attachment` can read. HEIC
    /// and TIFF and GIF are on the list even though the door refuses all three:
    /// they are what a photo library is full of, and turning them into a JPEG is
    /// this app's job rather than the person's.
    private func pick() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = Attachment.readableTypes
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.message = "One picture, sent with this message."
        panel.prompt = "Attach"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        attach(try? Data(contentsOf: url), named: url.lastPathComponent)
    }

    private func take(_ providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first else { return false }
        if provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
            _ = provider.loadObject(ofClass: URL.self) { url, _ in
                guard let url else { return }
                let data = try? Data(contentsOf: url)
                Task { @MainActor in attach(data, named: url.lastPathComponent) }
            }
            return true
        }
        provider.loadDataRepresentation(forTypeIdentifier: UTType.image.identifier) { data, _ in
            Task { @MainActor in attach(data, named: "dropped") }
        }
        return true
    }

    /// Cmd-V, but only when there is actually a picture on the clipboard.
    ///
    /// A plain `keyboardShortcut` here would have eaten every paste in the
    /// composer, which is the sort of fix that trades a missing feature for a
    /// broken one. The monitor looks at the clipboard first and hands the event
    /// straight back when what is on it is text, so pasting a sentence still
    /// pastes a sentence.
    private func watchForPastedPictures() {
        guard pasteWatch == nil else { return }
        pasteWatch = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            guard event.modifierFlags.contains(.command),
                  event.charactersIgnoringModifiers?.lowercased() == "v",
                  let (data, name) = pictureOnTheClipboard()
            else { return event }
            attach(data, named: name)
            return nil
        }
    }

    private func stopWatchingForPastedPictures() {
        if let pasteWatch { NSEvent.removeMonitor(pasteWatch) }
        pasteWatch = nil
    }

    private func pictureOnTheClipboard() -> (Data, String)? {
        let board = NSPasteboard.general
        // Text wins, always. Anything that can be read as a string is a paste
        // the text field was expecting.
        if board.canReadObject(forClasses: [NSString.self], options: nil) {
            if let urls = board.readObjects(forClasses: [NSURL.self], options: nil) as? [URL],
               let url = urls.first,
               let type = UTType(filenameExtension: url.pathExtension),
               Attachment.readableTypes.contains(where: { type.conforms(to: $0) }),
               let data = try? Data(contentsOf: url) {
                return (data, url.lastPathComponent)
            }
            return nil
        }
        for type in [NSPasteboard.PasteboardType.png,
                     NSPasteboard.PasteboardType(UTType.jpeg.identifier),
                     NSPasteboard.PasteboardType(UTType.heic.identifier),
                     NSPasteboard.PasteboardType.tiff] {
            if let data = board.data(forType: type), !data.isEmpty {
                return (data, "pasted")
            }
        }
        return nil
    }

    /// One picture at a time: a second one replaces the first rather than
    /// queueing behind it, because the door takes exactly one and a queue that
    /// can only ever be one deep is a lie about what is going to be sent.
    @MainActor
    private func attach(_ data: Data?, named name: String) {
        guard let data, !data.isEmpty else {
            store.toast = "that file could not be read"
            return
        }
        let typed = draft.wrappedValue.utf8.count
        guard let ready = Attachment.prepare(imageData: data, name: name, messageBytes: typed) else {
            store.toast = "that is not a picture this can send"
            return
        }
        store.pendingAttachments[key] = ready
    }
}

/// The picture that is about to be sent, said out loud before it is.
///
/// The size is the size *after* the downscale, because the number a person needs
/// is the one that is going to leave the machine, not the one on disk. Saying
/// "4.1 MB" next to something that will be sent as 184 KB is a true fact about
/// the wrong object.
struct AttachmentChip: View {
    let picked: PreparedImage
    let remove: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "photo")
                .font(.system(size: 12))
                .foregroundStyle(Theme.dim)
            Text(picked.name)
                .font(.system(size: 12))
                .foregroundStyle(Theme.text)
                .lineLimit(1)
                .truncationMode(.middle)
            Text("\(picked.readable) after downscale")
                .font(.system(size: 11))
                .foregroundStyle(Theme.faint)
            Button(action: remove) {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 12))
                    .foregroundStyle(Theme.faint)
            }
            .buttonStyle(.plain)
            .help("Send without it")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(
            RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Theme.raised)
        )
        .frame(maxWidth: 420, alignment: .leading)
    }
}

// MARK: - pieces

/// How far down the transcript sits, and how big the window onto it is.
///
/// Two numbers rather than one because neither means anything alone: the bottom
/// of the content is only "off screen" relative to a viewport, and the viewport
/// changes when the composer grows.
private struct ThreadBottom: PreferenceKey {
    static let defaultValue: Double = 0
    static func reduce(value: inout Double, nextValue: () -> Double) { value = nextValue() }
}

private struct ThreadViewport: PreferenceKey {
    static let defaultValue: Double = 0
    static func reduce(value: inout Double, nextValue: () -> Double) { value = nextValue() }
}

/// A reply landed behind you. Small, above the composer, and the only thing on
/// this screen that moves the transcript without being asked twice.
struct NewRepliesPill: View {
    let text: String
    let go: () -> Void

    var body: some View {
        Button(action: go) {
            HStack(spacing: 6) {
                Image(systemName: "arrow.down")
                    .font(.system(size: 9, weight: .semibold))
                Text(text)
                    .font(.system(size: 11.5, weight: .medium))
            }
            .foregroundStyle(Theme.text)
            .padding(.horizontal, 11)
            .padding(.vertical, 6)
            .background(
                Capsule()
                    .fill(Theme.selected)
                    .overlay(Capsule().strokeBorder(Theme.hairline, lineWidth: 1))
            )
        }
        .buttonStyle(.plain)
    }
}

struct Bubble: View {
    let turn: ChatTurn
    let color: Color
    /// Which conversation this turn is in, so a mark on it belongs to one
    /// thread. Defaults to nothing, which is the same as unreactable: a bubble
    /// drawn somewhere that has no thread to name cannot carry a mark, rather
    /// than sharing one with every other nameless bubble.
    var thread: String = ""
    /// Passed in rather than reached for, so this view has no opinion about
    /// where the marks live and the one that owns them is chosen at the call
    /// site. Nothing to react to when it is absent.
    var reactions: Reactions?

    var body: some View {
        HStack {
            if turn.isUser { Spacer(minLength: 60) }
            VStack(alignment: turn.isUser ? .trailing : .leading, spacing: 3) {
                if !turn.content.isEmpty || !turn.hasPhoto {
                    if let reactions, !thread.isEmpty {
                        ReactionMenu(reactions: reactions, thread: thread, turn: turn) {
                            bubble
                        }
                        // Under the bubble rather than overlapping its corner:
                        // an overlay would sit on top of the last line of a one
                        // line reply, and the mark is worth less than the words.
                        ReactionBadge(reactions: reactions, thread: thread,
                                      turn: turn, color: color)
                            .padding(.horizontal, 4)
                    } else {
                        bubble
                    }
                }
                // The picture itself is gone: the office carried the bytes and
                // never wrote them down, so this says a photo went and stops
                // there rather than offering to show one it cannot produce.
                if turn.hasPhoto {
                    Label("with a photo", systemImage: "photo")
                        .font(.system(size: 11))
                        .foregroundStyle(Theme.faint)
                        .padding(.horizontal, 4)
                }
            }
            .frame(maxWidth: 560, alignment: turn.isUser ? .trailing : .leading)
            if !turn.isUser { Spacer(minLength: 60) }
        }
    }

    /// What was said, in its rounded box. Named because it is drawn either bare
    /// or inside the reaction menu, and writing it twice is how the two drift.
    private var bubble: some View {
        said
            .padding(.horizontal, 13)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 15, style: .continuous)
                    .fill(turn.isUser ? color.opacity(0.28) : Theme.raised)
            )
    }

    /// A bot writes markdown; a person writes a sentence.
    ///
    /// Aria's own turns stay plain on purpose. What she typed is what she meant,
    /// including the asterisks, and a composer that quietly reinterprets her
    /// punctuation on the way into the transcript is a composer she cannot
    /// predict. A reply is the other way round: an agent writes lists and
    /// fenced commands because that is how the answer is shaped.
    @ViewBuilder private var said: some View {
        if turn.isUser {
            Text(turn.content)
                .font(.system(size: 13))
                .foregroundStyle(Theme.text)
                .textSelection(.enabled)
        } else {
            MarkdownText(raw: turn.content, size: 13)
        }
    }
}

/// Three dots that actually move. A static "working" label is indistinguishable
/// from a frozen app, and a two minute turn gives it plenty of time to look like one.
struct WorkingBubble: View {
    @State private var phase = 0.0

    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(Theme.dim)
                    .frame(width: 5, height: 5)
                    .opacity(0.35 + 0.65 * abs(sin(phase + Double(index) * 0.7)))
            }
        }
        .padding(.horizontal, 13)
        .padding(.vertical, 11)
        .background(RoundedRectangle(cornerRadius: 15, style: .continuous).fill(Theme.raised))
        .onAppear {
            withAnimation(.linear(duration: 1.2).repeatForever(autoreverses: false)) {
                phase = .pi * 2
            }
        }
    }
}

/// The raised hand, in the thread. Same three properties as the sheet: the
/// target verbatim, the clock running, and an answer that carries the id.
///
/// The card is handed the gate it draws and answers that gate by its own id,
/// which is what makes the id on the buttons and the text above them the same
/// question. It never reaches for whatever gate is live at the moment of the
/// click, and there is nowhere else in this view to get one from.
struct GateCard: View {
    @Bindable var store: Store
    let gate: Gate

    private var notice: String? { store.gateNotice }

    private func answer(_ verdict: String, _ always: Bool) {
        Task { await store.answerGate(id: gate.id, answer: verdict, always: always) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 7) {
                GateMark(size: 11)
                Text("asking permission")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Theme.amber)
                Spacer()
                if let waiting = gate.waitingS {
                    Text("waiting \(StateRules.waited(seconds: waiting))")
                        .font(.system(size: 11))
                        .foregroundStyle(Theme.faint)
                }
            }
            if !gate.permission.isEmpty {
                Text(gate.permission)
                    .font(.system(size: 12.5, weight: .medium))
                    .foregroundStyle(Theme.text)
            }
            Text(gate.target)
                .font(.system(size: 12.5, design: .monospaced))
                .foregroundStyle(Theme.text)
                .textSelection(.enabled)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Theme.well))
            if !gate.detail.isEmpty {
                Text(gate.detail)
                    .font(.system(size: 12))
                    .foregroundStyle(Theme.dim)
            }
            HStack(spacing: 8) {
                GateButton(title: "Deny", tint: Theme.red) { answer("deny", false) }
                GateButton(title: "Allow once", tint: Theme.green) { answer("allow", false) }
                GateButton(title: "Allow always", tint: Theme.dim) { answer("allow", true) }
            }
            if let notice {
                Text(notice)
                    .font(.system(size: 11.5))
                    .foregroundStyle(Theme.faint)
            }
        }
        .padding(13)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Theme.amber.opacity(0.07))
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .strokeBorder(Theme.amber.opacity(0.35), lineWidth: 1)
                )
        )
        .frame(maxWidth: 620, alignment: .leading)
    }
}

struct GateButton: View {
    let title: String
    let tint: Color
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(tint)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(
                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                        .fill(tint.opacity(0.14))
                )
        }
        .buttonStyle(.plain)
    }
}

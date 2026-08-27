import SwiftUI

/// Markdown, drawn.
///
/// Two jobs, and they are not the same job.
///
/// `render` is one attributed run for a place that has room for exactly one:
/// an issue body under a title, where bold and links matter and a fenced block
/// would be a paragraph with backticks in it either way. It parses inline only,
/// which keeps the line breaks the author typed.
///
/// `blocks` is the real thing, for a bot's reply. `Text` renders inline
/// styling out of an `AttributedString` and ignores every block intent in it,
/// so a `.full` parse handed straight to one `Text` comes out as every
/// paragraph welded together with the list markers gone and the code
/// indistinguishable from the prose. The parse still knows: each run carries a
/// `presentationIntent` saying which paragraph, list item, heading or code
/// block it came out of. Grouping the runs by that intent and drawing one view
/// per group is what turns a parse into a page.
///
/// `.inlineOnlyPreservingWhitespace` was measured against this and is not an
/// alternative for a reply: on a fenced block it emits `swift let x = 1` on one
/// line, having eaten the newlines and promoted the language hint to prose.
///
/// Bodies are written by anyone who can open an issue or run an agent, so only
/// http and https survive as links. Everything else is left as plain text
/// rather than rendered into something clickable.
enum Markdown {

    // MARK: - one run

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
        return safe(attributed)
    }

    /// Only http and https keep their link. A `javascript:` or a `file:` in a
    /// body somebody else wrote is text, not a button.
    private static func safe(_ attributed: AttributedString) -> AttributedString {
        var out = attributed
        let unsafe = out.runs.compactMap { run -> Range<AttributedString.Index>? in
            guard let link = run.link else { return nil }
            let scheme = link.scheme?.lowercased()
            return (scheme == "http" || scheme == "https") ? nil : run.range
        }
        for range in unsafe { out[range].link = nil }
        return out
    }

    // MARK: - a page

    /// One thing on a line of its own.
    struct Block: Identifiable {
        enum Kind: Equatable {
            case paragraph
            case heading(Int)
            /// The marker is worked out here rather than in the view, because
            /// whether a list is numbered is a fact about the document.
            case item(String)
            case code(String?)
            case quote
        }

        let id: Int
        let kind: Kind
        /// The words, with their inline styling. Empty for a code block, whose
        /// text is deliberately not attributed.
        let text: AttributedString
        /// The code, verbatim, newlines and all.
        let code: String
    }

    /// The reply, as a list of things to draw.
    ///
    /// A parse that fails comes back as one plain paragraph carrying every
    /// character of the original. Never nothing: a reply that will not parse is
    /// still a reply, and a bot whose answer renders as an empty bubble is
    /// worse than one whose asterisks show.
    static func blocks(_ raw: String) -> [Block] {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return [] }

        let parsed: AttributedString
        do {
            parsed = try AttributedString(
                markdown: raw,
                options: AttributedString.MarkdownParsingOptions(
                    allowsExtendedAttributes: true,
                    interpretedSyntax: .full,
                    failurePolicy: .returnPartiallyParsedIfPossible))
        } catch {
            return [Block(id: 0, kind: .paragraph,
                          text: AttributedString(raw), code: "")]
        }

        let attributed = safe(parsed)
        var out: [Block] = []
        // The identity of the innermost intent: every run of one paragraph
        // shares it, and it changes exactly when a new block starts.
        var openIdentity: Int?

        for run in attributed.runs {
            let piece = AttributedString(attributed[run.range])
            let intent = run.presentationIntent
            let identity = intent?.components.first?.identity ?? -1

            if identity == openIdentity, var last = out.popLast() {
                if case .code = last.kind {
                    last = Block(id: last.id, kind: last.kind, text: last.text,
                                 code: last.code + String(piece.characters))
                } else {
                    last = Block(id: last.id, kind: last.kind, text: last.text + piece,
                                 code: last.code)
                }
                out.append(last)
                continue
            }

            openIdentity = identity
            let kind = kind(of: intent)
            if case .code = kind {
                out.append(Block(id: out.count, kind: kind, text: AttributedString(),
                                 code: String(piece.characters)))
            } else {
                out.append(Block(id: out.count, kind: kind, text: piece, code: ""))
            }
        }

        // A parse that produced nothing readable still has to say what the bot
        // said, so the raw text is the floor rather than an empty bubble.
        if out.isEmpty {
            return [Block(id: 0, kind: .paragraph, text: AttributedString(raw), code: "")]
        }
        return out
    }

    /// What kind of line this is, read off the intent stack.
    ///
    /// The stack runs innermost first: a bullet's runs carry paragraph, then
    /// listItem, then unorderedList. A code block and a heading are the whole
    /// stack on their own. Anything unrecognised is a paragraph, because a
    /// table nobody drew is still words somebody wrote.
    private static func kind(of intent: PresentationIntent?) -> Block.Kind {
        guard let intent else { return .paragraph }
        var ordinal: Int?
        var quoted = false
        for component in intent.components {
            switch component.kind {
            case .codeBlock(let hint):
                return .code(hint)
            case .header(let level):
                return .heading(level)
            case .listItem(let n):
                ordinal = n
            case .orderedList:
                if let n = ordinal { return .item("\(n).") }
            case .unorderedList:
                return .item("\u{00b7}")
            case .blockQuote:
                quoted = true
            default:
                continue
            }
        }
        return quoted ? .quote : .paragraph
    }
}

/// A reply, drawn as a page rather than as a wall of characters.
///
/// One view per block, so a list is a list, a fenced block is a panel of
/// monospace on the recessed surface, and two paragraphs have air between them.
/// The bubble that holds this is the same bubble as before; what is inside it
/// is no longer a single `Text`.
struct MarkdownText: View {
    let raw: String
    var size: Double = 13
    var color: Color = Theme.text
    /// How many blocks to draw. `nil` is all of them. A card that shows the
    /// opening paragraph and offers the rest passes 1, which is a real first
    /// paragraph rather than a fixed height with the second one sliced through
    /// the middle of a word.
    var limit: Int?

    private var blocks: [Markdown.Block] {
        let all = Markdown.blocks(raw)
        guard let limit else { return all }
        return Array(all.prefix(limit))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            ForEach(blocks) { block in
                draw(block)
            }
        }
        .tint(Theme.blue)
        .textSelection(.enabled)
    }

    @ViewBuilder private func draw(_ block: Markdown.Block) -> some View {
        switch block.kind {
        case .paragraph:
            Text(block.text)
                .font(.system(size: size))
                .foregroundStyle(color)
                .fixedSize(horizontal: false, vertical: true)

        case .heading(let level):
            Text(block.text)
                .font(.system(size: size + (level <= 1 ? 2 : 1), weight: .semibold))
                .foregroundStyle(color)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 2)

        case .item(let marker):
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Text(marker)
                    .font(.system(size: size))
                    .foregroundStyle(Theme.faint)
                    // A hanging indent: the marker column is fixed so the
                    // sentences line up down one edge rather than each one
                    // starting wherever its bullet happened to end.
                    .frame(minWidth: 12, alignment: .trailing)
                Text(block.text)
                    .font(.system(size: size))
                    .foregroundStyle(color)
                    .fixedSize(horizontal: false, vertical: true)
            }

        case .code:
            // Sideways rather than wrapped. A wrapped line of code is a line
            // that reads as two lines of code, which is how a person
            // mis-copies a command.
            ScrollView(.horizontal, showsIndicators: false) {
                Text(block.code.trimmingCharacters(in: .newlines))
                    .font(.system(size: size - 1, design: .monospaced))
                    .foregroundStyle(color)
                    .textSelection(.enabled)
                    .padding(9)
            }
            .background(
                RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Theme.well)
            )

        case .quote:
            HStack(alignment: .top, spacing: 8) {
                Rectangle()
                    .fill(Theme.hairline)
                    .frame(width: 2)
                Text(block.text)
                    .font(.system(size: size))
                    .foregroundStyle(Theme.dim)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

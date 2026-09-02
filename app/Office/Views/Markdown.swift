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
            /// Rows of cells, header first. Columns are whatever the widest
            /// row has; a short row is padded rather than dropped.
            case table([[AttributedString]])
            case rule
            /// The `---` block at the top of a vault document. Not Markdown at
            /// all: CommonMark reads the fence as a horizontal rule and welds
            /// the keys under it into one paragraph, so every memory, plan and
            /// commission in this vault opened with a grey line and the word
            /// "name: something description: something".
            case meta([(String, String)])

            static func == (a: Kind, b: Kind) -> Bool {
                switch (a, b) {
                case (.paragraph, .paragraph), (.quote, .quote), (.rule, .rule):
                    return true
                case let (.heading(x), .heading(y)): return x == y
                case let (.item(x), .item(y)): return x == y
                case let (.code(x), .code(y)): return x == y
                case let (.table(x), .table(y)): return x == y
                case let (.meta(x), .meta(y)):
                    return x.count == y.count && zip(x, y).allSatisfy { $0 == $1 }
                default: return false
                }
            }
        }

        let id: Int
        let kind: Kind
        /// How deeply this line is nested inside lists and quotes. Zero for
        /// everything at the top. A nested bullet drawn flat is a document
        /// whose structure was thrown away in the parse.
        var depth: Int = 0
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

        let (meta, body) = frontmatter(trimmed)
        let head: [Block] = meta.isEmpty
            ? []
            : [Block(id: -9_000, kind: .meta(meta), text: AttributedString(), code: "")]
        return head + parse(body, from: head.count)
    }

    /// The `---` block at the top, split off before anything parses it.
    ///
    /// Returns the pairs and the document without them. Only a fence on the
    /// very first line counts, and only up to the next one: a `---` used as a
    /// horizontal rule halfway down a document is a horizontal rule, and
    /// treating it as a second frontmatter would eat the page.
    ///
    /// Deliberately not a YAML parser. It reads `key: value` and stops at
    /// anything else, because the alternative is a dependency and a whole class
    /// of failure for a strip of grey text at the top of a page.
    static func frontmatter(_ raw: String) -> ([(String, String)], String) {
        let lines = raw.components(separatedBy: "\n")
        guard lines.first?.trimmingCharacters(in: .whitespaces) == "---" else {
            return ([], raw)
        }
        guard let close = lines.dropFirst().firstIndex(where: {
            $0.trimmingCharacters(in: .whitespaces) == "---"
        }) else { return ([], raw) }

        var pairs: [(String, String)] = []
        for line in lines[1..<close] {
            let text = line.trimmingCharacters(in: .whitespaces)
            if text.isEmpty { continue }
            guard let colon = text.firstIndex(of: ":") else { continue }
            let key = String(text[text.startIndex..<colon])
                .trimmingCharacters(in: .whitespaces)
            let value = String(text[text.index(after: colon)...])
                .trimmingCharacters(in: .whitespaces)
            // A key with nothing after it is a nested block (`metadata:`), and
            // its children are the lines under it. Shown as the key alone
            // rather than dropped: a person editing this file needs to see that
            // it is there.
            pairs.append((key, value))
        }
        let body = lines[(close + 1)...].joined(separator: "\n")
        return (pairs, body.trimmingCharacters(in: .newlines))
    }

    private static func parse(_ raw: String, from firstID: Int) -> [Block] {
        guard !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return [] }
        let parsed: AttributedString
        do {
            parsed = try AttributedString(
                markdown: raw,
                options: AttributedString.MarkdownParsingOptions(
                    allowsExtendedAttributes: true,
                    interpretedSyntax: .full,
                    failurePolicy: .returnPartiallyParsedIfPossible))
        } catch {
            return [Block(id: firstID, kind: .paragraph,
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

            // A table arrives one cell at a time, every cell its own intent.
            // Folded into one block keyed on the table's identity, so the view
            // draws a grid rather than a column of welded words.
            if let cell = tableCell(of: intent) {
                if var last = out.popLast(), case .table(var rows) = last.kind,
                   last.id == -cell.table {
                    while rows.count <= cell.row { rows.append([]) }
                    while rows[cell.row].count <= cell.column { rows[cell.row].append(AttributedString()) }
                    rows[cell.row][cell.column] += piece
                    last = Block(id: last.id, kind: .table(rows), text: last.text, code: last.code)
                    out.append(last)
                } else {
                    var rows: [[AttributedString]] = Array(repeating: [], count: cell.row + 1)
                    rows[cell.row] = Array(repeating: AttributedString(), count: cell.column + 1)
                    rows[cell.row][cell.column] = piece
                    // Identified by the table, not by its position in `out`,
                    // so the next cell finds it again; negative so it can never
                    // collide with a positional id.
                    out.append(Block(id: -abs(cell.table) - 1, kind: .table(rows),
                                     text: AttributedString(), code: ""))
                }
                openIdentity = nil
                continue
            }

            openIdentity = identity
            let read = kind(of: intent)
            if case .code = read.kind {
                out.append(Block(id: firstID + out.count, kind: read.kind, depth: read.depth,
                                 text: AttributedString(), code: String(piece.characters)))
            } else if case .item(let marker) = read.kind {
                // `- [ ] thing` is a checklist, and Foundation hands it over as
                // a bullet whose words begin with the brackets. Drawn raw it is
                // a plan whose boxes are punctuation.
                let (box, rest) = checkbox(marker: marker, text: piece)
                out.append(Block(id: firstID + out.count, kind: .item(box), depth: read.depth,
                                 text: rest, code: ""))
            } else {
                out.append(Block(id: firstID + out.count, kind: read.kind, depth: read.depth,
                                 text: piece, code: ""))
            }
        }

        // A parse that produced nothing readable still has to say what the bot
        // said, so the raw text is the floor rather than an empty bubble.
        if out.isEmpty {
            return [Block(id: firstID, kind: .paragraph,
                          text: AttributedString(raw), code: "")]
        }
        return out
    }

    /// Where a run sits in a table, if it is in one at all.
    private static func tableCell(of intent: PresentationIntent?)
        -> (table: Int, row: Int, column: Int)? {
        guard let intent else { return nil }
        var table: Int?
        var row: Int?
        var column: Int?
        for component in intent.components {
            switch component.kind {
            case .table: table = component.identity
            case .tableRow(let r): row = r
            case .tableHeaderRow: row = 0
            case .tableCell(let c): column = c
            default: continue
            }
        }
        guard let table, let column else { return nil }
        // Foundation numbers body rows from 1 and the header row is 0, so the
        // header lands on top and nothing has to be shifted.
        return (table, row ?? 0, column)
    }

    /// What kind of line this is, read off the intent stack.
    ///
    /// The stack runs innermost first: a bullet's runs carry paragraph, then
    /// listItem, then unorderedList. A code block and a heading are the whole
    /// stack on their own. Anything unrecognised is a paragraph, because a
    /// table nobody drew is still words somebody wrote.
    private static func kind(of intent: PresentationIntent?) -> (kind: Block.Kind, depth: Int) {
        guard let intent else { return (.paragraph, 0) }
        var ordinal: Int?
        var quoted = false
        // How many lists and quotes this line is inside. The stack runs
        // innermost first and carries every enclosing list, so counting them is
        // the indent: without it a three-level plan draws as one flat column of
        // bullets and stops being a plan.
        var depth = 0
        var kind: Block.Kind?
        for component in intent.components {
            switch component.kind {
            case .codeBlock(let hint):
                if kind == nil { kind = .code(hint) }
            case .header(let level):
                if kind == nil { kind = .heading(level) }
            case .listItem(let n):
                ordinal = n
            case .orderedList:
                if kind == nil, let n = ordinal { kind = .item("\(n).") }
                depth += 1
            case .unorderedList:
                if kind == nil { kind = .item(bullet(at: depth)) }
                depth += 1
            case .blockQuote:
                quoted = true
                depth += 1
            case .thematicBreak:
                if kind == nil { kind = .rule }
            default:
                continue
            }
        }
        let resolved = kind ?? (quoted ? .quote : .paragraph)
        return (resolved, max(0, depth - 1))
    }

    /// A different mark at each level, the way a printed list does it, so two
    /// nested levels are told apart by more than how far in they start.
    private static func bullet(at depth: Int) -> String {
        switch depth {
        case 0: return "\u{00b7}"
        case 1: return "\u{2013}"
        default: return "\u{2022}"
        }
    }

    /// `- [ ] thing` and `- [x] thing`, split into a box and the words.
    ///
    /// Returns the original marker and text untouched for an ordinary bullet,
    /// so nothing that is not a checklist is changed by this.
    private static func checkbox(marker: String,
                                 text: AttributedString) -> (String, AttributedString) {
        let plain = String(text.characters)
        let head = plain.prefix(4).lowercased()
        guard head.hasPrefix("[ ] ") || head.hasPrefix("[x] ") else {
            return (marker, text)
        }
        var rest = text
        if let cut = rest.index(rest.startIndex, offsetByCharacters: 4) as AttributedString.Index? {
            rest.removeSubrange(rest.startIndex..<cut)
        }
        return (head.hasPrefix("[x] ") ? "\u{2611}" : "\u{2610}", rest)
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
    /// The size at scale one. Every glyph below is drawn at `size * scale`.
    var base: Double = 13
    var color: Color = Theme.text
    /// How many blocks to draw. `nil` is all of them. A card that shows the
    /// opening paragraph and offers the rest passes 1, which is a real first
    /// paragraph rather than a fixed height with the second one sliced through
    /// the middle of a word.
    var limit: Int?

    init(raw: String, size: Double = 13, color: Color = Theme.text, limit: Int? = nil) {
        self.raw = raw
        self.base = size
        self.color = color
        self.limit = limit
    }

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
            Text(inline(block.text, size: base))
                .officeFont(size: base)
                .foregroundStyle(color)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, CGFloat(min(block.depth, 4)) * 16)

        case .heading(let level):
            // A real ramp. Six levels all drawn within one point of the body
            // text is a document with no hierarchy in it, which is the same
            // document as one with no headings.
            Text(inline(block.text, size: headingSize(level)))
                .officeFont(size: headingSize(level), weight: level <= 2 ? .bold : .semibold)
                .foregroundStyle(color)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, level <= 2 ? 8 : 4)

        case .item(let marker):
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Text(marker)
                    .officeFont(size: base)
                    .foregroundStyle(Theme.faint)
                    // A hanging indent: the marker column is fixed so the
                    // sentences line up down one edge rather than each one
                    // starting wherever its bullet happened to end.
                    .frame(minWidth: 12, alignment: .trailing)
                Text(inline(block.text, size: base))
                    .officeFont(size: base)
                    .foregroundStyle(color)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(min(block.depth, 4)) * 16)

        case .code:
            // Sideways rather than wrapped. A wrapped line of code is a line
            // that reads as two lines of code, which is how a person
            // mis-copies a command.
            ScrollView(.horizontal, showsIndicators: false) {
                Text(block.code.trimmingCharacters(in: .newlines))
                    .officeFont(size: base - 1, design: .monospaced)
                    .foregroundStyle(color)
                    .textSelection(.enabled)
                    .padding(9)
            }
            .background(
                RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Theme.well)
            )

        case .rule:
            Rectangle()
                .fill(Theme.hairline)
                .frame(height: 1)
                .padding(.vertical, 4)

        case .table(let rows):
            let columns = rows.map(\.count).max() ?? 0
            ScrollView(.horizontal, showsIndicators: false) {
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 0) {
                    ForEach(rows.indices, id: \.self) { r in
                        GridRow {
                            ForEach(0..<columns, id: \.self) { c in
                                Text(c < rows[r].count ? rows[r][c] : AttributedString())
                                    .officeFont(size: base - 0.5, weight: r == 0 ? .semibold : .regular)
                                    .foregroundStyle(r == 0 ? color : Theme.dim)
                                    .padding(.vertical, 5)
                                    .padding(.horizontal, 2)
                                    .gridColumnAlignment(.leading)
                            }
                        }
                        if r == 0 {
                            Divider().overlay(Theme.hairline)
                                .gridCellUnsizedAxes(.horizontal)
                                .gridCellColumns(columns)
                        }
                    }
                }
                .padding(.horizontal, 6)
            }
            .background(
                RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Theme.well)
            )

        case .quote:
            HStack(alignment: .top, spacing: 8) {
                Rectangle()
                    .fill(Theme.hairline)
                    .frame(width: 2)
                Text(inline(block.text, size: base))
                    .officeFont(size: base)
                    .foregroundStyle(Theme.dim)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(min(block.depth, 4)) * 16)

        case .meta(let rows):
            // The document's own frontmatter, as a strip rather than as prose.
            // Two columns, keys dim, values readable: it is a label on a file,
            // not the first paragraph of it.
            VStack(alignment: .leading, spacing: 3) {
                ForEach(rows.indices, id: \.self) { i in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(rows[i].0)
                            .officeFont(size: base - 2, weight: .medium, design: .monospaced)
                            .foregroundStyle(Theme.faint)
                            .frame(minWidth: 76, alignment: .leading)
                        Text(rows[i].1)
                            .officeFont(size: base - 1.5)
                            .foregroundStyle(Theme.dim)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Theme.well)
            )
        }
    }

    /// How big a heading of this level is drawn.
    private func headingSize(_ level: Int) -> Double {
        switch max(1, level) {
        case 1: return base + 8
        case 2: return base + 5
        case 3: return base + 3
        case 4: return base + 1.5
        default: return base
        }
    }

    /// The inline styling `Text` will not apply on its own.
    ///
    /// A backticked word carries `inlinePresentationIntent .code` and nothing
    /// else: no font, no colour. `Text` honours bold and italic from the same
    /// attribute and draws code as ordinary prose, so every command, path and
    /// symbol in a document read as a sentence. The font is set here rather
    /// than in the parse because only the view knows what size it is drawing at.
    private func inline(_ text: AttributedString, size: Double) -> AttributedString {
        var out = text
        for run in out.runs where run.inlinePresentationIntent?.contains(.code) == true {
            out[run.range].font = .system(size: size - 0.5, design: .monospaced)
            out[run.range].foregroundColor = Theme.amber
        }
        return out
    }
}

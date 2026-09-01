import SwiftUI

/// The mark on a bubble, and the menu that puts it there.
///
/// **Why a context menu and not a hover strip.** iMessage reveals its reactions
/// on a press-and-hold, and the obvious Mac translation is a row of marks that
/// fades in when the pointer is over the bubble. That is the prettier answer and
/// it is not the one here, for a reason this repo learned the same day this was
/// written: `npm run shot` photographs, it does not hover, and it does not drag.
/// #35 shipped a drag with a green build, passing tests and a correct
/// screenshot, and the drag did not work — because nothing in the harness could
/// ever have pressed a mouse button.
///
/// A context menu is the one interaction on this surface already proven by use:
/// it is how a desk gets put away and pinned in `RosterView`, and Aria drives
/// those daily. So reacting borrows the mechanism that is known to work rather
/// than the one that photographs well, and the badge it produces is a plain
/// button that any framing can see.
@MainActor
struct ReactionMenu<Content: View>: View {
    let reactions: Reactions
    let thread: String
    let turn: ChatTurn
    // Named `Content`, not `Label`. A generic parameter called `Label` shadows
    // SwiftUI's `Label` view for the whole of this type, and the menu below is
    // built out of `Label(_:systemImage:)`.
    @ViewBuilder var content: () -> Content

    private var current: Reaction? { reactions.reaction(thread: thread, turn: turn) }

    var body: some View {
        content()
            .contextMenu {
                ForEach(Reaction.allCases, id: \.self) { reaction in
                    Button {
                        reactions.toggle(reaction, thread: thread, turn: turn)
                    } label: {
                        // The one already on says "remove" rather than wearing a
                        // tick, because a tick in a menu is a state and this is
                        // a verb: pressing it takes the mark off, and the row
                        // should say the thing it is about to do.
                        Label(current == reaction ? "remove \(reaction.label)" : reaction.label,
                              systemImage: reaction.symbol)
                            .officeLabel()
                    }
                }
            }
    }
}

/// The mark itself, sitting under the corner of the bubble it belongs to.
///
/// A button, not an ornament: pressing it takes the mark off. That is the only
/// way to remove one without opening a menu, and it is what a person tries
/// first.
@MainActor
struct ReactionBadge: View {
    let reactions: Reactions
    let thread: String
    let turn: ChatTurn
    let color: Color

    var body: some View {
        if let mark = reactions.reaction(thread: thread, turn: turn) {
            Button {
                reactions.clear(thread: thread, turn: turn)
            } label: {
                Image(systemName: mark.symbol)
                    .officeSymbol(size: 9, weight: .semibold)
                    .foregroundStyle(color)
                    .frame(width: 18, height: 18)
                    .background(
                        Circle()
                            .fill(Theme.raised)
                            .overlay(Circle().strokeBorder(color.opacity(0.45), lineWidth: 1))
                    )
            }
            .buttonStyle(.plain)
            .help("\(mark.label) — click to remove")
            .accessibilityLabel(mark.label)
        }
    }
}

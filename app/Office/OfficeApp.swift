import SwiftUI
import AppKit

/// The office, on this machine.
///
/// One window and one dot. The dot is the point: closing the window puts the
/// window away, not the app, so a gate that opens while you are somewhere else
/// still turns the menu bar amber. An agent blocked with nobody watching is the
/// failure this whole surface exists to prevent.
///
/// The window is built in AppKit rather than declared as a SwiftUI `Window`
/// scene, and that is not a style choice. A `Window` scene decides for itself
/// whether to restore, and it intermittently launched with no window at all:
/// the app was running, the menu bar item was there, and nothing was on screen.
/// A shot harness cannot photograph a room that sometimes does not open, and
/// neither can a person. Owning the window means it opens every time.
@main
struct OfficeApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        MenuBarExtra {
            MenuBarContents()
                .officeFont(size: 13)
                .environment(\.typeScale, Store.shared.typeScale)
        } label: {
            Image(nsImage: MenuDot.image(for: Store.shared.dot))
        }
    }
}

// MARK: - the app

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Before the window exists, so the framing it is photographed in is the
        // one it was built under rather than one applied to it afterwards.
        ShotHarness.forceAppearanceIfAsked()
        // The poll loop belongs to the app, not to the window. A closed window
        // must never stop the dot noticing a raised hand.
        Store.shared.start()
        showOffice()
        ShotHarness.startIfAsked(store: .shared)
        watchTypeKeys()
    }

    /// Cmd + / Cmd = / Cmd - / Cmd 0, the way every Mac text surface reads
    /// them. A local monitor rather than a menu item, because this app's menu
    /// bar is the dot and the window has no menu of its own to hang a shortcut
    /// on. Only while the office window is key: a keystroke meant for another
    /// app is never this app's business.
    private func watchTypeKeys() {
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self, let window = self.window, window.isKeyWindow,
                  event.modifierFlags.intersection(.deviceIndependentFlagsMask)
                      .subtracting(.shift) == .command
            else { return event }
            switch event.charactersIgnoringModifiers {
            case "+", "=": Store.shared.biggerType()
            case "-", "_": Store.shared.smallerType()
            case "0": Store.shared.resetType()
            default: return event
            }
            return nil
        }
    }

    /// `nexus-office://open?repo=owner/name&path=docs/x.md`, from `nexus open`.
    /// Anything else on this scheme is ignored: one verb, two names, and the
    /// door still decides whether the file is readable.
    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls {
            guard let ask = OfficeURL.parse(url) else { continue }
            showOffice()
            Task { await Store.shared.open(repo: ask.repo, path: ask.path) }
        }
    }

    /// Closing the window is not quitting. The dot outlives it.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { showOffice() }
        return true
    }

    @discardableResult
    func showOffice() -> NSWindow {
        if let window {
            reveal(window)
            return window
        }

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1180, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false)
        window.title = "Office"
        // No chrome: the traffic lights float over the roster and there is no
        // toolbar, because everything on this screen is content.
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        // The one pixel SwiftUI does not paint: what is behind the content view
        // while the window resizes. A literal black here was a black flash on
        // every drag in the light room.
        window.backgroundColor = Palette.ink.nsColor
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 900, height: 560)
        // No `preferredColorScheme` any more. The office follows the machine:
        // every colour it draws is a light/dark pair in `Palette`, resolved per
        // draw, so System Settings switching to Light Appearance switches this
        // window under it with nothing restarted.
        window.contentView = NSHostingView(rootView: RootView(store: .shared))
        window.setFrameAutosaveName("office")
        if window.frame.origin == .zero { window.center() }
        reveal(window)
        self.window = window
        return window
    }

    /// Put the window on screen, taking the desktop only if somebody is there.
    ///
    /// An unattended shoot photographs the room from the back of a desk
    /// somebody else is working at, so it must never make this window key and
    /// never activate this app: a key window steals the next keystroke and an
    /// activation pulls the front window out from under whoever is reading.
    /// The window still has to be ordered in, because a window that has never
    /// been ordered in has nothing to photograph.
    private func reveal(_ window: NSWindow) {
        if ShotHarness.isUnattended {
            window.orderBack(nil)
            return
        }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}

// MARK: - the room

struct RootView: View {
    @Bindable var store: Store

    var body: some View {
        room
            .officeFont(size: 13)
            .background(Theme.ink)
            .environment(\.typeScale, store.typeScale)
            .task {
                guard store.selection == nil else { return }
                await store.refreshBots()
                await store.refreshWorld()
                if let first = store.bots.first {
                    store.select(.bot(first.id))
                } else if let desk = store.stations.first {
                    store.select(.desk(desk.repo))
                }
            }
            // The sheet is presented over whatever is on screen, because a gate is
            // never scoped to the thread you happen to be reading.
            .sheet(isPresented: .constant(store.showsGateSheet)) {
                GateSheet(store: store)
            }
    }

    /// The three shapes this window comes in.
    ///
    /// `HSplitView` rather than an `HStack` with a fixed roster: the columns
    /// were a decision this file made once and a person could never change,
    /// which is wrong for a window whose whole content is other people's repo
    /// names of wildly different lengths. AppKit owns the divider, so the drag
    /// is the real one and the position is remembered by the window itself.
    @ViewBuilder private var room: some View {
        switch store.layout {
        case .minimal:
            // The narrowest sensible framing: the floor alone, filling the
            // window, with no detail pane at all.
            RosterView(store: store)
        case .focus:
            HSplitView {
                RosterView(store: store)
                detail(store.selection)
            }
        case .compare:
            HSplitView {
                RosterView(store: store)
                detail(store.selection)
                    .deskDrop { store.select(.desk($0)) }
                detail(store.compared, second: true)
                    .deskDrop { store.compared = .desk($0) }
            }
        }
    }

    @ViewBuilder private func detail(_ selection: Selection?, second: Bool = false) -> some View {
        // The automation page is a whole screen, not a card, and it is about the
        // room rather than about whatever is selected. So it takes the detail
        // pane while it is open and gives it straight back, which keeps the
        // selection underneath it exactly where it was. Only ever the first
        // pane: it is one page and two of it is a bug.
        if store.automationOpen && !second {
            AutomationView(store: store)
                .frame(minWidth: 380, maxWidth: .infinity, maxHeight: .infinity)
        } else {
            selected(selection, second: second)
                .frame(minWidth: 380, maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    @ViewBuilder private func selected(_ selection: Selection?, second: Bool) -> some View {
        switch selection {
        case .bot(let id):
            if let bot = store.bot(id) {
                BotThreadView(store: store, bot: bot)
            } else {
                Empty(text: "That bot is not on the roster any more.")
            }
        case .desk(let repo):
            if let station = store.station(repo) {
                DeskThreadView(store: store, station: station)
            } else if store.showsLocalContext(at: repo) {
                LocalContextThreadView(store: store, repo: repo)
            } else {
                Empty(text: "That desk is not in the snapshot any more.")
            }
        case .section(let id):
            if let section = store.section(id) {
                SectionView(section: section)
            } else {
                Empty(text: "That is not on the wall any more.")
            }
        case .home:
            // Everything waiting on a person, across every desk. The mirror of
            // the phone page at `/`, and the only screen that answers "what
            // needs me" without being asked about a particular repo.
            NeedsView(store: store)
        case .feed:
            // The whole machine talking at once, across every repo. Not scoped
            // to a desk, because it belongs to none of them.
            FeedView(store: store)
        case nil:
            Empty(text: second
                  ? "Drag a desk in here to read it beside the one on the left."
                  : "Pick a bot to talk to it, a desk to work its issues, or a card on the wall to see a flow. A raised hand opens by itself.")
        }
    }
}

struct Empty: View {
    let text: String

    var body: some View {
        VStack {
            Spacer()
            Text(text)
                .officeFont(size: 12.5)
                .foregroundStyle(Theme.faint)
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .background(Theme.ink)
    }
}

// MARK: - the dot

enum MenuDot {
    /// Drawn rather than a symbol, because the three states have to be told
    /// apart in a sixteen point strip out of the corner of an eye, and colour is
    /// the only channel that survives that.
    static func image(for state: DotState) -> NSImage {
        // Resolved by hand rather than left to a dynamic `NSColor`, because
        // this is drawn into a bitmap with `lockFocus` and a bitmap has no
        // appearance to ask. The menu bar in the light room is a light strip,
        // and the greys that read on a dark one vanish on it.
        let swatch: Palette.Swatch
        switch state {
        case .idle: swatch = Palette.dotIdle
        case .working: swatch = Palette.dotWorking
        case .needsYou: swatch = Palette.dotNeedsYou
        }
        let rgb = swatch.value(dark: NSApp?.effectiveAppearance.isDark ?? true)
        let color = NSColor(srgbRed: rgb.red, green: rgb.green, blue: rgb.blue, alpha: 1)
        let size = NSSize(width: 14, height: 14)
        let image = NSImage(size: size)
        image.lockFocus()
        if state == .needsYou {
            color.withAlphaComponent(0.28).setFill()
            NSBezierPath(ovalIn: NSRect(x: 0.5, y: 0.5, width: 13, height: 13)).fill()
        }
        color.setFill()
        let inset: CGFloat = state == .idle ? 4.5 : 3.5
        NSBezierPath(ovalIn: NSRect(x: inset, y: inset,
                                    width: size.width - inset * 2,
                                    height: size.height - inset * 2)).fill()
        image.unlockFocus()
        image.isTemplate = false
        return image
    }
}

struct MenuBarContents: View {
    private var store: Store { .shared }

    var body: some View {
        Text(store.dot.title)
        if store.gate.isPending {
            Text("permission: \(StateRules.line(store.gate.permission, limit: 40))")
            // Two hands up and a menu that mentions one of them is a menu that
            // hides the other.
            if store.gates.count > 1 {
                Text("\(store.gates.count) agents are waiting on you")
            }
        }
        if store.waitingCount > 0 {
            Text("\(store.waitingCount) issues waiting on you")
        }
        if store.wallNeeds > 0 {
            Text(store.wallLine)
        }
        Divider()
        Button("Open the office") {
            (NSApp.delegate as? AppDelegate)?.showOffice()
        }
        Divider()
        Button("Quit") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }
}

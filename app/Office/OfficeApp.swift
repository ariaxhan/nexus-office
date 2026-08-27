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
        // The poll loop belongs to the app, not to the window. A closed window
        // must never stop the dot noticing a raised hand.
        Store.shared.start()
        showOffice()
        ShotHarness.startIfAsked(store: .shared)
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
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
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
        window.backgroundColor = .black
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 900, height: 560)
        window.contentView = NSHostingView(
            rootView: RootView(store: .shared).preferredColorScheme(.dark))
        window.setFrameAutosaveName("office")
        if window.frame.origin == .zero { window.center() }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        self.window = window
        return window
    }
}

// MARK: - the room

struct RootView: View {
    @Bindable var store: Store

    var body: some View {
        HStack(spacing: 0) {
            RosterView(store: store)
            Divider().overlay(Theme.hairline)
            detail
        }
        .background(Theme.ink)
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

    @ViewBuilder private var detail: some View {
        switch store.selection {
        case .bot(let id):
            if let bot = store.bot(id) {
                BotThreadView(store: store, bot: bot)
            } else {
                Empty(text: "That bot is not on the roster any more.")
            }
        case .desk(let repo):
            if let station = store.station(repo) {
                DeskThreadView(store: store, station: station)
            } else {
                Empty(text: "That desk is not in the snapshot any more.")
            }
        case .section(let id):
            if let section = store.section(id) {
                SectionView(section: section)
            } else {
                Empty(text: "That is not on the wall any more.")
            }
        case nil:
            Empty(text: "Pick a bot to talk to it, a desk to work its issues, or a card on the wall to see a flow. A raised hand opens by itself.")
        }
    }
}

struct Empty: View {
    let text: String

    var body: some View {
        VStack {
            Spacer()
            Text(text)
                .font(.system(size: 12.5))
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
        let color: NSColor
        switch state {
        case .idle: color = NSColor(calibratedWhite: 0.55, alpha: 1)
        case .working: color = NSColor(srgbRed: 0.30, green: 0.55, blue: 1.0, alpha: 1)
        case .needsYou: color = NSColor(srgbRed: 1.0, green: 0.69, blue: 0.13, alpha: 1)
        }
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

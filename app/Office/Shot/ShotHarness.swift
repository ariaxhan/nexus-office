import AppKit
import SwiftUI

/// The eyes.
///
/// A build passing proves nothing about a room. Every defect this project has
/// had was invisible in source and obvious on screen, so the app can photograph
/// itself: `--demo <fixture> --shot-mode` opens the window at a fixed size,
/// walks seven framings, and writes each one to `shots/app-<framing>.png`.
///
/// `--light` is the eighth. The office follows the system appearance, and a
/// machine set to Dark would photograph the light room never, so that run
/// forces `.aqua` on the whole app before the window is built and takes one
/// picture of the roster. The roster is where the most colours are on screen
/// at once: a palette that fails, fails there first.
///
/// The capture is a region rather than `screencapture -l <windowid>` for one
/// reason: a sheet is its own window, so a window capture of the gate framing
/// would photograph an office with no gate in it, which is the exact lie this
/// harness exists to catch. The region is the window's own frame, read from the
/// window itself, so the framing is still the window and nothing else.
@MainActor
enum ShotHarness {
    static var isOn: Bool { CommandLine.arguments.contains("--shot-mode") }

    /// Photograph the light room instead of the dark one.
    static var isLight: Bool { CommandLine.arguments.contains("--light") }

    /// Pin the room a shoot is photographed in, before the window exists.
    ///
    /// Both ways round, and that is the point. The app follows the machine now,
    /// so without this the seven framings would come out light on a Mac set to
    /// Light Appearance and the pictures would silently stop being comparable
    /// with the ones in the last commit. A shoot decides its own appearance;
    /// only a person running the app for real gets the system's.
    ///
    /// Before the window rather than after: an appearance applied to a window
    /// that has already drawn itself once is a race between the redraw and the
    /// shutter.
    static func forceAppearanceIfAsked() {
        guard isOn else { return }
        NSApp.appearance = NSAppearance(named: isLight ? .aqua : .darkAqua)
    }

    private static var started = false
    private static let framing = NSSize(width: 1440, height: 900)

    static func startIfAsked(store: Store) {
        guard isOn, !started else { return }
        started = true
        Task { await run(store: store) }
    }

    private static func run(store: Store) async {
        let directory = Api.flag("--shots", in: CommandLine.arguments)
            ?? FileManager.default.currentDirectoryPath + "/shots"
        try? FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: true)

        // The gate sheet is the loudest thing this app does and it would sit on
        // top of every other framing. It is parked for the pictures that are
        // not about it, and only for those.
        store.suppressGateSheet = true
        await settle(2.0)

        guard let window = officeWindow() else {
            say("no window to photograph")
            NSApp.terminate(nil)
            return
        }
        place(window)
        NSApp.activate(ignoringOtherApps: true)
        await settle(1.2)

        if let bot = preferredBot(store) {
            store.select(.bot(bot))
        } else {
            // A server with no chatroom yet is a floor of desks. Photograph that
            // rather than a thread for a bot that does not exist.
            store.select(.desk(deskWithWork(store)))
        }

        // One picture, and out. The other seven are the same room and would
        // only double the wall clock of every shoot to say the same thing
        // twice.
        if isLight {
            await frame(store, window, directory, "light", settling: 1.4)
            NSApp.terminate(nil)
            return
        }

        await frame(store, window, directory, "roster")

        store.select(.desk(deskWithWork(store)))
        await frame(store, window, directory, "desk")

        if let asker = store.gate.bot, store.bot(asker) != nil {
            store.select(.bot(asker))
        }
        store.suppressGateSheet = false
        await frame(store, window, directory, "gate", settling: 1.6)
        store.suppressGateSheet = true
        await settle(0.8)

        store.needsOnly = true
        store.select(.desk(deskNeedingAPerson(store)))
        await frame(store, window, directory, "needs")

        // The wall: the local sources, which are not repos and not colleagues,
        // and the pane one of them opens into. Photographed with the source
        // that says something needs a person, because a wall of quiet rows
        // proves the group renders and proves nothing about the badge.
        store.needsOnly = false
        store.select(.section(sectionNeedingAPerson(store)))
        await frame(store, window, directory, "wall", settling: 1.4)

        // The two things the other framings cannot show at once: the
        // desks a person put away, which live in a section that is shut by
        // default, and a desk saying out loud that what you are reading is the
        // last thing it managed to pull.
        store.putAwayOpen = true
        store.select(.desk(deskShowingOldData(store)))
        await frame(store, window, directory, "putaway")

        // The automation page: the schedule, the door, and the list of what the
        // runner touched with the link to what it said. It replaces the detail
        // pane rather than opening a sheet, so it photographs as the window.
        store.putAwayOpen = false
        store.automationOpen = true
        await frame(store, window, directory, "automation", settling: 1.4)
        store.automationOpen = false

        // A desk with a session on it, opened. The sessions block sits above the
        // issues and none of the seven older framings would ever reveal it: it
        // is drawn from a route the other framings do not read, and its
        // composer only exists while a thread is open.
        if let desk = deskWithASession(store) {
            store.select(.desk(desk.repo))
            if let session = store.sessions(at: desk.repo).sessions.first {
                store.openSessionThread(session.name)
                await settle(0.6)
                store.drafts[Store.draftKey(session: session.name)] =
                    "take the second option, and say why in the issue"
            }
            await frame(store, window, directory, "sessions", settling: 1.4)
            store.openSession = nil
        }

        // A picture picked and not yet sent. The composer is the one part of
        // this app whose state nothing else photographs: the chip, the size
        // after the downscale and the way out of it all live for the seconds
        // between choosing a screenshot and sending it, and the mark on the
        // turn above it is what that send leaves behind.
        store.putAwayOpen = false
        if let bot = preferredBot(store) {
            store.select(.bot(bot))
            let key = Store.draftKey(bot: bot)
            store.drafts[key] = "and this is the one from staging"
            store.pendingAttachments[key] = PreparedImage(
                name: "checkout-staging.jpg", mimeType: "image/jpeg",
                base64: "", bytes: 188_416, width: 1200, height: 780)
            await frame(store, window, directory, "attach", settling: 1.4)
        }

        NSApp.terminate(nil)
    }

    /// A desk the demo floor has an agent sitting at, preferring one that is
    /// waiting on a person: that is the row with the amber pill and the reason
    /// this framing exists.
    private static func deskWithASession(_ store: Store) -> Station? {
        let withSessions = store.stations.filter { !store.sessions(at: $0.repo).sessions.isEmpty }
        return withSessions.first { store.sessions(at: $0.repo).blocked > 0 } ?? withSessions.first
    }

    /// One framing: put the room in a known state, let it settle, photograph it.
    ///
    /// The search field is cleared every time on purpose. The window takes focus
    /// to be photographed, and a keystroke meant for another app lands in it: a
    /// stray character in the search box silently filtered four desks out of a
    /// framing once, and the picture looked plausible.
    private static func frame(_ store: Store, _ window: NSWindow,
                              _ directory: String, _ name: String,
                              settling seconds: Double = 1.0) async {
        store.query = ""
        await settle(seconds)
        shoot(window, into: directory, named: name)
    }

    // MARK: - the window

    private static func officeWindow() -> NSWindow? {
        if let titled = NSApp.windows.first(where: { $0.isVisible && $0.title == "Office" }) {
            return titled
        }
        return NSApp.windows
            .filter { $0.isVisible && $0.frame.width > 500 }
            .max { $0.frame.width < $1.frame.width }
    }

    private static func place(_ window: NSWindow) {
        guard let screen = window.screen ?? NSScreen.main else { return }
        let usable = screen.visibleFrame
        // Clamped, because a window hanging under the Dock photographs the Dock.
        // Dropped below the top for the same reason: other people's floating
        // panels live in the notch strip, and a region capture cannot tell them
        // apart from this window's own pixels.
        let clearance: CGFloat = 48
        let size = NSSize(width: min(framing.width, usable.width),
                          height: min(framing.height, usable.height - clearance))
        let origin = NSPoint(x: (usable.midX - size.width / 2).rounded(),
                             y: (usable.maxY - clearance - size.height).rounded())
        window.setFrame(NSRect(origin: origin, size: size), display: true)
        window.makeKeyAndOrderFront(nil)
        parkTheCursor(over: window)
    }

    /// Put the pointer somewhere harmless inside the window.
    ///
    /// Wherever it rests, something hovers: a Dock tooltip drew itself across the
    /// bottom of a framing, and a row under the cursor highlights as though it
    /// were selected. An empty patch of the thread is the one place that reacts
    /// to nothing.
    private static func parkTheCursor(over window: NSWindow) {
        guard let primary = NSScreen.screens.first(where: { $0.frame.origin == .zero })
            ?? NSScreen.screens.first else { return }
        let frame = window.frame
        CGWarpMouseCursorPosition(CGPoint(x: frame.maxX - 40,
                                          y: primary.frame.maxY - frame.maxY + 40))
        CGAssociateMouseAndMouseCursorPosition(1)
    }

    private static func shoot(_ window: NSWindow, into directory: String, named name: String) {
        // `screencapture -R` measures from the top left of the PRIMARY display,
        // which on a multi display Mac is the one at the origin and not
        // necessarily the first one AppKit lists.
        guard let primary = NSScreen.screens.first(where: { $0.frame.origin == .zero })
            ?? NSScreen.screens.first else { return }
        let frame = window.frame
        let rect = [Int(frame.minX.rounded()),
                    Int((primary.frame.maxY - frame.maxY).rounded()),
                    Int(frame.width.rounded()),
                    Int(frame.height.rounded())]
            .map(String.init)
            .joined(separator: ",")

        // Key before the shutter, so the picture shows a window a person is
        // actually looking at rather than a greyed out one.
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        let path = "\(directory)/app-\(name).png"
        let capture = Process()
        capture.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        capture.arguments = ["-x", "-o", "-R", rect, path]
        do {
            try capture.run()
            capture.waitUntilExit()
            say(capture.terminationStatus == 0
                ? "wrote \(path)"
                : "screencapture failed on \(name) with status \(capture.terminationStatus)")
        } catch {
            say("could not run screencapture: \(error.localizedDescription)")
        }
    }

    // MARK: - what to point it at

    private static func preferredBot(_ store: Store) -> String? {
        if store.bot("chief") != nil { return "chief" }
        return store.bots.first?.id
    }

    /// A desk with a button worth pressing. A framing of a repo whose only PR is
    /// a draft never photographs the merge button, which is the one control on
    /// this screen that can do something irreversible.
    private static func deskWithWork(_ store: Store) -> String {
        let best = store.stations.first { station in
            !station.issues.isEmpty && station.prs.contains(where: \.canMerge)
        }
            ?? store.stations.first { !$0.issues.isEmpty && !$0.prs.isEmpty }
            ?? store.stations.first { !$0.issues.isEmpty }
        return best?.repo ?? store.stations.first?.repo ?? ""
    }

    /// A source with a count on it. A framing of a wall row whose badge is
    /// absent photographs the one case the badge was built for not happening.
    private static func sectionNeedingAPerson(_ store: Store) -> String {
        let wanted = store.sections.first { $0.needs > 0 }
            ?? store.sections.first { !$0.isOK }
        return wanted?.id ?? store.sections.first?.id ?? ""
    }

    private static func deskNeedingAPerson(_ store: Store) -> String {
        let waiting = store.stations.first { StateRules.deskState($0) == .waiting }
        return waiting?.repo ?? deskWithWork(store)
    }

    /// A desk whose last successful pull is behind the snapshot it arrived in,
    /// AND which has issues to draw under the notice. A framing of the notice
    /// over an empty desk proves the sentence renders and proves nothing about
    /// the thing it exists for, which is last-good data still being there.
    private static func deskShowingOldData(_ store: Store) -> String {
        let stale = store.stations.first { station in
            StateRules.isStale(station: station, generated: store.worldGenerated)
                && !station.problems.isEmpty
                && !station.issues.isEmpty
        }
        return stale?.repo ?? deskWithWork(store)
    }

    private static func settle(_ seconds: Double) async {
        try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }

    private static func say(_ line: String) {
        FileHandle.standardError.write(Data(("shot: " + line + "\n").utf8))
    }
}

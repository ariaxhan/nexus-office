import AppKit
import SwiftUI

/// The eyes.
///
/// A build passing proves nothing about a room. Every defect this project has
/// had was invisible in source and obvious on screen, so the app can photograph
/// itself: `--demo <fixture> --shot-mode` opens the window at a fixed size,
/// walks five framings, and writes each one to `shots/app-<framing>.png`.
///
/// The capture is a region rather than `screencapture -l <windowid>` for one
/// reason: a sheet is its own window, so a window capture of the gate framing
/// would photograph an office with no gate in it, which is the exact lie this
/// harness exists to catch. The region is the window's own frame, read from the
/// window itself, so the framing is still the window and nothing else.
@MainActor
enum ShotHarness {
    static var isOn: Bool { CommandLine.arguments.contains("--shot-mode") }

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
        // top of every other framing. It is parked for the three pictures that
        // are not about it, and only for those.
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

        // The two things the other four framings cannot show at once: the
        // desks a person put away, which live in a section that is shut by
        // default, and a desk saying out loud that what you are reading is the
        // last thing it managed to pull.
        store.needsOnly = false
        store.putAwayOpen = true
        store.select(.desk(deskShowingOldData(store)))
        await frame(store, window, directory, "putaway")

        NSApp.terminate(nil)
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

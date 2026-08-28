import AppKit
import ScreenCaptureKit
import SwiftUI

/// The eyes.
///
/// A build passing proves nothing about a room. Every defect this project has
/// had was invisible in source and obvious on screen, so the app can photograph
/// itself: `--demo <fixture> --shot-mode` opens the window at a fixed size,
/// walks its framings, and writes each one to `shots/app-<framing>.png`.
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

    /// Photograph without taking the desktop.
    ///
    /// A lane that cannot see ships what it never looked at, which is the one
    /// rule this repo opens with. The visible path is correctly refused to an
    /// unattended run, because it activates a window and warps the cursor on a
    /// desk somebody is working at. This path does neither: the window is
    /// ordered to the BACK, never made key, never activated, and the cursor is
    /// left exactly where the person left it.
    ///
    /// The picture cannot come from `screencapture` then, because a region of
    /// the screen would photograph whatever is in front of the office rather
    /// than the office. It comes from ScreenCaptureKit, which composites a
    /// named set of windows and nothing else, so what is in front does not
    /// appear and does not have to be moved.
    ///
    /// The set is every window this app has, not just the big one, which keeps
    /// the property the visible path buys with a region: a sheet is its own
    /// window, and a capture that photographed only the main window would show
    /// the gate framing with no gate in it.
    static var isUnattended: Bool { CommandLine.arguments.contains("--offscreen") }

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
        // `place` has already ordered an unattended window to the back on
        // purpose; activating here would undo it and take the desktop.
        if !isUnattended { NSApp.activate(ignoringOtherApps: true) }
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

        // The two sources whose whole point is the LIST rather than the count.
        // The `wall` framing above photographs a card with facts on it and
        // would look identical whether the rows arrived or not, which is the
        // exact defect this harness exists to catch: a table that decoded to
        // nothing is invisible in source and obvious here.
        // The pipeline's own list: which issues are being worked right now, and
        // which are waiting for a lane. Its own framing rather than a corner of
        // `wall`, because the card alone is a headline and a count, and the
        // whole question a person has here is WHICH ones, by name. A lane that
        // has gone quiet draws differently from one that is working, and no
        // other framing would ever show that.
        for source in ["library", "clock", "pipeline"] where store.section(source) != nil {
            store.select(.section(source))
            await frame(store, window, directory, source, settling: 1.4)
        }

        // A desk's own Markdown, open. It is a whole second half of the desk
        // behind a switch, so no framing that photographs the Work side can
        // ever reveal it: an index that came back empty and a document that
        // never loaded both draw as a desk with nothing on it.
        if let desk = await deskWithContext(store) {
            store.select(.desk(desk))
            store.showContext(at: desk)
            // Long enough for the index AND the file it opens: the read is two
            // calls, and photographing between them is a picture of a list
            // beside an empty pane, which is the one state this is not about.
            await frame(store, window, directory, "context", settling: 2.2)
            store.showWork(at: desk)
        }

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

        // The marks a person left on a conversation. Its own framing rather than
        // a corner of the composer shot: a reaction is drawn from a store none
        // of the other ten read, and leaning on `attach` to prove it would mean
        // the day it stops rendering, the only picture that could say so is one
        // nobody is scanning for a mark.
        //
        // The marks come from the fixture's `reactions`, seeded when the thread
        // loads. A screenshot run keeps them in memory and never writes them
        // down, so photographing this cannot edit what Aria reacted to.
        if let bot = preferredBot(store) {
            store.select(.bot(bot))
            await frame(store, window, directory, "reactions", settling: 1.4)
        }

        // A desk with nothing open on it. Every other framing photographs a
        // desk that has work on it, so none of them would ever show what a
        // quiet desk draws, which is the one place this app used to print a
        // single grey line and stop.
        if let quiet = quietDesk(store) {
            store.select(.desk(quiet.repo))
            await frame(store, window, directory, "readme", settling: 1.4)
        }

        // The gear, open. Every preference this app has is behind this one
        // button now, and no other framing can reach it: the popover is its own
        // window, so a run that never opens it photographs a room where the
        // settings surface and a settings surface that draws nothing look
        // exactly the same.
        store.settingsOpen = true
        await frame(store, window, directory, "settings", settling: 1.4)
        store.settingsOpen = false
        await settle(0.6)

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

    /// A desk with no issues, no PRs and no raised hand: the state whose whole
    /// content is the repo's front page.
    private static func quietDesk(_ store: Store) -> Station? {
        store.stations.first {
            $0.issues.isEmpty && $0.prs.isEmpty && !$0.hidden && $0.gate == nil
        }
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
        await shoot(window, into: directory, named: name)
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
        if isUnattended {
            // Ordered in so it draws, ordered BACK so it never covers what the
            // person is reading, and never made key: a key window steals the
            // keystrokes of whoever is typing.
            window.orderBack(nil)
            return
        }
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

    /// One picture, composited from this app's own windows.
    ///
    /// The window set rather than the screen region is the whole point: the
    /// office is at the BACK of somebody's desktop while this runs, so a region
    /// of the screen would photograph their browser. ScreenCaptureKit draws the
    /// listed windows and nothing else, occluded or not.
    ///
    /// Every visible window this app owns is listed, and the picture is cropped
    /// to the union of the ones touching the office window. That is how a sheet
    /// stays in the frame: it is its own window, it hangs over the office, and
    /// a capture of the main window alone would show the gate framing with no
    /// gate in it, which is the exact lie this harness exists to catch.
    private static func captureQuietly(_ window: NSWindow,
                                       into directory: String,
                                       named name: String) async {
        let path = "\(directory)/app-\(name).png"
        // A window number is a signed `Int` and AppKit hands out values a
        // `CGWindowID` cannot hold for windows the window server does not own.
        // Converting one of those traps the whole app, so every conversion here
        // is the failable one: a window nothing can photograph is a window to
        // leave out, not a crash.
        let ours = Set(NSApp.windows.filter(\.isVisible)
            .compactMap { CGWindowID(exactly: $0.windowNumber) })
        guard let target = CGWindowID(exactly: window.windowNumber) else {
            say("the office window has no window server id, so \(name) was not photographed")
            return
        }
        do {
            // `onScreenWindowsOnly: false`, because a window that is behind
            // everything else is still a window this app must be able to
            // photograph, and the on-screen list is not a promise about that.
            let content = try await SCShareableContent.excludingDesktopWindows(
                true, onScreenWindowsOnly: false)
            let mine = content.windows.filter { ours.contains($0.windowID) }
            guard let office = mine.first(where: { $0.windowID == target }) else {
                say("the office window is not in the window list, so \(name) was not photographed")
                return
            }
            guard let display = content.displays.first(where: { $0.frame.intersects(office.frame) })
                ?? content.displays.first else {
                say("no display to photograph \(name) against")
                return
            }
            // The filter's content is the windows themselves, not the screen
            // they happen to sit on, so the picture is asked for at the office
            // window's own size and never cropped afterwards. Sizing it to the
            // display instead stretches that content across the whole frame,
            // which photographs a room sliding off its own edges.
            let filter = SCContentFilter(display: display, including: mine)
            let scale = CGFloat(filter.pointPixelScale)
            let config = SCStreamConfiguration()
            config.width = Int(office.frame.width * scale)
            config.height = Int(office.frame.height * scale)
            config.showsCursor = false
            let cut = try await SCScreenshotManager.captureImage(contentFilter: filter,
                                                                 configuration: config)
            let rep = NSBitmapImageRep(cgImage: cut)
            guard let png = rep.representation(using: .png, properties: [:]) else {
                say("could not encode \(name)")
                return
            }
            try png.write(to: URL(fileURLWithPath: path))
            say("wrote \(path)")
        } catch {
            say("could not photograph \(name) quietly: \(error.localizedDescription)")
        }
    }

    private static func shoot(_ window: NSWindow, into directory: String, named name: String) async {
        if isUnattended {
            await captureQuietly(window, into: directory, named: name)
            return
        }
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

    /// A desk the fixture has Markdown for, preferring one with the most of it:
    /// a context framing of a desk with a lone README photographs the pane and
    /// not the index, and the index is half of what this screen is.
    ///
    /// Asked by loading rather than by reading the fixture, because the demo
    /// floor and the live door answer this the same way and the harness must
    /// not learn a shape only one of them has.
    private static func deskWithContext(_ store: Store) async -> String? {
        var best: (repo: String, files: Int)?
        for station in store.stations.prefix(12) {
            await store.loadContext(repo: station.repo)
            guard let found = store.context(at: station.repo), !found.files.isEmpty
            else { continue }
            if best == nil || found.files.count > best!.files {
                best = (station.repo, found.files.count)
            }
        }
        return best?.repo
    }

    /// A source with a count on it. A framing of a wall row whose badge is
    /// absent photographs the one case the badge was built for not happening.
    ///
    /// One with no TABLE, where there is one. `library` and `clock` have their
    /// own framings now, and three pictures of the same card is two framings
    /// that rot: this one exists to prove the badge and the facts, so it is
    /// pointed at a source whose card is only that.
    private static func sectionNeedingAPerson(_ store: Store) -> String {
        let wanted = store.sections.first { $0.needs > 0 && $0.card.rows.isEmpty }
            ?? store.sections.first { $0.needs > 0 }
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

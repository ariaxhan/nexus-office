#!/usr/bin/env swift

// The de-risk CLI for #25: can a model find a named button on this screen, and
// can we click it without taking the desk away from the person at it?
//
// WHY THIS EXISTS BEFORE ANY OF THE REST
// -------------------------------------
// The issue asks for a bot with hands, gated, with a kill switch. That is thirty
// hours of work resting on one unmeasured claim: that a downscaled screenshot is
// enough for Claude to point at a real button in a real app. This file measures
// exactly that claim and nothing else. It is deliberately not wired to the gate,
// the harness, or any daemon: nothing here can be reached by an agent, because
// the only way to run it is a person typing it.
//
//     swift scripts/hands.swift TextEdit "Save"          # look only, no click
//     swift scripts/hands.swift Slack "the compose button"
//     swift scripts/hands.swift TextEdit "Save" --click   # actually clicks once
//
// LOOKING IS THE DEFAULT AND CLICKING IS NOT. Without --click it prints the
// point and draws nothing on the machine, so a wrong answer costs a line of
// output instead of a click in somebody's document.
//
// It never activates the target app: the window is found in the window server by
// owner name, photographed by its frame, and the click is posted to the HID tap
// with the cursor put back where it was. The window may still raise itself in
// response to the click -- that is the app's decision, not ours -- and that is
// one of the things this CLI exists to find out.
//
// Needs Screen Recording (to see) and Accessibility (to click) for whatever
// runs it, which is the terminal, not the Office bundle. ANTHROPIC_API_KEY must
// be set; nothing here reads a credential from anywhere else.

import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

let MODEL = "claude-opus-5"
let MAX_EDGE = 1280  // the downscale: an edge longer than this buys no accuracy

func die(_ message: String) -> Never {
    FileHandle.standardError.write(Data(("hands: " + message + "\n").utf8))
    exit(1)
}

// MARK: - the window, without touching it

/// The frontmost on-screen window belonging to an app, by owner name. Read out
/// of the window server, so nothing is activated, raised or made key.
func windowFrame(ownerName: String) -> CGRect {
    let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
    guard let windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else {
        die("could not read the window list (Screen Recording permission?)")
    }
    for window in windows {
        guard let owner = window[kCGWindowOwnerName as String] as? String,
              owner.caseInsensitiveCompare(ownerName) == .orderedSame,
              let bounds = window[kCGWindowBounds as String] as? [String: Any],
              let rect = CGRect(dictionaryRepresentation: bounds as CFDictionary),
              rect.width > 120, rect.height > 120
        else { continue }
        return rect
    }
    die("no on-screen window belongs to \(ownerName). Is it running and unminimised?")
}

// MARK: - the picture

/// `screencapture -R` on the window's frame, not `-l <windowid>`: the same
/// reason the shot harness does it, plus one more. A window capture omits a
/// sheet, and a sheet is exactly where the interesting buttons are.
func capture(_ rect: CGRect) -> URL {
    let url = URL(fileURLWithPath: NSTemporaryDirectory())
        .appendingPathComponent("hands-\(getpid()).png")
    let task = Process()
    task.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    task.arguments = [
        "-x", "-o",
        "-R\(Int(rect.origin.x)),\(Int(rect.origin.y)),\(Int(rect.width)),\(Int(rect.height))",
        url.path,
    ]
    try? task.run()
    task.waitUntilExit()
    guard task.terminationStatus == 0, FileManager.default.fileExists(atPath: url.path) else {
        die("screencapture failed (Screen Recording permission for this terminal?)")
    }
    return url
}

/// Downscale to a long edge of MAX_EDGE and return the PNG bytes plus the
/// picture's own size, because the answer comes back in the picture's pixels
/// and has to be spent in the window's points.
func downscale(_ url: URL) -> (png: Data, size: CGSize) {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
              kCGImageSourceCreateThumbnailFromImageAlways: true,
              kCGImageSourceThumbnailMaxPixelSize: MAX_EDGE,
          ] as CFDictionary)
    else { die("could not read \(url.path)") }

    let out = NSMutableData()
    guard let dest = CGImageDestinationCreateWithData(out, UTType.png.identifier as CFString, 1, nil) else {
        die("could not encode the downscaled picture")
    }
    CGImageDestinationAddImage(dest, image, nil)
    guard CGImageDestinationFinalize(dest) else { die("could not encode the downscaled picture") }
    return (out as Data, CGSize(width: image.width, height: image.height))
}

// MARK: - the ask

struct Answer {
    let found: Bool
    let x: Double
    let y: Double
    let what: String
}

func ask(png: Data, size: CGSize, target: String, app: String) -> Answer {
    guard let key = ProcessInfo.processInfo.environment["ANTHROPIC_API_KEY"], !key.isEmpty else {
        die("ANTHROPIC_API_KEY is not set")
    }
    let prompt = """
    This is a screenshot of the \(app) window, \(Int(size.width)) by \(Int(size.height)) pixels.

    Find: \(target)

    Reply with one JSON object and nothing else:
    {"found": true|false, "x": <pixel>, "y": <pixel>, "what": "<what you are pointing at, or why you could not find it>"}

    x and y are the centre of the thing, in the pixel coordinates of this image,
    with 0,0 at its top left. If it is not visible, "found" is false and x and y
    are 0. Do not guess a location for something you cannot see: a wrong point is
    worse than no point, because a wrong point gets clicked.
    """
    let body: [String: Any] = [
        "model": MODEL,
        "max_tokens": 1024,
        "messages": [[
            "role": "user",
            "content": [
                ["type": "image",
                 "source": ["type": "base64", "media_type": "image/png",
                            "data": png.base64EncodedString()]],
                ["type": "text", "text": prompt],
            ],
        ]],
    ]

    var request = URLRequest(url: URL(string: "https://api.anthropic.com/v1/messages")!)
    request.httpMethod = "POST"
    request.timeoutInterval = 120
    request.setValue(key, forHTTPHeaderField: "x-api-key")
    request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
    request.setValue("application/json", forHTTPHeaderField: "content-type")
    request.httpBody = try? JSONSerialization.data(withJSONObject: body)

    var payload: Data?
    var failure: String?
    let done = DispatchSemaphore(value: 0)
    URLSession.shared.dataTask(with: request) { data, response, error in
        if let error { failure = error.localizedDescription }
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            failure = "HTTP \(http.statusCode): \(String(data: data ?? Data(), encoding: .utf8) ?? "")"
        }
        payload = data
        done.signal()
    }.resume()
    done.wait()
    if let failure { die(failure) }

    guard let payload,
          let top = try? JSONSerialization.jsonObject(with: payload) as? [String: Any],
          let content = top["content"] as? [[String: Any]],
          let text = content.first(where: { $0["type"] as? String == "text" })?["text"] as? String,
          let start = text.firstIndex(of: "{"), let end = text.lastIndex(of: "}"),
          let object = try? JSONSerialization.jsonObject(
              with: Data(text[start...end].utf8)) as? [String: Any]
    else { die("could not read an answer out of the reply") }

    return Answer(found: object["found"] as? Bool ?? false,
                  x: object["x"] as? Double ?? 0,
                  y: object["y"] as? Double ?? 0,
                  what: object["what"] as? String ?? "")
}

// MARK: - the hand

/// One click at a screen point, posted to the HID tap. The app is never
/// activated and the pointer is put back where the person left it.
func click(at point: CGPoint) {
    let was = CGEvent(source: nil)?.location
    let source = CGEventSource(stateID: .hidSystemState)
    guard let down = CGEvent(mouseEventSource: source, mouseType: .leftMouseDown,
                             mouseCursorPosition: point, mouseButton: .left),
          let up = CGEvent(mouseEventSource: source, mouseType: .leftMouseUp,
                           mouseCursorPosition: point, mouseButton: .left)
    else { die("could not build the click (Accessibility permission for this terminal?)") }
    down.post(tap: .cghidEventTap)
    usleep(60_000)
    up.post(tap: .cghidEventTap)
    if let was { CGWarpMouseCursorPosition(was) }
}

// MARK: - run it

var args = Array(CommandLine.arguments.dropFirst())
let shouldClick = args.contains("--click")
args.removeAll { $0 == "--click" }
guard args.count == 2 else {
    die("usage: swift scripts/hands.swift <app> <what to find> [--click]")
}
let app = args[0], target = args[1]

let frame = windowFrame(ownerName: app)
let shot = capture(frame)
defer { try? FileManager.default.removeItem(at: shot) }
let (png, size) = downscale(shot)
let answer = ask(png: png, size: size, target: target, app: app)

guard answer.found else {
    print("not found: \(answer.what)")
    exit(2)
}

// The picture's pixels back into the window's points, and the window's points
// into the screen. Both scales are in play: the downscale, and the Retina
// backing store the capture was taken at.
let point = CGPoint(x: frame.origin.x + answer.x / Double(size.width) * frame.width,
                    y: frame.origin.y + answer.y / Double(size.height) * frame.height)
print(String(format: "%@ at %.0f,%.0f in %@  (%@)",
             target, point.x, point.y, app, answer.what))

if shouldClick {
    click(at: point)
    print("clicked. \(app) was never activated; if it came forward, it raised itself.")
} else {
    print("looked only. --click to actually press it.")
}

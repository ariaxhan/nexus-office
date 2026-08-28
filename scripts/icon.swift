#!/usr/bin/env swift

// The app's icon, drawn rather than stored.
//
// WHY THIS IS A SCRIPT AND NOT A PNG SOMEBODY EXPORTED
// ---------------------------------------------------
// An icon exported from a design tool is a binary nobody in this repo can read,
// diff or argue with. Every number that decides what the mark looks like is in
// this file, so changing the amber, the spacing or the corner is an edit with a
// diff on it, and the ten PNGs it produces are derived the way the xcodeproj is.
//
// WHAT IT DRAWS
// -------------
// The roster, which is the app. Four rows in the dark room: three quiet, and one
// lit amber with a brighter name beside it. That is the entire thesis of this
// program in one picture -- most of your desks are fine, and this one is asking
// you something -- and it is the same amber as the menu bar dot, which is the
// mark Aria already reads at a glance a hundred times a day.
//
// Deliberately not a building, a desk, a robot or a chat bubble. Those are
// pictures of the metaphor. This is a picture of the screen.
//
//     swift scripts/icon.swift        # or: npm run icon
//
// It writes app/Office/Resources/Assets.xcassets/AppIcon.appiconset/ and then
// LOOK at the result, at 16 points as well as at 1024: an icon that only works
// at full size is an icon nobody ever sees working.

import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

// MARK: - the colours, from Palette.swift

// Written as literals rather than imported, because this script runs with no
// project, no target and no build. They are the dark room's own numbers and the
// menu-bar dot's own amber, and the test at the bottom of this file fails if
// they drift from `Palette`.
let roomTop = (0.105, 0.105, 0.105)     // Palette.raised, dark
let roomBottom = (0.03, 0.03, 0.03)     // just off Palette.ink, dark
let hairline = (0.20, 0.20, 0.20)       // Palette.hairline dark, lifted to survive the bevel
let amber = (1.0, 0.69, 0.13)           // Palette.dotNeedsYou, dark
let quietDot = (0.34, 0.34, 0.34)       // near Palette.faint, dark
let litName = (0.94, 0.94, 0.94)        // Palette.text, dark
let quietName = (0.30, 0.30, 0.30)

// MARK: - the geometry, in fractions of the canvas

// Fractions, so every size is the same drawing rather than ten drawings that
// drift. macOS insets its icon inside the canvas: the squircle is about 82% of
// the tile, and a full-bleed icon sits visibly larger than every neighbour in
// the Dock.
let inset = 0.086
let cornerFraction = 0.185

// Four rows, centred as a block. The lit one is second: dead centre reads as a
// bullseye, and the top reads as a heading rather than as one row among peers.
let rows = 4
let litRow = 1
let rowHeight = 0.108
let rowGap = 0.062
let dotDiameter = 0.072
let litDotDiameter = 0.092
let nameHeight = 0.050
let nameGap = 0.052
let quietNameWidths = [0.30, 0.38, 0.34, 0.24]  // index 1 is the lit row; its width is litNameWidth
let litNameWidth = 0.38

func rgb(_ c: (Double, Double, Double), _ alpha: Double = 1) -> CGColor {
    CGColor(srgbRed: c.0, green: c.1, blue: c.2, alpha: alpha)
}

/// One icon, at one size. Everything is in points of `size`, so 16 and 1024 are
/// the same picture.
func draw(size: Double) -> CGImage? {
    let space = CGColorSpace(name: CGColorSpace.sRGB)!
    guard let ctx = CGContext(data: nil, width: Int(size), height: Int(size),
                              bitsPerComponent: 8, bytesPerRow: 0, space: space,
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { return nil }

    ctx.setAllowsAntialiasing(true)
    ctx.interpolationQuality = .high

    let tile = CGRect(x: size * inset, y: size * inset,
                      width: size * (1 - 2 * inset), height: size * (1 - 2 * inset))
    let radius = size * cornerFraction

    // The room. A rounded rect rather than a circle, because every surface in
    // this app is a rounded rect and the icon should look like the window it
    // opens.
    let room = CGPath(roundedRect: tile, cornerWidth: radius, cornerHeight: radius,
                      transform: nil)
    ctx.saveGState()
    ctx.addPath(room)
    ctx.clip()
    let gradient = CGGradient(colorsSpace: space,
                              colors: [rgb(roomTop), rgb(roomBottom)] as CFArray,
                              locations: [0, 1])!
    ctx.drawLinearGradient(gradient,
                           start: CGPoint(x: 0, y: tile.maxY),
                           end: CGPoint(x: 0, y: tile.minY),
                           options: [])
    ctx.restoreGState()

    // One hairline, the only line this app draws anywhere. Skipped below 32
    // points, where a sub-pixel stroke is mud rather than an edge.
    if size >= 32 {
        ctx.addPath(room)
        ctx.setStrokeColor(rgb(hairline))
        ctx.setLineWidth(max(1, size * 0.004))
        ctx.strokePath()
    }

    // MARK: the roster

    // Below 32 points the four-row roster stops being a roster and becomes a
    // grey smudge with a speck in it: at 16 points each row is two pixels tall
    // and the gaps between them are one. So the small sizes draw a different,
    // simpler picture with the same meaning -- three dots, the middle one lit --
    // rather than the same picture shrunk until it is mud.
    //
    // This is the whole reason the sizes are drawn and not resampled from a
    // single 1024. An exported icon has one drawing in it, and the 16 point
    // version is whatever the resampler made of it.
    if size <= 32 {
        let small = 3
        let diameter = 0.20
        let gap = 0.10
        let block = Double(small) * diameter + Double(small - 1) * gap
        let top = (1 - block) / 2
        for row in 0..<small {
            let centreY = 1 - (top + Double(row) * (diameter + gap) + diameter / 2)
            ctx.setFillColor(rgb(row == 1 ? amber : quietDot))
            ctx.fillEllipse(in: CGRect(x: (0.5 - diameter / 2) * size,
                                       y: (centreY - diameter / 2) * size,
                                       width: diameter * size, height: diameter * size))
        }
        return ctx.makeImage()
    }

    let blockHeight = Double(rows) * rowHeight + Double(rows - 1) * rowGap
    // Optically centred, not arithmetically: the lit row is heavier than the
    // three around it, so the block sits a hair high to stop it pulling down.
    let firstRowTop = (1 - blockHeight) / 2 - 0.008
    let leftEdge = 0.255

    for row in 0..<rows {
        let lit = row == litRow
        let centreY = 1 - (firstRowTop + Double(row) * (rowHeight + rowGap) + rowHeight / 2)
        let diameter = lit ? litDotDiameter : dotDiameter

        // The dot. Every desk in the roster has one and its colour is the whole
        // message, which is why it is the one thing here that is ever coloured.
        let dot = CGRect(x: (leftEdge - diameter / 2) * size,
                         y: (centreY - diameter / 2) * size,
                         width: diameter * size, height: diameter * size)
        ctx.setFillColor(rgb(lit ? amber : quietDot))
        ctx.fillEllipse(in: dot)

        // The name beside it. A bar, not letters: letters at 16 points are a
        // grey smear that reads as dirt.
        let width = lit ? litNameWidth : quietNameWidths[row]
        let height = nameHeight
        let name = CGRect(x: (leftEdge + diameter / 2 + nameGap) * size,
                          y: (centreY - height / 2) * size,
                          width: width * size, height: height * size)
        ctx.setFillColor(rgb(lit ? litName : quietName))
        ctx.addPath(CGPath(roundedRect: name, cornerWidth: name.height / 2,
                           cornerHeight: name.height / 2, transform: nil))
        ctx.fillPath()
    }

    return ctx.makeImage()
}

// MARK: - writing the catalog

// The ten macOS sizes, as Xcode names them. Both scales of each point size,
// because the Dock draws @2x and the Finder list draws @1x, and an icon that
// only exists at @2x is one macOS resamples badly.
let variants: [(idiom: String, point: Double, scale: Int)] = [
    ("mac", 16, 1), ("mac", 16, 2),
    ("mac", 32, 1), ("mac", 32, 2),
    ("mac", 128, 1), ("mac", 128, 2),
    ("mac", 256, 1), ("mac", 256, 2),
    ("mac", 512, 1), ("mac", 512, 2),
]

let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
let catalog = root.appendingPathComponent(
    "app/Office/Resources/Assets.xcassets/AppIcon.appiconset")

guard FileManager.default.fileExists(atPath: root.appendingPathComponent("app/project.yml").path)
else {
    FileHandle.standardError.write(Data("icon: run this from the repo root\n".utf8))
    exit(1)
}

try? FileManager.default.createDirectory(at: catalog, withIntermediateDirectories: true)

var images: [[String: String]] = []
for variant in variants {
    let pixels = variant.point * Double(variant.scale)
    guard let image = draw(size: pixels) else {
        FileHandle.standardError.write(Data("icon: could not draw \(pixels)px\n".utf8))
        exit(1)
    }
    let name = "icon_\(Int(variant.point))x\(Int(variant.point))"
        + (variant.scale == 2 ? "@2x" : "") + ".png"
    let url = catalog.appendingPathComponent(name)
    guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString,
                                                     1, nil) else {
        FileHandle.standardError.write(Data("icon: could not write \(name)\n".utf8))
        exit(1)
    }
    CGImageDestinationAddImage(dest, image, nil)
    CGImageDestinationFinalize(dest)
    images.append([
        "idiom": variant.idiom,
        "size": "\(Int(variant.point))x\(Int(variant.point))",
        "scale": "\(variant.scale)x",
        "filename": name,
    ])
    print("icon: \(name) (\(Int(pixels))px)")
}

let contents: [String: Any] = [
    "images": images,
    "info": ["version": 1, "author": "scripts/icon.swift"],
]
let json = try JSONSerialization.data(withJSONObject: contents,
                                      options: [.prettyPrinted, .sortedKeys])
try json.write(to: catalog.appendingPathComponent("Contents.json"))

// The catalog itself needs one too, or xcodebuild warns about an unversioned
// asset catalog on every single build.
let catalogRoot = catalog.deletingLastPathComponent()
    .appendingPathComponent("Contents.json")
if !FileManager.default.fileExists(atPath: catalogRoot.path) {
    let root: [String: Any] = ["info": ["version": 1, "author": "scripts/icon.swift"]]
    try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted, .sortedKeys])
        .write(to: catalogRoot)
}

print("icon: wrote \(images.count) sizes into Assets.xcassets/AppIcon.appiconset")
print("icon: now LOOK at it, at 16 points as well as at 1024.")

import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// Turning a picture a person picked into something the door will take.
///
/// The door is narrow on purpose: one attachment, `image/png` or `image/jpeg`,
/// and the whole request under 512 KB. A phone screenshot is four megabytes and
/// a photo off a camera roll is a HEIC, so something has to do the shrinking,
/// and the one place it must not live is the view. This is that place: pure
/// bytes in, pure bytes out, no AppKit, no window, no store. That is what makes
/// it provable headlessly, which matters because the failure mode here is a
/// picture that is a hundred kilobytes too big and only says so on a real send.
///
/// The rules, in the order they are applied:
///
///   * the longest side comes down to 1200 px, never up
///   * JPEG at quality 0.8, because that is the size the door was measured for
///   * a PNG stays a PNG only when the JPEG of it would be *larger* and the PNG
///     itself fits, so a screenshot of flat colour does not get smeared for no
///     saving and no transparency is thrown away by accident
///   * anything else (HEIC, TIFF, GIF) becomes a JPEG, because those three are
///     exactly what the door refuses
///   * if it still does not fit, quality and then size come down a rung at a
///     time until it does, and if it never does this answers nil rather than
///     handing the composer something the door will refuse
///
/// The budget is the base64 payload plus the message, not the raw bytes: base64
/// is a third bigger than what it encodes, and a limit measured on the wrong
/// side of that arithmetic is the same as no limit.
public struct PreparedImage: Equatable, Sendable {
    /// What to call it on the wire. Always ends in the extension of `mimeType`.
    public let name: String
    /// `image/png` or `image/jpeg`. Nothing else exists as far as the door goes.
    public let mimeType: String
    /// Strict base64: no `data:` prefix, no line breaks, nothing to strip.
    public let base64: String
    /// The size of the encoded image, which is the number a person recognises.
    /// The base64 of it is a third bigger and is nobody's mental model.
    public let bytes: Int
    /// Pixels, after the downscale. Kept so a readout can be honest about what
    /// was actually sent rather than about what was picked.
    public let width: Int
    public let height: Int

    public init(name: String, mimeType: String, base64: String,
                bytes: Int, width: Int, height: Int) {
        self.name = name
        self.mimeType = mimeType
        self.base64 = base64
        self.bytes = bytes
        self.width = width
        self.height = height
    }

    /// "184 KB". Rounded the way a Finder window rounds, because this number is
    /// read next to a file name and nowhere else.
    public var readable: String {
        if bytes < 1024 { return "\(bytes) bytes" }
        let kb = Double(bytes) / 1024
        if kb < 1000 { return "\(Int(kb.rounded())) KB" }
        return String(format: "%.1f MB", kb / 1024)
    }

    public var isPNG: Bool { mimeType == "image/png" }
}

public enum Attachment {
    /// The longest side of anything that leaves here.
    public static let longestSide = 1200

    /// The room left for base64 plus message. The door's ceiling is 512 KB for
    /// the whole request; this leaves 32 KB of headroom for the JSON around it
    /// and for the fact that a message may still be typed after the picture was
    /// picked. A send refused for eight hundred bytes is the worst possible
    /// place to be exactly right.
    public static let payloadCeiling = 480 * 1024

    /// Every format the pickers offer. HEIC, TIFF and GIF are here because a
    /// person's photo library is full of them and the door takes none of them:
    /// they come in and a JPEG goes out.
    public static let readableTypes: [UTType] = [.png, .jpeg, .heic, .heif, .tiff, .gif]

    /// The one function. `nil` means "this is not a picture I can send", which
    /// the composer says out loud rather than swallowing.
    ///
    /// - Parameters:
    ///   - imageData: the file exactly as it was picked, dropped or pasted.
    ///   - name: what it was called, if anything called it something.
    ///   - messageBytes: how long the message going with it is, in UTF-8 bytes,
    ///     because the two share one ceiling.
    public static func prepare(imageData: Data,
                               name: String = "photo",
                               messageBytes: Int = 0) -> PreparedImage? {
        guard !imageData.isEmpty,
              let source = CGImageSourceCreateWithData(imageData as CFData, nil),
              CGImageSourceGetCount(source) > 0,
              let original = CGImageSourceCreateImageAtIndex(source, 0, nil),
              original.width > 0, original.height > 0
        else { return nil }

        let budget = max(4 * 1024, payloadCeiling - messageBytes)
        let wasPNG = (CGImageSourceGetType(source) as String?) == UTType.png.identifier
        let stem = baseName(name)

        // The first rung: full 1200 px, quality 0.8, and the PNG question.
        let (scaled, didScale) = redraw(original, longest: longestSide, opaque: false)
        if wasPNG {
            let png = didScale ? encode(scaled, as: .png) : imageData
            if let png, fits(png, budget) {
                let jpegOfIt = encode(redrawOpaque(scaled), as: .jpeg, quality: 0.8)
                if jpegOfIt == nil || jpegOfIt!.count >= png.count {
                    return made(png, "image/png", stem + ".png", scaled)
                }
            }
        }

        // Everything else is a JPEG, and it comes down a rung at a time until it
        // fits. The ladder drops quality first because a 1200 px picture at 0.45
        // is still readable and a 600 px one at 0.8 is not.
        let ladder: [(side: Int, quality: CGFloat)] = [
            (longestSide, 0.8), (longestSide, 0.6), (longestSide, 0.45),
            (1000, 0.5), (800, 0.5), (640, 0.45), (480, 0.4), (360, 0.35),
        ]
        for rung in ladder {
            let (image, _) = redraw(original, longest: rung.side, opaque: true)
            guard let jpeg = encode(image, as: .jpeg, quality: rung.quality) else { continue }
            if fits(jpeg, budget) {
                return made(jpeg, "image/jpeg", stem + ".jpg", image)
            }
        }
        return nil
    }

    // MARK: - the arithmetic

    /// base64 is four characters for every three bytes, rounded up. Measuring
    /// the raw length instead is how a payload gets a third bigger than the
    /// ceiling it was checked against.
    public static func base64Length(ofBytes count: Int) -> Int {
        ((count + 2) / 3) * 4
    }

    private static func fits(_ data: Data, _ budget: Int) -> Bool {
        base64Length(ofBytes: data.count) <= budget
    }

    private static func made(_ data: Data, _ mime: String,
                             _ name: String, _ image: CGImage) -> PreparedImage {
        PreparedImage(name: name, mimeType: mime,
                      base64: data.base64EncodedString(),
                      bytes: data.count, width: image.width, height: image.height)
    }

    // MARK: - the pixels

    /// Scaled so the longest side is at most `longest`, never enlarged. The
    /// second half of the answer says whether anything actually happened, which
    /// is what lets an untouched PNG keep its original bytes.
    private static func redraw(_ image: CGImage, longest: Int,
                               opaque: Bool) -> (CGImage, Bool) {
        let side = max(image.width, image.height)
        if side <= longest {
            return opaque ? (redrawOpaque(image), false) : (image, false)
        }
        let ratio = Double(longest) / Double(side)
        let width = max(1, Int((Double(image.width) * ratio).rounded()))
        let height = max(1, Int((Double(image.height) * ratio).rounded()))
        return (draw(image, width: width, height: height, opaque: opaque) ?? image, true)
    }

    /// The same pixels on white. A JPEG has no alpha, so a transparent PNG
    /// turned into one picks a background whether or not anybody chose it, and
    /// black is the one nobody would have chosen.
    private static func redrawOpaque(_ image: CGImage) -> CGImage {
        draw(image, width: image.width, height: image.height, opaque: true) ?? image
    }

    private static func draw(_ image: CGImage, width: Int, height: Int,
                             opaque: Bool) -> CGImage? {
        let info: CGImageAlphaInfo = opaque ? .noneSkipLast : .premultipliedLast
        guard let context = CGContext(data: nil, width: width, height: height,
                                      bitsPerComponent: 8, bytesPerRow: 0,
                                      space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: info.rawValue)
        else { return nil }
        if opaque {
            context.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
            context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        }
        context.interpolationQuality = .high
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        return context.makeImage()
    }

    private static func encode(_ image: CGImage, as type: UTType,
                               quality: CGFloat = 1) -> Data? {
        let out = NSMutableData()
        guard let sink = CGImageDestinationCreateWithData(
            out as CFMutableData, type.identifier as CFString, 1, nil)
        else { return nil }
        let options: [CFString: Any] = [kCGImageDestinationLossyCompressionQuality: quality]
        CGImageDestinationAddImage(sink, image, options as CFDictionary)
        guard CGImageDestinationFinalize(sink) else { return nil }
        return out as Data
    }

    // MARK: - the name

    /// A file name is a thing a person typed on some other machine, and it ends
    /// up in a JSON body. Everything that is not a plain name comes off, the
    /// extension is replaced by the one that matches what was actually encoded,
    /// and something with no name left is called `photo`.
    private static func baseName(_ raw: String) -> String {
        let stem = (raw as NSString).lastPathComponent
        // `.jpeg` is a name that is nothing but an extension, and NSString reads
        // it as a name with no extension at all: without this, a file called
        // that is sent as `jpeg.jpg`.
        let dropped = stem.hasPrefix(".") ? "" : (stem as NSString).deletingPathExtension
        let kept = dropped.unicodeScalars.filter {
            CharacterSet.alphanumerics.contains($0) || $0 == "-" || $0 == "_" || $0 == " "
        }
        let cleaned = String(String.UnicodeScalarView(kept))
            .trimmingCharacters(in: .whitespaces)
        if cleaned.isEmpty { return "photo" }
        return String(cleaned.prefix(64))
    }
}

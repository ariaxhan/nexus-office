import CoreGraphics
import ImageIO
import UniformTypeIdentifiers
import XCTest

/// What actually leaves the machine when a person attaches a picture.
///
/// The door takes one attachment, `image/png` or `image/jpeg`, and under half a
/// megabyte for the whole request. Every one of those is a number, and a number
/// that is only checked by sending a real screenshot to a real agent is a number
/// nobody checks. So the bitmaps here are generated rather than fixtures: a four
/// thousand pixel photograph, a tiny flat PNG, a TIFF, a HEIC, and eight bytes
/// of nonsense, each of which is a different branch of the same function.
final class AttachmentTests: XCTestCase {

    // MARK: - making pictures to feed it

    /// Noise, not flat colour: a solid rectangle compresses to nothing at any
    /// size, so a size test written on one proves the ladder never has to run.
    private func bitmap(width: Int, height: Int, noisy: Bool = true) -> CGImage {
        let context = CGContext(data: nil, width: width, height: height,
                                bitsPerComponent: 8, bytesPerRow: 0,
                                space: CGColorSpaceCreateDeviceRGB(),
                                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        context.setFillColor(CGColor(red: 0.1, green: 0.15, blue: 0.2, alpha: 1))
        context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        guard noisy else { return context.makeImage()! }
        var seed: UInt64 = 0x5eed
        let step = max(2, min(width, height) / 160)
        for y in stride(from: 0, to: height, by: step) {
            for x in stride(from: 0, to: width, by: step) {
                seed = seed &* 6364136223846793005 &+ 1442695040888963407
                let value = Double((seed >> 33) % 1000) / 1000
                context.setFillColor(CGColor(red: value, green: 1 - value,
                                             blue: (value * 3).truncatingRemainder(dividingBy: 1),
                                             alpha: 1))
                context.fill(CGRect(x: x, y: y, width: step, height: step))
            }
        }
        return context.makeImage()!
    }

    private func encoded(_ image: CGImage, as type: UTType, quality: CGFloat = 1) -> Data? {
        let out = NSMutableData()
        guard let sink = CGImageDestinationCreateWithData(
            out as CFMutableData, type.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(sink, image,
                                   [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
        guard CGImageDestinationFinalize(sink) else { return nil }
        return out as Data
    }

    private func decoded(_ ready: PreparedImage) throws -> CGImage {
        let raw = try XCTUnwrap(Data(base64Encoded: ready.base64),
                               "the payload must be base64 something can decode")
        let source = try XCTUnwrap(CGImageSourceCreateWithData(raw as CFData, nil))
        return try XCTUnwrap(CGImageSourceCreateImageAtIndex(source, 0, nil))
    }

    // MARK: - the big one

    func testAFourThousandPixelPhotographComesBackAtTwelveHundredAndFitsTheDoor() throws {
        let huge = try XCTUnwrap(encoded(bitmap(width: 4000, height: 3000), as: .jpeg, quality: 1))
        XCTAssertGreaterThan(huge.count, 512 * 1024, "the input has to be too big to matter")

        let ready = try XCTUnwrap(Attachment.prepare(imageData: huge, name: "IMG_4021.JPG"))
        XCTAssertEqual(ready.mimeType, "image/jpeg")
        XCTAssertEqual(ready.width, 1200)
        XCTAssertEqual(ready.height, 900)
        XCTAssertLessThanOrEqual(ready.base64.count, Attachment.payloadCeiling)
        XCTAssertEqual(ready.name, "IMG_4021.jpg")

        let out = try decoded(ready)
        XCTAssertEqual(out.width, 1200)
        XCTAssertEqual(out.height, 900)
    }

    func testANarrowPictureIsMeasuredOnItsLongSideAndNotItsWidth() throws {
        let tall = try XCTUnwrap(encoded(bitmap(width: 900, height: 3600), as: .jpeg, quality: 1))
        let ready = try XCTUnwrap(Attachment.prepare(imageData: tall))
        XCTAssertEqual(ready.height, 1200)
        XCTAssertEqual(ready.width, 300)
    }

    func testAPictureAlreadySmallerThanTheLimitIsNotBlownUpToReachIt() throws {
        let small = try XCTUnwrap(encoded(bitmap(width: 320, height: 200), as: .jpeg, quality: 0.9))
        let ready = try XCTUnwrap(Attachment.prepare(imageData: small))
        XCTAssertEqual(ready.width, 320)
        XCTAssertEqual(ready.height, 200)
    }

    // MARK: - what stays a PNG and what does not

    func testATinyPNGStaysAPNG() throws {
        let flat = try XCTUnwrap(encoded(bitmap(width: 24, height: 24, noisy: false), as: .png))
        let ready = try XCTUnwrap(Attachment.prepare(imageData: flat, name: "dot.png"))
        XCTAssertEqual(ready.mimeType, "image/png")
        XCTAssertEqual(ready.name, "dot.png")
        XCTAssertEqual(ready.base64, flat.base64EncodedString(),
                       "an untouched PNG is sent exactly as it was picked")
    }

    func testAPhotographSavedAsAPNGBecomesAJPEGBecauseTheJPEGIsSmaller() throws {
        let heavy = try XCTUnwrap(encoded(bitmap(width: 2000, height: 1500), as: .png))
        let ready = try XCTUnwrap(Attachment.prepare(imageData: heavy, name: "screen.png"))
        XCTAssertEqual(ready.mimeType, "image/jpeg")
        XCTAssertEqual(ready.name, "screen.jpg")
        XCTAssertLessThanOrEqual(ready.base64.count, Attachment.payloadCeiling)
    }

    // MARK: - the formats the door refuses

    func testATIFFBecomesAJPEG() throws {
        let tiff = try XCTUnwrap(encoded(bitmap(width: 1600, height: 1200), as: .tiff))
        let ready = try XCTUnwrap(Attachment.prepare(imageData: tiff, name: "scan.tiff"))
        XCTAssertEqual(ready.mimeType, "image/jpeg")
        XCTAssertEqual(ready.name, "scan.jpg")
        XCTAssertEqual(ready.width, 1200)
    }

    func testAGIFBecomesAJPEG() throws {
        let gif = try XCTUnwrap(encoded(bitmap(width: 600, height: 400), as: .gif))
        let ready = try XCTUnwrap(Attachment.prepare(imageData: gif, name: "loop.gif"))
        XCTAssertEqual(ready.mimeType, "image/jpeg")
        XCTAssertEqual(ready.name, "loop.jpg")
    }

    func testAHEICBecomesAJPEG() throws {
        guard let heic = encoded(bitmap(width: 1800, height: 1350), as: .heic) else {
            throw XCTSkip("this machine's ImageIO will not write HEIC")
        }
        let ready = try XCTUnwrap(Attachment.prepare(imageData: heic, name: "IMG_0007.HEIC"))
        XCTAssertEqual(ready.mimeType, "image/jpeg")
        XCTAssertEqual(ready.name, "IMG_0007.jpg")
        XCTAssertEqual(ready.width, 1200)
    }

    // MARK: - the refusals

    func testGarbageBytesAreNotAPicture() {
        XCTAssertNil(Attachment.prepare(imageData: Data("not a picture at all".utf8)))
        XCTAssertNil(Attachment.prepare(imageData: Data()))
        XCTAssertNil(Attachment.prepare(imageData: Data([0x00, 0xff, 0x10, 0x99])))
    }

    func testAPDFIsNotSentAsAPicture() throws {
        // The first bytes of a PDF, which ImageIO can open in other contexts and
        // which the door would refuse as a mime type it never agreed to.
        let pdf = Data("%PDF-1.4\n1 0 obj\n<<>>\nendobj\n".utf8)
        XCTAssertNil(Attachment.prepare(imageData: pdf))
    }

    // MARK: - the budget is shared with the message

    func testTheMessageEatsIntoTheSamePayloadTheDoorMeasures() throws {
        let huge = try XCTUnwrap(encoded(bitmap(width: 3000, height: 2000), as: .jpeg, quality: 1))
        let alone = try XCTUnwrap(Attachment.prepare(imageData: huge))
        let crowded = try XCTUnwrap(Attachment.prepare(imageData: huge, messageBytes: 7000))
        XCTAssertLessThanOrEqual(alone.base64.count, Attachment.payloadCeiling)
        XCTAssertLessThanOrEqual(crowded.base64.count, Attachment.payloadCeiling - 7000)
    }

    func testAPayloadThatCannotBeMadeToFitIsRefusedRatherThanSentTooBig() throws {
        let huge = try XCTUnwrap(encoded(bitmap(width: 4000, height: 4000), as: .jpeg, quality: 1))
        // A budget nothing can reach. The answer is nothing, not a payload the
        // door will refuse thirty seconds later.
        XCTAssertNil(Attachment.prepare(imageData: huge,
                                        messageBytes: Attachment.payloadCeiling - 100))
    }

    func testTheBase64IsStrictWithNoPrefixAndNoWhitespace() throws {
        let jpeg = try XCTUnwrap(encoded(bitmap(width: 900, height: 700), as: .jpeg, quality: 0.9))
        let ready = try XCTUnwrap(Attachment.prepare(imageData: jpeg))
        XCTAssertFalse(ready.base64.hasPrefix("data:"))
        XCTAssertNil(ready.base64.rangeOfCharacter(from: .whitespacesAndNewlines))
        XCTAssertNotNil(Data(base64Encoded: ready.base64))
    }

    func testTheBase64LengthArithmeticIsTheOneTheDoorMeasures() {
        XCTAssertEqual(Attachment.base64Length(ofBytes: 0), 0)
        XCTAssertEqual(Attachment.base64Length(ofBytes: 1), 4)
        XCTAssertEqual(Attachment.base64Length(ofBytes: 3), 4)
        XCTAssertEqual(Attachment.base64Length(ofBytes: 4), 8)
        XCTAssertEqual(Attachment.base64Length(ofBytes: 300_000), 400_000)
    }

    // MARK: - the name that ends up on the wire

    func testANameThatCameFromAnotherMachineIsMadeSafe() throws {
        let jpeg = try XCTUnwrap(encoded(bitmap(width: 200, height: 200), as: .jpeg, quality: 0.8))
        let ready = try XCTUnwrap(
            Attachment.prepare(imageData: jpeg, name: "../../etc/pa$$wd\";drop.jpeg"))
        XCTAssertEqual(ready.name, "pawddrop.jpg")
        XCTAssertFalse(ready.name.contains("/"))
    }

    func testSomethingWithNoNameLeftIsStillCalledSomething() throws {
        let jpeg = try XCTUnwrap(encoded(bitmap(width: 120, height: 120), as: .jpeg, quality: 0.8))
        let ready = try XCTUnwrap(Attachment.prepare(imageData: jpeg, name: "///.jpeg"))
        XCTAssertEqual(ready.name, "photo.jpg")
    }

    func testTheReadableSizeIsTheOneAPersonSeesNextToTheName() {
        XCTAssertEqual(PreparedImage(name: "a", mimeType: "image/jpeg", base64: "",
                                     bytes: 900, width: 1, height: 1).readable, "900 bytes")
        XCTAssertEqual(PreparedImage(name: "a", mimeType: "image/jpeg", base64: "",
                                     bytes: 188_416, width: 1, height: 1).readable, "184 KB")
        XCTAssertEqual(PreparedImage(name: "a", mimeType: "image/jpeg", base64: "",
                                     bytes: 4_300_000, width: 1, height: 1).readable, "4.1 MB")
    }
}

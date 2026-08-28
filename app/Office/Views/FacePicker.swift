import AppKit
import SwiftUI

/// Dressing one desk: the system colour well, and the hex under it.
///
/// The well is `ColorPicker`, which is the Mac's own colour panel and knows
/// about eyedroppers, palettes and the display's colour space; writing a colour
/// wheel by hand here would be a worse one. The field next to it exists because
/// a repo's colour is a thing a person copies out of somewhere else, and a
/// picker with no way to type `#4c8dff` is a picker you have to fight.
///
/// Nothing is clamped. A face is a disc and never a word: no sentence in this
/// app is drawn in this colour, and the state is written out under the name in a
/// colour `Palette` does check. So a face that is hard to see is a face that
/// looks wrong, which the person who typed it can see and undo, rather than a
/// row that quietly cannot be read.
struct FacePicker: View {
    let repo: String
    @Bindable var store: Store
    var faces: FaceBook = .shared

    /// What is in the field, which is not yet what the desk is wearing: a
    /// half-typed `#4c8` must not paint anything.
    @State private var typed: String = ""

    private var current: String { faces.hex(repo: repo) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text(repo)
                    .font(.system(size: 12.5, weight: .medium))
                    .foregroundStyle(Theme.text)
                Text(Faces.name(repo: repo))
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.faint)
            }

            ColorPicker("Face", selection: Binding(
                get: { Color(hex: current) },
                set: { picked in
                    guard let hex = picked.hexString else { return }
                    faces.choose(repo: repo, hex: hex)
                    typed = hex
                }
            ), supportsOpacity: false)
            .font(.system(size: 12))

            HStack(spacing: 6) {
                TextField("#rrggbb", text: $typed)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                    .frame(width: 96)
                    .onSubmit { commit() }
                Button("Set") { commit() }
                    .disabled(Faces.normalise(hex: typed) == nil)
                Spacer(minLength: 0)
                Button("Reset") {
                    faces.reset(repo: repo)
                    typed = current
                }
                .disabled(!faces.isChosen(repo: repo))
            }
            .font(.system(size: 12))
        }
        .padding(14)
        .frame(width: 260)
        .onAppear { typed = current }
    }

    private func commit() {
        guard faces.choose(repo: repo, hex: typed) else { return }
        typed = current
    }
}

extension Color {
    /// `#rrggbb` for a colour that came back out of the system picker.
    ///
    /// Converted through sRGB deliberately: the panel can hand back a colour in
    /// the display's own space or in a named catalogue, and reading its
    /// components without converting gives numbers that are not the ones a
    /// person would paste anywhere else.
    var hexString: String? {
        guard let rgb = NSColor(self).usingColorSpace(.sRGB) else { return nil }
        let channel = { (value: CGFloat) in Int((max(0, min(1, value)) * 255).rounded()) }
        return String(format: "#%02x%02x%02x",
                      channel(rgb.redComponent),
                      channel(rgb.greenComponent),
                      channel(rgb.blueComponent))
    }
}

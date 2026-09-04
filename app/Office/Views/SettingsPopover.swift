import SwiftUI

/// What a person can decide about this window, in the one place they can find.
///
/// Only what exists, and all of it persists. A per-desk colour is not here on
/// purpose: it is a property of one desk, so it lives on that desk's row, and a
/// list of seventy-two colour wells in a popover is a worse way to change one.
///
/// Everything on this panel is a view of the same floor. None of it changes
/// what is polled, what a desk says, or what the door accepts — the server's
/// own knobs are env vars on purpose, and this window cannot reach them.
struct SettingsPopover: View {
    @Bindable var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Settings")
                .officeFont(size: 12, weight: .semibold)
                .foregroundStyle(Theme.text)

            VStack(alignment: .leading, spacing: 6) {
                label("layout")
                Picker("", selection: $store.layout) {
                    ForEach(LayoutPreset.allCases) { preset in
                        Text(preset.label).tag(preset)
                    }
                }
                .labelsHidden()
                .pickerStyle(.segmented)
                Text(layoutBlurb)
                    .officeFont(size: 10.5)
                    .foregroundStyle(Theme.faint)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(width: 220, alignment: .leading)
            }

            VStack(alignment: .leading, spacing: 6) {
                label("typeface")
                Picker("", selection: $store.fontPreset) {
                    ForEach(FontPreset.allCases) { preset in Text(preset.label).tag(preset) }
                }
                .labelsHidden()
                .pickerStyle(.segmented)
            }

            VStack(alignment: .leading, spacing: 6) {
                label("appearance")
                Picker("", selection: $store.appearance) {
                    ForEach(AppearancePreset.allCases) { preset in
                        Text(preset.label).tag(preset)
                    }
                }
                .labelsHidden()
                .pickerStyle(.segmented)
                ColorPicker("Light canvas", selection: colorBinding(for: \Store.lightCanvas))
                ColorPicker("Dark canvas", selection: colorBinding(for: \Store.darkCanvas))
                Button("Reset colors") {
                    store.lightCanvas = Palette.defaultLightCanvas
                    store.darkCanvas = Palette.defaultDarkCanvas
                }
                .buttonStyle(.link)
                .officeFont(size: 11)
            }

            VStack(alignment: .leading, spacing: 6) {
                label("panes")
                Toggle("Bots", isOn: $store.showBots)
                    .toggleStyle(.checkbox)
                    .officeFont(size: 12)
                    .foregroundStyle(Theme.text)
                Toggle("Wall", isOn: $store.showWall)
                    .toggleStyle(.checkbox)
                    .officeFont(size: 12)
                    .foregroundStyle(Theme.text)
            }

            VStack(alignment: .leading, spacing: 6) {
                label("desk order")
                Picker("", selection: $store.deskSort) {
                    ForEach(StateRules.DeskSort.allCases) { order in
                        Text(order.label).tag(order)
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .fixedSize()
            }

            VStack(alignment: .leading, spacing: 6) {
                label("filter")
                Toggle("Only desks that need a person", isOn: $store.needsOnly)
                    .toggleStyle(.checkbox)
                    .officeFont(size: 12)
                    .foregroundStyle(Theme.text)
            }

            Text("Kept between launches. A raised hand is never hidden by any of it.")
                .officeFont(size: 10.5)
                .foregroundStyle(Theme.faint)
                .fixedSize(horizontal: false, vertical: true)
                .frame(width: 220, alignment: .leading)
        }
        .padding(16)
    }

    private func colorBinding(for keyPath: ReferenceWritableKeyPath<Store, String>) -> Binding<Color> {
        Binding(get: { Color(hex: store[keyPath: keyPath]) },
                set: { if let hex = $0.hexRGB { store[keyPath: keyPath] = hex } })
    }

    private var layoutBlurb: String {
        switch store.layout {
        case .focus: return "The roster and one pane."
        case .compare: return "Two panes. Drag a desk from the roster into either one."
        case .minimal: return "The roster alone, filling the window."
        }
    }

    private func label(_ text: String) -> some View {
        Text(text)
            .officeFont(size: 10.5, weight: .semibold)
            .foregroundStyle(Theme.faint)
    }
}

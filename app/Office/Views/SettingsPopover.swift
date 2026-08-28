import SwiftUI

/// What a person can decide about this window, in the one place they can find.
///
/// Only what exists. Layout presets, pane visibility and per-desk colour are
/// #41 and are not built, so there is nothing here pretending to hold them: a
/// control for a thing that does not exist reads as a broken control.
///
/// Everything on this panel is a view of the same floor. None of it changes
/// what is polled, what a desk says, or what the door accepts — the server's
/// own knobs are env vars on purpose, and this window cannot reach them.
struct SettingsPopover: View {
    @Bindable var store: Store

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Settings")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.text)

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
                    .font(.system(size: 12))
                    .foregroundStyle(Theme.text)
            }

            Text("Kept between launches. A raised hand is never hidden by any of it.")
                .font(.system(size: 10.5))
                .foregroundStyle(Theme.faint)
                .fixedSize(horizontal: false, vertical: true)
                .frame(width: 220, alignment: .leading)
        }
        .padding(16)
    }

    private func label(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 10.5, weight: .semibold))
            .foregroundStyle(Theme.faint)
    }
}

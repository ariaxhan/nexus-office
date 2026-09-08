import SwiftUI

@main
struct OfficeMobileApp: App {
    @StateObject private var connection = OfficeConnection()
    @StateObject private var player = OfficePlayer()
    @Environment(\.scenePhase) private var phase
    var body: some Scene {
        WindowGroup {
            Group {
                if connection.configure {
                    ConnectionView(connection: connection)
                } else {
                    OfficeWebView(connection: connection, player: player)
                        .overlay(alignment: .top) {
                            if let error = connection.error {
                                VStack {
                                    Text(error).font(.callout)
                                    Button("Mac connection") { connection.configure = true }
                                }.padding().background(.regularMaterial).clipShape(RoundedRectangle(cornerRadius: 12))
                            }
                        }
                }
            }.onChange(of: phase) { _, next in player.background = next != .active }
        }
    }
}

final class OfficeConnection: ObservableObject {
    @Published var configure = false
    @Published var error: String?
    @Published var address: String
    init() {
        address = UserDefaults.standard.string(forKey: "office.address") ?? (Bundle.main.object(forInfoDictionaryKey: "OfficeURL") as? String) ?? "https://arias-macbook-pro-2.tail4f309a.ts.net"
        if let index = ProcessInfo.processInfo.arguments.firstIndex(of: "--office-url"), ProcessInfo.processInfo.arguments.indices.contains(index + 1) {
            address = ProcessInfo.processInfo.arguments[index + 1]
            if url != nil { UserDefaults.standard.set(address, forKey: "office.address") }
        }
    }
    var url: URL? {
        guard let value = URL(string: address), value.scheme == "https", value.host != nil,
              value.user == nil, value.password == nil else { return nil }
        return value
    }
    func connect() {
        guard url != nil else { error = "Enter your Mac’s HTTPS Office address."; return }
        UserDefaults.standard.set(address, forKey: "office.address")
        error = nil; configure = false
    }
}

struct ConnectionView: View {
    @ObservedObject var connection: OfficeConnection
    var body: some View {
        Form {
            Section("Your Mac holds the work") {
                TextField("Office HTTPS address", text: $connection.address)
                    .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                Text("Connect Tailscale on this phone and keep your Mac awake.").font(.footnote)
                Button("Open Office", action: connection.connect)
                if let error = connection.error { Text(error).foregroundStyle(.red) }
            }
        }
    }
}

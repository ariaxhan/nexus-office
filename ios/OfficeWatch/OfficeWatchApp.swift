import SwiftUI
import WatchConnectivity

@main
struct OfficeWatchApp: App {
    @StateObject private var pulse = WatchPulse()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text("nexus office")
                        .font(.system(.headline, design: .serif))
                    Text(pulse.message != nil ? "OFFLINE · LAST KNOWN" : (pulse.healthy ? "CONNECTED" : "NEEDS ATTENTION"))
                        .font(.system(size: 10, weight: .semibold, design: .monospaced))
                        .foregroundStyle(pulse.message == nil && pulse.healthy ? .green : .orange)
                    HStack(spacing: 8) {
                        count("Needs you", pulse.pending)
                        count("Running", pulse.active)
                    }
                    if pulse.held > 0 {
                        Label("\(pulse.held) held or failed", systemImage: "exclamationmark.circle")
                            .font(.caption2).foregroundStyle(.orange)
                    }
                    if let checked = pulse.checked {
                        Text("Checked \(checked, style: .time)")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                    if let message = pulse.message {
                        Text(message).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                    }
                    Button("Refresh") { pulse.refresh() }
                        .buttonStyle(.bordered)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 6)
            }
            .onAppear { pulse.refresh() }
            .onChange(of: phase) { _, next in if next == .active { pulse.refresh() } }
        }
    }

    private func count(_ title: String, _ value: Int) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(value)").font(.system(size: 30, weight: .regular, design: .serif))
            Text(title).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(8)
        .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
    }
}

final class WatchPulse: NSObject, ObservableObject, WCSessionDelegate {
    @Published private(set) var pending = 0
    @Published private(set) var active = 0
    @Published private(set) var held = 0
    @Published private(set) var healthy = false
    @Published private(set) var checked: Date?
    @Published private(set) var message: String?

    override init() {
        super.init()
        if let cached = UserDefaults.standard.dictionary(forKey: "office.pulse") { apply(cached) }
        if WCSession.isSupported() {
            WCSession.default.delegate = self
            WCSession.default.activate()
        }
    }

    func refresh() {
        guard WCSession.isSupported(), WCSession.default.activationState == .activated,
              WCSession.default.isReachable else {
            Task { await refreshDirectly() }
            return
        }
        WCSession.default.sendMessage(["request": "pulse"], replyHandler: { [weak self] reply in
            DispatchQueue.main.async { self?.apply(reply) }
        }, errorHandler: { [weak self] _ in
            Task { await self?.refreshDirectly() }
        })
    }

    @MainActor private func refreshDirectly() async {
        guard let base = URL(string: "https://office.tail4f309a.ts.net") else { return }
        do {
            async let watch = fetch(base.appending(path: "api/watch"))
            async let runs = fetch(base.appending(path: "api/system/runs"))
            async let health = fetch(base.appending(path: "api/health"))
            let (w, r, h) = try await (watch, runs, health)
            let attention = (w["data"] as? [String: Any])?["attention"] as? [String: Any] ?? [:]
            let asks = (attention["items"] as? [[String: Any]] ?? []).filter {
                ["human-ask", "coordinator-question"].contains($0["kind"] as? String ?? "")
            }
            let rows = r["items"] as? [[String: Any]] ?? []
            apply(["pending": asks.count,
                   "active": rows.filter { $0["state"] as? String == "running" }.count,
                   "held": rows.filter { ["failed", "held", "blocked"].contains($0["state"] as? String ?? "") }.count,
                   "healthy": h["ok"] as? Bool == true,
                   "checked": Date().timeIntervalSince1970])
        } catch {
            message = "Office unavailable; showing last update"
        }
    }

    private func fetch(_ url: URL) async throws -> [String: Any] {
        var request = URLRequest(url: url)
        request.timeoutInterval = 8
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse, response.statusCode == 200,
              data.count < 2_000_000,
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw URLError(.badServerResponse)
        }
        return object
    }

    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        DispatchQueue.main.async { self.refresh() }
    }

    func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
        DispatchQueue.main.async { self.apply(applicationContext) }
    }

    func sessionReachabilityDidChange(_ session: WCSession) {
        if session.isReachable { DispatchQueue.main.async { self.refresh() } }
    }

    private func apply(_ data: [String: Any]) {
        if data["error"] != nil { message = "Office unavailable; showing last update"; return }
        pending = data["pending"] as? Int ?? 0
        active = data["active"] as? Int ?? 0
        held = data["held"] as? Int ?? 0
        healthy = data["healthy"] as? Bool ?? false
        checked = (data["checked"] as? TimeInterval).map(Date.init(timeIntervalSince1970:))
        message = nil
        UserDefaults.standard.set(data, forKey: "office.pulse")
    }
}

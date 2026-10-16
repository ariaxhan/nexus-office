import Foundation
import WatchConnectivity

final class OfficeWatchBridge: NSObject, ObservableObject, WCSessionDelegate {
    private var session: WCSession? { WCSession.isSupported() ? .default : nil }
    private var address: String?

    override init() {
        super.init()
        session?.delegate = self
        session?.activate()
    }

    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        if let address { refresh(address: address) }
    }
    func sessionDidBecomeInactive(_ session: WCSession) {}
    func sessionDidDeactivate(_ session: WCSession) { session.activate() }
    func sessionWatchStateDidChange(_ session: WCSession) {
        if let address { refresh(address: address) }
    }

    func session(_ session: WCSession, didReceiveMessage message: [String: Any], replyHandler: @escaping ([String: Any]) -> Void) {
        guard message["request"] as? String == "pulse" else { replyHandler(["error": "Unknown request"]); return }
        Task {
            let address = UserDefaults.standard.string(forKey: "office.address")
                ?? (Bundle.main.object(forInfoDictionaryKey: "OfficeURL") as? String)
                ?? "https://office.tail4f309a.ts.net"
            let pulse = await Self.pulse(address: address)
            if pulse["error"] == nil { try? session.updateApplicationContext(pulse) }
            replyHandler(pulse)
        }
    }

    func refresh(address: String) {
        self.address = address
        guard let session, session.activationState == .activated, session.isPaired, session.isWatchAppInstalled else { return }
        Task {
            let pulse = await Self.pulse(address: address)
            if pulse["error"] == nil { try? session.updateApplicationContext(pulse) }
        }
    }

    private static func pulse(address: String) async -> [String: Any] {
        guard let base = URL(string: address), base.scheme == "https", base.host != nil,
              base.user == nil, base.password == nil else { return ["error": "Invalid Office address"] }
        do {
            async let watch = json(base, "/api/watch")
            async let runs = json(base, "/api/system/runs")
            async let health = json(base, "/api/health")
            async let feed = json(base, "/api/feed")
            let (w, r, h, f) = try await (watch, runs, health, feed)
            let attention = (w["data"] as? [String: Any])?["attention"] as? [String: Any] ?? [:]
            let pending = (attention["items"] as? [[String: Any]] ?? []).filter {
                ["human-ask", "coordinator-question"].contains($0["kind"] as? String ?? "")
            }
            let runRows = r["items"] as? [[String: Any]] ?? []
            let active = runRows.filter { $0["state"] as? String == "running" }.count
            let held = runRows.filter { ["failed", "held", "blocked"].contains($0["state"] as? String ?? "") }.count
            let headline = (f["items"] as? [[String: Any]])?.first?["title"] as? String ?? "No new updates"
            return ["pending": pending.count, "active": active, "held": held,
                    "healthy": h["ok"] as? Bool == true,
                    "headline": String(headline.prefix(90)), "checked": Date().timeIntervalSince1970]
        } catch { return ["error": "Office unavailable", "checked": Date().timeIntervalSince1970] }
    }

    private static func json(_ base: URL, _ path: String) async throws -> [String: Any] {
        guard let url = URL(string: path, relativeTo: base)?.absoluteURL, url.scheme == base.scheme,
              url.host == base.host else { throw URLError(.badURL) }
        var request = URLRequest(url: url)
        request.timeoutInterval = 8
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse, response.statusCode == 200,
              data.count < 2_000_000,
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw URLError(.badServerResponse) }
        return object
    }
}

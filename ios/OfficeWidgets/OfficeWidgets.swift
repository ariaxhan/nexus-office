import AppIntents
import SwiftUI
import WidgetKit

private let group = "group.app.nexusoffice.shared"
private let fallbackAddress = "https://office.tail4f309a.ts.net"

enum OfficeSlice: String, AppEnum {
    case pending, active, coordinators, feed, health, single
    static var typeDisplayRepresentation = TypeDisplayRepresentation(name: "Office content")
    static var caseDisplayRepresentations: [Self: DisplayRepresentation] = [
        .pending: "Pending work", .active: "Active work", .coordinators: "Coordinators",
        .feed: "Feed", .health: "System health", .single: "Single item"
    ]
}

struct OfficeWidgetIntent: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "Office widget"
    static var description = IntentDescription("Choose an Office view and optional repo, coordinator, or issue.")
    @Parameter(title: "Content") var slice: OfficeSlice?
    @Parameter(title: "Repo or coordinator filter", default: "") var filter: String
    @Parameter(title: "Pinned item, e.g. ariaxhan/nexus-office#329", default: "") var item: String
    init() { slice = .pending; filter = ""; item = "" }
}

struct OfficeEntry: TimelineEntry {
    let date: Date
    let slice: OfficeSlice
    let title: String
    let count: String
    let lines: [String]
    let state: String
    let path: String
    let offline: Bool
    static func placeholder(_ slice: OfficeSlice = .pending) -> Self {
        .init(date: .now, slice: slice, title: "Office", count: "—", lines: ["Checking your work"], state: "Preview", path: "/", offline: false)
    }
}

private enum OfficeFetch {
    static func address() -> URL? {
        let stored = UserDefaults(suiteName: group)?.string(forKey: "office.address") ?? fallbackAddress
        guard let url = URL(string: stored), url.scheme == "https", url.host != nil,
              url.user == nil, url.password == nil else { return nil }
        return url
    }
    static func dictionary(_ path: String) async throws -> [String: Any] {
        guard let base = address(), let url = URL(string: path, relativeTo: base)?.absoluteURL,
              url.host == base.host, url.scheme == base.scheme else { throw URLError(.badURL) }
        var request = URLRequest(url: url); request.timeoutInterval = 8
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse, response.statusCode == 200,
              data.count < 2_000_000,
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw URLError(.badServerResponse) }
        return object
    }
    static func rows(_ value: Any?) -> [[String: Any]] { value as? [[String: Any]] ?? [] }
    static func word(_ object: [String: Any], _ names: String...) -> String {
        for name in names { if let text = object[name] as? String, !text.isEmpty { return text } }
        return ""
    }
    static func matching(_ rows: [[String: Any]], _ filter: String) -> [[String: Any]] {
        let term = filter.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !term.isEmpty else { return rows }
        return rows.filter { String(describing: $0).lowercased().contains(term) }
    }
    static func load(_ intent: OfficeWidgetIntent) async -> OfficeEntry {
        let slice = intent.slice ?? .pending
        let date = Date()
        do {
            switch slice {
            case .pending:
                let data = try await dictionary("/api/watch")
                let attention = (data["data"] as? [String: Any])?["attention"] as? [String: Any] ?? [:]
                let rows = matching(rows(attention["items"]), intent.filter)
                let lines = rows.prefix(3).map { row in
                    let item = row["item"] as? [String: Any] ?? [:]
                    return word(item, "title", "action", "decision").prefix(85).description
                }
                return .init(date: date, slice: slice, title: "Pending work", count: "\(rows.count)", lines: lines.isEmpty ? ["Nothing waiting on you"] : lines, state: "Needs your attention", path: "/#watch", offline: false)
            case .active:
                let data = try await dictionary("/api/system/runs")
                let rows = matching(rows(data["items"]), intent.filter)
                let running = rows.filter { word($0, "state") == "running" }
                let failed = rows.filter { ["failed", "held", "blocked"].contains(word($0, "state")) }
                let lines = Array(running.prefix(2) + failed.prefix(2)).map { "\(word($0, "state").capitalized) · \(word($0, "title", "plan").prefix(65))" }
                return .init(date: date, slice: slice, title: "Active work", count: "\(running.count)", lines: lines.isEmpty ? ["No active runs"] : lines, state: "\(failed.count) failed or held", path: "/#work", offline: false)
            case .coordinators:
                let data = try await dictionary("/api/coordinators")
                let rows = matching(rows(data["coordinators"]), intent.filter)
                let lines = rows.prefix(4).map { "\(word($0, "name", "id")) · \(word($0, "health", "state"))" }
                return .init(date: date, slice: slice, title: "Coordinators", count: "\(rows.count)", lines: lines.isEmpty ? ["No coordinators"] : lines, state: "Current status", path: "/#coordinator", offline: false)
            case .feed:
                let data = try await dictionary("/api/feed")
                let rows = matching(rows(data["items"]), intent.filter)
                let lines = rows.prefix(3).map { word($0, "title").prefix(85).description }
                return .init(date: date, slice: slice, title: "Office feed", count: "\(rows.count)", lines: lines.isEmpty ? ["No recent updates"] : lines, state: "Latest updates", path: "/#feed", offline: false)
            case .health:
                let data = try await dictionary("/api/health")
                let healthy = data["ok"] as? Bool == true
                return .init(date: date, slice: slice, title: "System health", count: healthy ? "UP" : "DOWN", lines: ["Office door \(healthy ? "responding" : "unhealthy")", "Snapshot \(word(data, "snapshot_at"))"], state: healthy ? "Connected" : "Needs attention", path: "/#system", offline: false)
            case .single:
                let target = intent.item.trimmingCharacters(in: .whitespacesAndNewlines)
                if let mark = target.lastIndex(of: "#"), let number = Int(target[target.index(after: mark)...]), number > 0 {
                    let repo = String(target[..<mark])
                    let parts = repo.split(separator: "/")
                    let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
                    guard parts.count == 2, parts.allSatisfy({ !$0.isEmpty && $0.unicodeScalars.allSatisfy(allowed.contains) }) else { throw URLError(.badURL) }
                    let safe = repo.addingPercentEncoding(withAllowedCharacters: allowed) ?? repo
                    let data = try await dictionary("/api/github/detail?repo=\(safe)&number=\(number)")
                    let id = try JSONSerialization.data(withJSONObject: ["repo": repo, "number": number, "kind": "issues"])
                    let detail = try JSONSerialization.data(withJSONObject: ["kind": "github", "id": String(decoding: id, as: UTF8.self)])
                    var destination = URLComponents()
                    destination.path = "/"
                    destination.queryItems = [URLQueryItem(name: "detail", value: String(decoding: detail, as: UTF8.self))]
                    destination.fragment = "issues"
                    return .init(date: date, slice: slice, title: "Pinned item", count: "#\(number)", lines: [word(data, "title").prefix(90).description], state: word(data, "state").capitalized, path: destination.string ?? "/#issues", offline: false)
                }
                let data = try await dictionary("/api/coordinators")
                guard let row = rows(data["coordinators"]).first(where: { word($0, "id").lowercased() == target.lowercased() }) else {
                    return .init(date: date, slice: slice, title: "Pinned item", count: "—", lines: ["Set an issue or coordinator in widget settings"], state: "Needs configuration", path: "/", offline: false)
                }
                return .init(date: date, slice: slice, title: word(row, "name", "id"), count: "●", lines: [word(row, "health", "state")], state: "Coordinator", path: "/#coordinator?id=\(target)", offline: false)
            }
        } catch {
            return .init(date: date, slice: slice, title: "Office offline", count: "—", lines: ["Connect Tailscale and check the Mac"], state: "Last checked \(date.formatted(date: .omitted, time: .shortened))", path: "/", offline: true)
        }
    }
}

struct OfficeProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> OfficeEntry { .placeholder() }
    func snapshot(for configuration: OfficeWidgetIntent, in context: Context) async -> OfficeEntry { await OfficeFetch.load(configuration) }
    func timeline(for configuration: OfficeWidgetIntent, in context: Context) async -> Timeline<OfficeEntry> {
        let entry = await OfficeFetch.load(configuration)
        return Timeline(entries: [entry], policy: .after(Date().addingTimeInterval(15 * 60)))
    }
}

struct OfficeWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: OfficeEntry
    private var destination: URL? {
        var components = URLComponents()
        components.scheme = "nexusoffice"
        components.host = "open"
        components.queryItems = [URLQueryItem(name: "path", value: entry.path)]
        return components.url
    }
    var body: some View {
        let content = VStack(alignment: .leading, spacing: 5) {
            Text(entry.title.uppercased()).font(.system(size: 10, weight: .semibold, design: .monospaced)).tracking(1).foregroundStyle(.secondary)
            Text(entry.count).font(.system(size: family == .systemSmall ? 35 : 44, weight: .regular, design: .serif)).minimumScaleFactor(0.7)
            if family != .systemSmall {
                ForEach(Array(entry.lines.prefix(family == .systemLarge ? 4 : 2).enumerated()), id: \.offset) { _, line in
                    Text(line).font(.system(size: 12)).lineLimit(1)
                }
            } else { Text(entry.lines.first ?? entry.state).font(.system(size: 11)).lineLimit(2) }
            Spacer(minLength: 0)
            Text(entry.offline ? "OFFLINE" : "CHECKED \(entry.date.formatted(date: .omitted, time: .shortened))")
                .font(.system(size: 9, design: .monospaced)).foregroundStyle(entry.offline ? .red : .secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .widgetURL(destination)
        switch family {
        case .accessoryCircular: Text(entry.count).font(.system(.title3, design: .rounded)).widgetURL(destination)
        case .accessoryInline: Text("\(entry.title): \(entry.count) · \(entry.state)").widgetURL(destination)
        case .accessoryRectangular: VStack(alignment: .leading) { Text(entry.title).font(.caption).bold(); Text("\(entry.count) · \(entry.state)").font(.caption2).lineLimit(2) }.widgetURL(destination)
        default: content.containerBackground(Color(red: 0.97, green: 0.96, blue: 0.93), for: .widget)
        }
    }
}

struct OfficeWidget: Widget {
    let kind = "app.nexusoffice.widgets"
    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: kind, intent: OfficeWidgetIntent.self, provider: OfficeProvider()) { OfficeWidgetView(entry: $0) }
            .configurationDisplayName("Nexus Office")
            .description("Pending work, active runs, coordinators, feed, health, or a pinned item.")
            .supportedFamilies([.systemSmall, .systemMedium, .systemLarge, .accessoryCircular, .accessoryRectangular, .accessoryInline])
    }
}

@main struct OfficeWidgets: WidgetBundle { var body: some Widget { OfficeWidget() } }

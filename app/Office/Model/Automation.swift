import Foundation

/// The automation, as one page: what the schedule is, whether it is running,
/// what it touched, and where what it said is.
///
/// Every value here was measured by the server when it built the snapshot. This
/// file decodes and nothing else: it does no arithmetic on ages, picks no
/// headline, and never decides whether a state is bad. All of that lives in
/// `client/automation.py`, so the phone page and this app say the same sentence
/// about the same machine rather than two sentences that agree until they do
/// not.
///
/// Decoding is lenient the whole way down, for the reason the rest of
/// `Models.swift` is: a server that has not learned a field yet must draw a page
/// with a gap in it, never no page at all.

public struct Automation: Decodable, Equatable {
    public var state: String = "unknown"
    public var headline: String = ""
    /// How the whole thing works, in the order the events happen. From the
    /// server, so there is one copy of the explanation rather than one here and
    /// one in the phone's JavaScript.
    public var how: [String] = []
    public var schedule: Schedule = Schedule()
    public var now: Current = Current()
    public var trigger: Trigger = Trigger()
    public var reached: Reached = Reached()
    public var activity: [Activity] = []
    /// How many rows the server left off the end. Drawn, always: a list capped
    /// in silence reads as "that is everything that happened".
    public var activityDropped: Int = 0

    public var isEmpty: Bool { headline.isEmpty && activity.isEmpty }

    /// Whether anything on this page wants a person. The kill switch does not:
    /// somebody switched it off, which is a decision and not a fault.
    public var needsSomebody: Bool {
        schedule.overdue || schedule.deferring || now.stalePid != nil
            || !trigger.blockedBy.isEmpty || state == "unreadable"
    }

    enum CodingKeys: String, CodingKey {
        case state, headline, how, schedule, now, trigger, reached, activity
        case activityDropped = "activity_dropped"
    }

    public init() {}

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        state = c.str(.state) ?? "unknown"
        headline = c.str(.headline) ?? ""
        how = c.list(.how, String.self)
        schedule = ((try? c.decodeIfPresent(Schedule.self, forKey: .schedule)) ?? nil) ?? Schedule()
        now = ((try? c.decodeIfPresent(Current.self, forKey: .now)) ?? nil) ?? Current()
        trigger = ((try? c.decodeIfPresent(Trigger.self, forKey: .trigger)) ?? nil) ?? Trigger()
        reached = ((try? c.decodeIfPresent(Reached.self, forKey: .reached)) ?? nil) ?? Reached()
        // One row at a time: a receipt that will not decode loses its own row,
        // never the whole history.
        activity = c.list(.activity, Lenient<Activity>.self).compactMap(\.value)
        activityDropped = c.int(.activityDropped) ?? 0
    }

    // MARK: - when it looks

    public struct Schedule: Decodable, Equatable {
        public var every: String = ""
        public var nextIn: String = ""
        public var overdue: Bool = false
        public var lateBy: String = ""
        public var enabled: Bool?
        public var killSwitch: Bool = false
        public var power: String = "unknown"
        /// dispatch exits before doing anything on battery, so an hourly
        /// deferral and an idle hour look identical without this.
        public var deferring: Bool = false
        public var lastFullRun: String = ""
        public var lastFullRunAge: Int?

        enum CodingKeys: String, CodingKey {
            case every, overdue, power, deferring
            case nextIn = "next_in", lateBy = "late_by", killSwitch = "kill_switch"
            case enabled
            case lastFullRun = "last_full_run", lastFullRunAge = "last_full_run_age_s"
        }

        public init() {}

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            every = c.str(.every) ?? ""
            nextIn = c.str(.nextIn) ?? ""
            overdue = c.bool(.overdue) ?? false
            lateBy = c.str(.lateBy) ?? ""
            enabled = c.bool(.enabled)
            killSwitch = c.bool(.killSwitch) ?? false
            power = c.str(.power) ?? "unknown"
            deferring = c.bool(.deferring) ?? false
            lastFullRun = c.str(.lastFullRun) ?? ""
            lastFullRunAge = c.int(.lastFullRunAge)
        }

        /// "every hour, next look in 43 minutes". One line, and it says overdue
        /// rather than "in 0 minutes" when the scheduler has stopped firing.
        public var line: String {
            var parts: [String] = []
            if !every.isEmpty { parts.append("every \(every)") }
            if overdue {
                parts.append("overdue by \(lateBy.isEmpty ? "a while" : lateBy)")
            } else if !nextIn.isEmpty {
                parts.append("next look \(nextIn)")
            }
            return parts.joined(separator: ", ")
        }
    }

    // MARK: - whether it is looking right now

    public struct Current: Decodable, Equatable {
        public var running: Bool = false
        public var forHowLong: String = ""
        public var doing: String = ""
        public var lastSaid: String = ""
        public var lastSaidAge: Int?
        public var detail: String = ""
        /// A pid file naming a process that is not a live dispatch.sh. Never
        /// reported as a run: that is the whole point of it being here.
        public var stalePid: String?

        enum CodingKeys: String, CodingKey {
            case running, doing, detail
            case forHowLong = "for"
            case lastSaid = "last_said", lastSaidAge = "last_said_age_s"
            case stalePid = "stale_pid"
        }

        public init() {}

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            running = c.bool(.running) ?? false
            forHowLong = c.str(.forHowLong) ?? ""
            doing = c.str(.doing) ?? ""
            lastSaid = c.str(.lastSaid) ?? ""
            lastSaidAge = c.int(.lastSaidAge)
            detail = c.str(.detail) ?? ""
            let stale = (try? c.decodeIfPresent(StalePid.self, forKey: .stalePid)) ?? nil
            stalePid = stale?.why.isEmpty == false ? stale?.why : nil
        }

        private struct StalePid: Decodable {
            var why: String = ""
            enum CodingKeys: String, CodingKey { case why }
            init(from decoder: Decoder) throws {
                why = (try decoder.container(keyedBy: CodingKeys.self)).str(.why) ?? ""
            }
        }
    }

    // MARK: - the other way in

    public struct Trigger: Decodable, Equatable {
        public var state: String = "unknown"
        public var reachable: Bool = false
        public var deliveries: Int?
        public var today: Int?
        public var lastAt: String = ""
        public var lastAge: Int?
        public var runsToday: Int?
        public var queued: Int = 0
        public var detail: String = ""
        /// The one actionable line when nothing is arriving. Empty when nothing
        /// is proven to be blocking, never absent.
        public var blockedBy: String = ""

        enum CodingKeys: String, CodingKey {
            case state, reachable, deliveries, today, queued, detail
            case lastAt = "last_at", lastAge = "last_age_s"
            case runsToday = "runs_today", blockedBy = "blocked_by"
        }

        public init() {}

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            state = c.str(.state) ?? "unknown"
            reachable = c.bool(.reachable) ?? false
            deliveries = c.int(.deliveries)
            today = c.int(.today)
            lastAt = c.str(.lastAt) ?? ""
            lastAge = c.int(.lastAge)
            runsToday = c.int(.runsToday)
            queued = c.int(.queued) ?? 0
            detail = c.str(.detail) ?? ""
            blockedBy = c.str(.blockedBy) ?? ""
        }
    }

    // MARK: - what it reached

    public struct Reached: Decodable, Equatable {
        public var repos: Int?
        public var receipts: Int?
        public var window: String = "24h"
        public var state: String = "unknown"

        enum CodingKeys: String, CodingKey { case repos, receipts, window, state }

        public init() {}

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            repos = c.int(.repos)
            receipts = c.int(.receipts)
            window = c.str(.window) ?? "24h"
            state = c.str(.state) ?? "unknown"
        }
    }

    // MARK: - one thing the runner did

    public struct Activity: Decodable, Equatable, Identifiable {
        public var at: String = ""
        public var ago: String = ""
        public var repo: String = ""
        public var issue: String = ""
        public var outcome: String = ""
        public var tone: String = ""
        /// What that outcome means, in words a person who has never read
        /// dispatch.sh can act on.
        public var means: String = ""
        public var detail: String = ""
        public var title: String = ""
        public var issueURL: String = ""
        /// The exact comment the runner left, when the office knows it. Empty
        /// when a human replied after it, which moves the last comment: a deep
        /// link to somebody else's words labelled as the runner's is worse than
        /// no deep link.
        public var commentURL: String = ""
        public var commentAt: String = ""

        /// Stable across polls: a receipt is one repo, one issue, one moment.
        public var id: String { "\(repo)#\(issue)@\(at)" }

        public var hasComment: Bool { !commentURL.isEmpty }
        public var link: URL? { URL(string: hasComment ? commentURL : issueURL) }

        enum CodingKeys: String, CodingKey {
            case at, ago, repo, issue, outcome, tone, means, detail, title
            case issueURL = "issue_url", commentURL = "comment_url", commentAt = "comment_at"
        }

        public init() {}

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            at = c.str(.at) ?? ""
            ago = c.str(.ago) ?? ""
            repo = c.str(.repo) ?? ""
            issue = c.str(.issue) ?? ""
            outcome = c.str(.outcome) ?? ""
            tone = c.str(.tone) ?? ""
            means = c.str(.means) ?? ""
            detail = c.str(.detail) ?? ""
            title = c.str(.title) ?? ""
            issueURL = c.str(.issueURL) ?? ""
            commentURL = c.str(.commentURL) ?? ""
            commentAt = c.str(.commentAt) ?? ""
        }
    }
}

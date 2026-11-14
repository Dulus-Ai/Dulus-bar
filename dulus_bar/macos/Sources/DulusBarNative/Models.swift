import Foundation
#if canImport(Combine)
import Combine
#endif

struct AgentEvent: Decodable {
    let agent: String
    let type: String
    let sessionID: String
    let requestID: String?
    let payload: [String: JSONValue]
    /// When it happened (epoch seconds); the hub stamps every relayed event.
    let ts: Double?

    enum CodingKeys: String, CodingKey {
        case agent, type, payload, ts
        case sessionID = "session_id"
        case requestID = "request_id"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        agent = try container.decodeIfPresent(String.self, forKey: .agent) ?? ""
        type = try container.decodeIfPresent(String.self, forKey: .type) ?? "message"
        sessionID = try container.decodeIfPresent(String.self, forKey: .sessionID) ?? "default"
        requestID = try container.decodeIfPresent(String.self, forKey: .requestID)
        payload = (try? container.decodeIfPresent([String: JSONValue].self, forKey: .payload)) ?? [:]
        ts = try? container.decodeIfPresent(Double.self, forKey: .ts)
    }

    var isHealth: Bool {
        ["ping", "health", "pong"].contains(type)
            || ["", "_health", "health", "VibeHealth", "DulusHealth"].contains(agent)
            || ["_health", "health"].contains(sessionID)
    }

    /// First non-empty string among the given payload keys.
    func text(_ keys: String...) -> String? {
        for key in keys {
            if let value = payload[key]?.string, !value.isEmpty { return value }
        }
        return nil
    }
}

enum JSONValue: Codable, Equatable {
    case string(String), number(Double), bool(Bool), object([String: JSONValue])
    case array([JSONValue]), null

    init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let v = try? value.decode(Bool.self) { self = .bool(v) }
        else if let v = try? value.decode(Double.self) { self = .number(v) }
        else if let v = try? value.decode(String.self) { self = .string(v) }
        else if let v = try? value.decode([String: JSONValue].self) { self = .object(v) }
        else if let v = try? value.decode([JSONValue].self) { self = .array(v) }
        else { throw DecodingError.dataCorruptedError(in: value, debugDescription: "Unsupported JSON value") }
    }

    func encode(to encoder: Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case .string(let v): try value.encode(v)
        case .number(let v): try value.encode(v)
        case .bool(let v): try value.encode(v)
        case .object(let v): try value.encode(v)
        case .array(let v): try value.encode(v)
        case .null: try value.encodeNil()
        }
    }

    var string: String? {
        switch self {
        case .string(let value): return value
        case .number(let value):
            if let integer = Int(exactly: value) { return String(integer) }  // 1e19 must not trap
            return String(value)
        case .bool(let value): return String(value)
        default: return nil
        }
    }

    var bool: Bool? {
        if case .bool(let value) = self { return value }
        return nil
    }

    var double: Double? {
        if case .number(let value) = self { return value }
        return nil
    }

    /// A process id: a whole number, or a string of digits (as the hub accepts).
    var pid: Int32? {
        switch self {
        case .number(let value): return Int32(exactly: value).flatMap { $0 > 0 ? $0 : nil }
        case .string(let value): return Int32(value).flatMap { $0 > 0 ? $0 : nil }
        default: return nil
        }
    }
}

/// A tool call waiting for Allow / Deny.
struct PermissionRequest: Identifiable, Equatable {
    let requestID: String
    let agent: String
    let sessionID: String
    var tool = ""
    var args = ""
    var detail = ""
    var canAlways = false
    var alwaysLabel = ""

    var id: String { requestID }
    var sessionKey: String { "\(agent)::\(sessionID)" }
    var label: String { callText(tool, args) }
    /// The first line of what's asked: the command, the path…
    var detailLine: String {
        let source = detail.isEmpty ? args : detail
        let first = source.split(whereSeparator: { $0.isNewline }).first.map(String.init) ?? ""
        return oneLine(first, limit: 160)
    }
    /// The card's headline: the tool, or a wrapped agent's own prompt line.
    var title: String { tool.isEmpty ? "Run a tool?" : revealHidden(tool) }
    /// The card's code block: the full command / diff excerpt, verbatim
    /// except for characters that would hide part of it.
    var code: String {
        revealHidden(detail.isEmpty ? args : detail).trimmingCharacters(in: .whitespacesAndNewlines)
    }
    /// What "Always allow" would save, as shown before you grant it.
    var alwaysText: String { revealHidden(alwaysLabel) }

    init(requestID: String, agent: String, sessionID: String) {
        self.requestID = requestID
        self.agent = agent
        self.sessionID = sessionID
    }

    init(event: AgentEvent) {
        self.init(
            requestID: event.requestID ?? event.payload["request_id"]?.string ?? UUID().uuidString,
            agent: event.agent,
            sessionID: event.sessionID
        )
        tool = oneLine(event.payload["tool"]?.string ?? "")
        args = oneLine(event.payload["args"]?.string ?? "", limit: 300)
        detail = String((event.payload["detail"]?.string ?? "").prefix(4000))
        canAlways = event.payload["can_always"]?.bool ?? false
        alwaysLabel = oneLine(event.payload["always_label"]?.string ?? "", limit: 120)
    }

    init?(snapshot item: [String: JSONValue]) {
        guard let requestID = item["request_id"]?.string, let agent = item["agent"]?.string,
              let sessionID = item["session_id"]?.string else { return nil }
        self.init(requestID: requestID, agent: agent, sessionID: sessionID)
        tool = item["tool"]?.string ?? ""
        args = item["args"]?.string ?? ""
        detail = item["detail"]?.string ?? ""
        canAlways = item["can_always"]?.bool ?? false
        alwaysLabel = item["always_label"]?.string ?? ""
    }
}

struct Session: Identifiable, Equatable {
    let agent: String
    let sessionID: String
    var status = "running"
    var message = "Started"
    var model = ""
    var context = ""
    var title = ""
    /// The agent's process (for click-to-focus), and which machine it's on.
    var pid: Int32?
    var pidHost = ""
    var startedAt = Date()
    var statusSince = Date()
    var updatedAt = Date()
    var lastTS: Double = 0

    var id: String { "\(agent)::\(sessionID)" }
    var isActive: Bool { status == "running" || status == "waiting" }

    init(agent: String, sessionID: String) {
        self.agent = agent
        self.sessionID = sessionID
    }

    init?(snapshot item: [String: JSONValue]) {
        guard let agent = item["agent"]?.string, let sessionID = item["session_id"]?.string else { return nil }
        self.init(agent: agent, sessionID: sessionID)
        status = item["status"]?.string ?? status
        message = item["message"]?.string ?? ""
        model = item["model"]?.string ?? ""
        context = item["ctx"]?.string ?? ""
        title = item["title"]?.string ?? ""
        pid = item["pid"]?.pid
        pidHost = item["pid_host"]?.string ?? ""
        lastTS = item["last_ts"]?.double ?? 0
        func date(_ key: String) -> Date? { item[key]?.double.map { Date(timeIntervalSince1970: $0) } }
        startedAt = date("started") ?? startedAt
        statusSince = date("status_since") ?? statusSince
        updatedAt = date("updated") ?? updatedAt
    }

    /// Attention first: a prompt you must answer, then failures, then live work.
    var rank: Int {
        switch status {
        case "waiting": return 0
        case "error": return 1
        case "running": return 2
        case "done": return 3
        case "idle": return 4
        default: return 9
        }
    }
}

/// What the hub sends a freshly connected island.
struct Snapshot: Decodable {
    let sessions: [[String: JSONValue]]
    let permissions: [[String: JSONValue]]
}

struct Envelope: Decodable {
    let type: String?
    let agent: String?
    let hostID: String?

    enum CodingKeys: String, CodingKey {
        case type, agent
        case hostID = "host_id"
    }
}

/// How long the island peeks open on its own.
enum RevealTime {
    /// An agent finished, failed or asked for something.
    static let peek = 4.5
    /// After an answer: just long enough to see it went through.
    static let settle = 1.6
}

@MainActor
final class IslandModel: ObservableObject {
    @Published private(set) var sessions: [String: Session] = [:]
    @Published private(set) var permissions: [PermissionRequest] = []
    @Published var hoverExpanded = false
    @Published var eventExpanded = false
    @Published private(set) var connected = false
    /// The session a brief reveal is about: it just finished, failed, or got answered.
    @Published private(set) var spotlight: String?
    /// Height of the expanded card as laid out; the panel hit-tests exactly it.
    @Published var contentHeight: CGFloat = 0

    /// The hub's machine (from its welcome): only its pids can be focused.
    var hostID = ""
    var sendDecision: ((PermissionRequest, Bool, Bool) -> Void)?
    var focusSession: ((Session) -> Void)?

    private var collapseTask: Task<Void, Never>?

    /// The head of the queue: the request on screen.
    var permission: PermissionRequest? { permissions.first }
    var expanded: Bool { hoverExpanded || eventExpanded || permission != nil }
    var foremost: Session? {
        if let request = permission, let session = sessions[request.sessionKey] { return session }
        let values = sessions.values
        let active = values.filter(\.isActive)
        let pool = active.isEmpty ? Array(values) : active
        return pool.max { $0.updatedAt < $1.updatedAt }
    }

    /// Panel order: the spotlight, then attention first, newest within a status.
    var ordered: [Session] {
        sessions.values.sorted { a, b in
            if a.id == spotlight || b.id == spotlight { return a.id == spotlight && b.id != spotlight }
            if a.rank != b.rank { return a.rank < b.rank }
            if a.startedAt != b.startedAt { return a.startedAt > b.startedAt }
            return a.id < b.id
        }
    }

    /// Rows for the expanded card: just the spotlight during a peek, every
    /// session (up to `limit`) while you hover.
    func rows(limit: Int) -> [Session] {
        if !hoverExpanded, let key = spotlight, let session = sessions[key] { return [session] }
        return Array(ordered.prefix(limit))
    }

    /// One light per live (or failed) session for the collapsed island.
    var lights: [Session] {
        Array(ordered.filter { $0.isActive || $0.status == "error" }.sorted { $0.rank < $1.rank }.prefix(6))
    }

    func setConnected(_ value: Bool) { connected = value }

    func load(_ snapshot: Snapshot) {
        let hadRequests = !permissions.isEmpty
        var loaded: [String: Session] = [:]
        for item in snapshot.sessions {
            if let session = Session(snapshot: item) { loaded[session.id] = session }
        }
        sessions = loaded
        permissions = snapshot.permissions.compactMap { PermissionRequest(snapshot: $0) }
        if spotlight.map({ loaded[$0] == nil }) ?? false { spotlight = nil }
        if !permissions.isEmpty || hadRequests { reveal() }
    }

    private func dropPermissions(_ key: String, where match: (PermissionRequest) -> Bool = { _ in true }) {
        permissions.removeAll { $0.sessionKey == key && match($0) }
    }

    func consume(_ event: AgentEvent) {
        guard !event.isHealth, event.type != "decision" else { return }
        let hadRequests = !permissions.isEmpty
        var revealed = false
        defer {
            // Answered somewhere else, or its agent left: let the island settle
            // instead of staying stuck open.
            if hadRequests && permissions.isEmpty && !revealed { reveal(for: RevealTime.settle) }
        }
        let key = "\(event.agent)::\(event.sessionID)"
        switch event.type {
        case "disconnected", "session_ended", "session_removed":
            // Gone for good: never resurrect it as "running".
            sessions[key] = nil
            dropPermissions(key)
            if spotlight == key { spotlight = nil }
            return
        case "request_resolved":
            let requestID = event.requestID ?? event.payload["request_id"]?.string
            permissions.removeAll { $0.requestID == requestID }
            normalize(key, outcome: event.payload["outcome"]?.string)
            return
        default:
            break
        }

        let now = Date()
        var session = sessions[key] ?? Session(agent: event.agent, sessionID: event.sessionID)
        let ts = event.ts ?? now.timeIntervalSince1970
        if event.type != "tool_request" && session.lastTS > 0 && ts < session.lastTS {
            // A late event from a hook process that lost a race: never step back
            // (same rule as the hub's reducer). Requests are never dropped.
            return
        }
        session.lastTS = max(session.lastTS, ts)
        let payload = event.payload
        session.updatedAt = now
        if let model = payload["model"]?.string, !model.isEmpty { session.model = model }
        if let context = payload["ctx"]?.string, !context.isEmpty { session.context = context }
        if let title = event.text("title", "project") {
            session.title = title
        } else if session.title.isEmpty, let cwd = event.text("cwd") {
            session.title = URL(fileURLWithPath: cwd).lastPathComponent
        }
        if let pid = payload["pid"]?.pid { session.pid = pid }
        if let host = event.text("pid_host") { session.pidHost = host }
        let text = event.text("text", "message")
        let tool = payload["tool"]?.string ?? ""
        let args = payload["args"]?.string ?? ""

        func set(_ status: String, _ message: String) {
            if session.status != status { session.statusSince = now }
            session.status = status
            session.message = message
        }

        switch event.type {
        case "session_started":
            set("running", text ?? "Started")
        case "prompt":
            dropPermissions(key)
            set("running", text ?? "Working…")
        case "tool_started":
            set("running", callText(tool, args, " · "))
        case "tool_finished":
            // The tool ran, so its prompt was answered in the terminal.
            dropPermissions(key) { $0.tool == oneLine(tool) && (args.isEmpty || $0.args == oneLine(args, limit: 300)) }
            set("running", callText(tool, args, " · "))
        case "tool_request":
            let request = PermissionRequest(event: event)
            if !permissions.contains(where: { $0.requestID == request.requestID }) { permissions.append(request) }
        case "tool_approved", "tool_denied":
            dropPermissions(key)
            set("running", event.type == "tool_approved" ? "Approved" : "Denied")
        case "idle":
            dropPermissions(key)
            set("done", text ?? "Waiting for you")
        case "completed":
            dropPermissions(key)
            set("done", text ?? "Done")
        case "error":
            set("error", text ?? "Error")
        default:
            set("running", text ?? session.message)
        }
        sessions[key] = session
        normalize(key, outcome: nil)
        if event.type == "tool_request" {
            reveal()
            revealed = true
        } else if ["completed", "idle", "error"].contains(event.type) {
            reveal(spotlight: key)
            revealed = true
        }
    }

    /// A session is "waiting" exactly while it has a pending request.
    private func normalize(_ key: String, outcome: String?) {
        guard var session = sessions[key] else { return }
        if let pending = permissions.first(where: { $0.sessionKey == key }) {
            if session.status != "waiting" { session.statusSince = Date() }
            session.status = "waiting"
            session.message = pending.label.isEmpty ? "Needs approval" : pending.label
        } else {
            if session.status == "waiting" {
                session.status = "running"
                session.statusSince = Date()
            }
            if outcome == "approved" { session.message = "Approved" }
            if outcome == "denied" { session.message = "Denied" }
        }
        sessions[key] = session
    }

    /// Peek the island open for a while (it stays open anyway while a request
    /// is up, or while you hover it).
    func reveal(for seconds: Double = RevealTime.peek, spotlight key: String? = nil) {
        collapseTask?.cancel()
        eventExpanded = true
        spotlight = key
        collapseTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(seconds))
            guard !Task.isCancelled else { return }
            self?.eventExpanded = false
            self?.spotlight = nil
        }
    }

    /// Answer a request. Bound to `requestID` when given: a click lands on the
    /// card it was made on, never on whichever request slid into its place.
    func decide(_ approved: Bool, always: Bool = false, requestID: String? = nil) {
        let target: PermissionRequest?
        if let requestID {
            target = permissions.first { $0.requestID == requestID }
        } else {
            target = permission
        }
        guard let request = target else { return }
        // "Always" only for a request that offered it, and only with an allow.
        sendDecision?(request, approved, always && approved && request.canAlways)
        permissions.removeAll { $0.requestID == request.requestID }
        normalize(request.sessionKey, outcome: approved ? "approved" : "denied")
        if var session = sessions[request.sessionKey] {
            session.updatedAt = Date()
            sessions[request.sessionKey] = session
        }
        reveal(for: RevealTime.settle, spotlight: sessions[request.sessionKey] == nil ? nil : request.sessionKey)
    }

    /// Bring up the terminal or editor a session runs in (if it's on this Mac).
    func focus(_ session: Session) {
        focusSession?(session)
    }

    /// A session's pid, when it belongs to this machine.
    func localPID(of session: Session) -> Int32? {
        guard let pid = session.pid else { return nil }
        return session.pidHost.isEmpty || hostID.isEmpty || session.pidHost == hostID ? pid : nil
    }
}

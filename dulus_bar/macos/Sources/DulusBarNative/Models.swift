import Foundation

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
}

/// Collapse whitespace to one line and truncate with an ellipsis.
func oneLine(_ text: String, limit: Int = 200) -> String {
    let flat = text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    return flat.count <= limit ? flat : String(flat.prefix(limit - 1)) + "…"
}

/// One clean representation of a tool call; never the same text twice.
func callText(_ tool: String, _ args: String, _ separator: String = "  ") -> String {
    let tool = tool.trimmingCharacters(in: .whitespacesAndNewlines)
    let args = args.trimmingCharacters(in: .whitespacesAndNewlines)
    if args.isEmpty { return tool }
    if tool.isEmpty { return args }
    if args.contains(tool) || tool.contains(args) { return args.count >= tool.count ? args : tool }
    return tool + separator + args
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
    /// The line the card shows under the tool name: the command, the path…
    var detailLine: String {
        let source = detail.isEmpty ? args : detail
        let first = source.split(whereSeparator: { $0.isNewline }).first.map(String.init) ?? ""
        return oneLine(first, limit: 160)
    }

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
        detail = event.payload["detail"]?.string ?? ""
        canAlways = event.payload["can_always"]?.bool ?? false
        alwaysLabel = event.payload["always_label"]?.string ?? ""
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
    var message = "started"
    var model = ""
    var context = ""
    var title = ""
    var updatedAt = Date()
    var lastTS: Double = 0

    var id: String { "\(agent)::\(sessionID)" }

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
        lastTS = item["last_ts"]?.double ?? 0
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
}

@MainActor
final class IslandModel: ObservableObject {
    @Published private(set) var sessions: [String: Session] = [:]
    @Published private(set) var permissions: [PermissionRequest] = []
    @Published var hoverExpanded = false
    @Published var eventExpanded = false
    @Published private(set) var connected = false

    private var collapseTask: Task<Void, Never>?
    var sendDecision: ((PermissionRequest, Bool, Bool) -> Void)?

    /// The head of the queue: the request on screen.
    var permission: PermissionRequest? { permissions.first }
    var expanded: Bool { hoverExpanded || eventExpanded || permission != nil }
    var foremost: Session? {
        if let request = permission, let session = sessions[request.sessionKey] { return session }
        let values = sessions.values
        let active = values.filter { ["running", "waiting"].contains($0.status) }
        let pool = active.isEmpty ? Array(values) : active
        return pool.max { $0.updatedAt < $1.updatedAt }
    }

    /// Height of the black card. The panel controller hit-tests the same
    /// number, so the hover area always matches what's drawn.
    func cardHeight(notchHeight: CGFloat) -> CGFloat {
        guard expanded else { return notchHeight }
        var height = notchHeight + 65  // top padding, the session row, bottom padding
        if let request = permission {
            height += 68  // the tool line and the buttons
            if !request.detailLine.isEmpty { height += 28 }
        }
        return height
    }

    func setConnected(_ value: Bool) { connected = value }

    func load(_ snapshot: Snapshot) {
        var loaded: [String: Session] = [:]
        for item in snapshot.sessions {
            if let session = Session(snapshot: item) { loaded[session.id] = session }
        }
        sessions = loaded
        permissions = snapshot.permissions.compactMap { PermissionRequest(snapshot: $0) }
        if !permissions.isEmpty { reveal(sticky: true) }
    }

    private func dropPermissions(_ key: String, where match: (PermissionRequest) -> Bool = { _ in true }) {
        permissions.removeAll { $0.sessionKey == key && match($0) }
    }

    func consume(_ event: AgentEvent) {
        guard !event.isHealth, event.type != "decision" else { return }
        let hadRequests = !permissions.isEmpty
        defer {
            // Answered somewhere else, or its agent left: let the island settle
            // instead of staying stuck open.
            if hadRequests && permissions.isEmpty { reveal() }
        }
        let key = "\(event.agent)::\(event.sessionID)"
        switch event.type {
        case "disconnected", "session_ended", "session_removed":
            // Gone for good: never resurrect it as "running".
            sessions[key] = nil
            dropPermissions(key)
            return
        case "request_resolved":
            let requestID = event.requestID ?? event.payload["request_id"]?.string
            permissions.removeAll { $0.requestID == requestID }
            normalize(key, outcome: event.payload["outcome"]?.string)
            return
        default:
            break
        }

        var session = sessions[key] ?? Session(agent: event.agent, sessionID: event.sessionID)
        let ts = event.ts ?? Date().timeIntervalSince1970
        if event.type != "tool_request" && session.lastTS > 0 && ts < session.lastTS {
            // A late event from a hook process that lost a race: never step back
            // (same rule as the hub's reducer). Requests are never dropped.
            return
        }
        session.lastTS = max(session.lastTS, ts)
        let payload = event.payload
        session.updatedAt = Date()
        if let model = payload["model"]?.string, !model.isEmpty { session.model = model }
        if let context = payload["ctx"]?.string, !context.isEmpty { session.context = context }
        if let title = event.text("title", "project") {
            session.title = title
        } else if session.title.isEmpty, let cwd = event.text("cwd") {
            session.title = URL(fileURLWithPath: cwd).lastPathComponent
        }
        let text = event.text("text", "message")
        let tool = payload["tool"]?.string ?? ""
        let args = payload["args"]?.string ?? ""

        switch event.type {
        case "session_started":
            session.status = "running"; session.message = text ?? "Started"
        case "prompt":
            dropPermissions(key)
            session.status = "running"; session.message = text ?? "Working…"
        case "tool_started":
            session.status = "running"; session.message = callText(tool, args, " · ")
        case "tool_finished":
            // The tool ran, so its prompt was answered in the terminal.
            dropPermissions(key) { $0.tool == oneLine(tool) && (args.isEmpty || $0.args == oneLine(args, limit: 300)) }
            session.status = "running"; session.message = callText(tool, args, " · ")
        case "tool_request":
            let request = PermissionRequest(event: event)
            if !permissions.contains(where: { $0.requestID == request.requestID }) { permissions.append(request) }
        case "tool_approved", "tool_denied":
            dropPermissions(key)
            session.status = "running"; session.message = event.type == "tool_approved" ? "Approved" : "Denied"
        case "idle":
            dropPermissions(key)
            session.status = "done"; session.message = text ?? "Waiting for you"
        case "completed":
            dropPermissions(key)
            session.status = "done"; session.message = text ?? "Done"
        case "error":
            session.status = "error"; session.message = text ?? "Error"
        default:
            session.status = "running"
            if let text { session.message = text }
        }
        sessions[key] = session
        normalize(key, outcome: nil)
        if event.type == "tool_request" {
            reveal(sticky: true)
        } else if ["completed", "idle", "error"].contains(event.type) {
            reveal()
        }
    }

    /// A session is "waiting" exactly while it has a pending request.
    private func normalize(_ key: String, outcome: String?) {
        guard var session = sessions[key] else { return }
        if let pending = permissions.first(where: { $0.sessionKey == key }) {
            session.status = "waiting"
            session.message = pending.label.isEmpty ? "Needs approval" : pending.label
        } else {
            if session.status == "waiting" { session.status = "running" }
            if outcome == "approved" { session.message = "Approved" }
            if outcome == "denied" { session.message = "Denied" }
        }
        sessions[key] = session
    }

    func reveal(sticky: Bool = false) {
        collapseTask?.cancel()
        eventExpanded = true
        guard !sticky else { return }
        collapseTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(4.5))
            guard !Task.isCancelled else { return }
            self?.eventExpanded = false
        }
    }

    func decide(_ approved: Bool, always: Bool = false) {
        guard let request = permission else { return }
        sendDecision?(request, approved, always)
        permissions.removeAll { $0.requestID == request.requestID }
        normalize(request.sessionKey, outcome: approved ? "approved" : "denied")
        if var session = sessions[request.sessionKey] {
            session.updatedAt = Date()
            sessions[request.sessionKey] = session
        }
        reveal()
    }
}

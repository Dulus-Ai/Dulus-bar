import Foundation

struct AgentEvent: Decodable {
    let agent: String
    let type: String
    let sessionID: String
    let payload: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case agent, type, payload
        case sessionID = "session_id"
    }

    var isHealth: Bool {
        ["ping", "health", "pong"].contains(type)
            || ["", "_health", "health", "VibeHealth", "DulusHealth"].contains(agent)
            || ["_health", "health"].contains(sessionID)
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
        case .number(let value): return value.rounded() == value ? String(Int(value)) : String(value)
        case .bool(let value): return String(value)
        default: return nil
        }
    }
}

struct Session: Identifiable, Equatable {
    let agent: String
    let sessionID: String
    var status = "running"
    var message = "started"
    var model = ""
    var context = ""
    var updatedAt = Date()

    var id: String { "\(agent)::\(sessionID)" }
}

@MainActor
final class IslandModel: ObservableObject {
    @Published private(set) var sessions: [String: Session] = [:]
    @Published private(set) var permission: AgentEvent?
    @Published var hoverExpanded = false
    @Published var eventExpanded = false
    @Published private(set) var connected = false

    private var collapseTask: Task<Void, Never>?
    var sendDecision: ((AgentEvent, Bool) -> Void)?

    var expanded: Bool { hoverExpanded || eventExpanded || permission != nil }
    var foremost: Session? {
        let values = sessions.values
        let active = values.filter { ["running", "waiting"].contains($0.status) }
        let pool = active.isEmpty ? Array(values) : active
        let waiting = pool.filter { $0.status == "waiting" }
        return (waiting.isEmpty ? pool : waiting).max { $0.updatedAt < $1.updatedAt }
    }

    func setConnected(_ value: Bool) { connected = value }

    func consume(_ event: AgentEvent) {
        guard !event.isHealth, event.type != "decision" else { return }
        let key = "\(event.agent)::\(event.sessionID)"
        var session = sessions[key] ?? Session(agent: event.agent, sessionID: event.sessionID)
        let payload = event.payload
        session.updatedAt = Date()
        if let model = payload["model"]?.string, !model.isEmpty { session.model = model }
        if let context = payload["ctx"]?.string, !context.isEmpty { session.context = context }

        switch event.type {
        case "session_started":
            session.status = "running"; session.message = "started"
        case "message":
            session.status = "running"
            session.message = payload["text"]?.string ?? payload["message"]?.string ?? session.message
        case "tool_request":
            session.status = "waiting"
            session.message = payload["tool"]?.string ?? payload["message"]?.string ?? "Permission required"
            permission = event
        case "tool_approved":
            session.status = "running"; session.message = "approved"
            if permission?.sessionID == event.sessionID { permission = nil }
        case "tool_denied":
            session.status = "running"; session.message = "denied"
            if permission?.sessionID == event.sessionID { permission = nil }
        case "completed":
            session.status = "done"; session.message = payload["message"]?.string ?? "completed"
        case "error":
            session.status = "error"; session.message = payload["message"]?.string ?? "error"
        default:
            session.status = "running"
            session.message = payload["text"]?.string ?? payload["message"]?.string ?? session.message
        }
        sessions[key] = session
        reveal(sticky: event.type == "tool_request")
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

    func decide(_ approved: Bool) {
        guard let event = permission else { return }
        sendDecision?(event, approved)
        permission = nil
        if var session = sessions["\(event.agent)::\(event.sessionID)"] {
            session.status = "running"
            session.message = approved ? "approved" : "denied"
            session.updatedAt = Date()
            sessions[session.id] = session
        }
        reveal()
    }
}

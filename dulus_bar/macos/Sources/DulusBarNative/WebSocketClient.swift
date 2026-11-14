import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

@MainActor
final class WebSocketClient {
    private let url: URL
    private let token: String
    private unowned let model: IslandModel
    private var socket: URLSessionWebSocketTask?
    private var worker: Task<Void, Never>?
    private var stopped = false

    /// `token` is the per-run hub token: only an authenticated island may
    /// answer a request. It arrives in the environment, never on argv.
    init(url: URL, token: String, model: IslandModel) {
        self.url = url
        self.token = token
        self.model = model
    }

    func start() {
        guard worker == nil else { return }
        stopped = false
        worker = Task { [weak self] in
            await self?.connectionLoop()
        }
    }

    func stop() {
        stopped = true
        worker?.cancel()
        worker = nil
        socket?.cancel(with: .goingAway, reason: nil)
        socket = nil
        model.setConnected(false)
    }

    func sendDecision(for request: PermissionRequest, approved: Bool, always: Bool) {
        send([
            "agent": request.agent,
            "type": "decision",
            "session_id": request.sessionID,
            "request_id": request.requestID,
            "payload": ["approved": approved, "always": always],
        ])
    }

    private var hello: [String: Any] {
        [
            "type": "hello",
            "v": 2,
            "role": "ui",
            "token": token,
            "client": "dulus-bar-native",
            // Older hubs treat this as a health probe instead of an agent.
            "agent": "_health",
            "session_id": "_health",
        ]
    }

    private func send(_ message: [String: Any], on task: URLSessionWebSocketTask? = nil) {
        guard let socket = task ?? socket,
              let data = try? JSONSerialization.data(withJSONObject: message),
              let text = String(data: data, encoding: .utf8) else { return }
        Task {
            do {
                try await socket.send(.string(text))
            } catch {
                model.setConnected(false)
            }
        }
    }

    private func connectionLoop() async {
        var delay: Double = 0.35
        while !stopped && !Task.isCancelled {
            let task = URLSession.shared.webSocketTask(with: url)
            socket = task
            task.resume()
            send(hello, on: task)

            do {
                try await receiveMessages(from: task)
                delay = 0.35
            } catch {
                if stopped || Task.isCancelled { break }
            }

            task.cancel(with: .goingAway, reason: nil)
            if socket === task { socket = nil }
            model.setConnected(false)

            do {
                try await Task.sleep(for: .seconds(delay))
            } catch {
                break
            }
            delay = min(delay * 1.8, 4.0)
        }
    }

    private func receiveMessages(from task: URLSessionWebSocketTask) async throws {
        while !stopped && !Task.isCancelled {
            let message = try await task.receive()
            let data: Data
            switch message {
            case .data(let bytes): data = bytes
            case .string(let text): data = Data(text.utf8)
            @unknown default: continue
            }
            handle(data)
        }
    }

    /// One message from the hub (internal so tests can feed recorded traffic).
    func handle(_ data: Data) {
        let decoder = JSONDecoder()
        guard let envelope = try? decoder.decode(Envelope.self, from: data) else { return }
        switch envelope.type {
        case "welcome":
            model.setConnected(true)
        case "snapshot":
            if let snapshot = try? decoder.decode(Snapshot.self, from: data) { model.load(snapshot) }
        case "error" where envelope.agent == nil:
            // The hub's own error (e.g. a bad token: it closes us next). An
            // agent's "error" event carries an agent and goes to the model.
            model.setConnected(false)
        default:
            if let event = try? decoder.decode(AgentEvent.self, from: data) { model.consume(event) }
        }
    }
}

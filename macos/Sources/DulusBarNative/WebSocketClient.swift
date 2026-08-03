import Foundation

@MainActor
final class WebSocketClient {
    private let url: URL
    private unowned let model: IslandModel
    private var socket: URLSessionWebSocketTask?
    private var worker: Task<Void, Never>?
    private var stopped = false

    init(url: URL, model: IslandModel) {
        self.url = url
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

    func sendDecision(for event: AgentEvent, approved: Bool) {
        guard let socket else { return }
        let message: [String: Any] = [
            "agent": event.agent,
            "type": "decision",
            "session_id": event.sessionID,
            "payload": ["approved": approved],
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: message) else { return }
        Task {
            do {
                try await socket.send(.data(data))
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
            model.setConnected(true)

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
            guard let event = try? JSONDecoder().decode(AgentEvent.self, from: data) else {
                continue
            }
            model.consume(event)
        }
    }
}

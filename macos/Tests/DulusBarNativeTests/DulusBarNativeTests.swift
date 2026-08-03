import XCTest
@testable import DulusBarNative

final class DulusBarNativeTests: XCTestCase {
    func testNotchFrameIsTopCentered() {
        let metrics = NotchMetrics(
            width: 185,
            height: 32,
            screenFrame: CGRect(x: 0, y: 0, width: 1512, height: 982)
        )
        XCTAssertEqual(metrics.frame, CGRect(x: 663.5, y: 950, width: 185, height: 32))
    }

    @MainActor
    func testWaitingSessionGetsPriority() throws {
        let model = IslandModel()
        let started = try decode("""
        {"agent":"Dulus","type":"session_started","session_id":"a","payload":{}}
        """)
        let waiting = try decode("""
        {"agent":"Claude","type":"tool_request","session_id":"b","payload":{"tool":"Bash"}}
        """)
        model.consume(started)
        model.consume(waiting)
        XCTAssertEqual(model.foremost?.agent, "Claude")
        XCTAssertEqual(model.foremost?.status, "waiting")
        XCTAssertNotNil(model.permission)
    }

    private func decode(_ json: String) throws -> AgentEvent {
        try JSONDecoder().decode(AgentEvent.self, from: Data(json.utf8))
    }
}

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
        model.consume(try decode("""
        {"agent":"Dulus","type":"session_started","session_id":"a","payload":{}}
        """))
        model.consume(try decode("""
        {"agent":"Claude","type":"tool_request","session_id":"b","request_id":"r1","payload":{"tool":"Bash"}}
        """))
        XCTAssertEqual(model.foremost?.agent, "Claude")
        XCTAssertEqual(model.foremost?.status, "waiting")
        XCTAssertNotNil(model.permission)
    }

    @MainActor
    func testRequestsQueueInsteadOfOverwriting() throws {
        let model = IslandModel()
        var sent: [(String, Bool, Bool)] = []
        model.sendDecision = { request, approved, always in sent.append((request.requestID, approved, always)) }
        model.consume(try decode("""
        {"agent":"Claude Code","type":"tool_request","session_id":"a","request_id":"r1",
         "payload":{"tool":"Bash","args":"rm -rf build/","detail":"rm -rf build/\\n# clean","can_always":true}}
        """))
        model.consume(try decode("""
        {"agent":"Codex","type":"tool_request","session_id":"b","request_id":"r2","payload":{"tool":"Edit"}}
        """))
        XCTAssertEqual(model.permissions.map(\.requestID), ["r1", "r2"])
        XCTAssertEqual(model.permission?.detailLine, "rm -rf build/")
        XCTAssertTrue(model.permission?.canAlways ?? false)
        model.decide(true, always: true)
        XCTAssertEqual(sent.first?.0, "r1")
        XCTAssertEqual(model.permission?.requestID, "r2")
        XCTAssertEqual(model.sessions["Claude Code::a"]?.message, "Approved")
    }

    @MainActor
    func testDisconnectedSessionsStayGone() throws {
        let model = IslandModel()
        model.consume(try decode("""
        {"agent":"Dulus","type":"session_started","session_id":"a","payload":{}}
        """))
        model.consume(try decode("""
        {"agent":"Dulus","type":"disconnected","session_id":"a","payload":{}}
        """))
        XCTAssertNil(model.sessions["Dulus::a"])
    }

    @MainActor
    func testAnsweredElsewhereDropsTheCard() throws {
        let model = IslandModel()
        model.consume(try decode("""
        {"agent":"Claude Code","type":"tool_request","session_id":"a","request_id":"r1","payload":{"tool":"Bash"}}
        """))
        model.consume(try decode("""
        {"agent":"Claude Code","type":"request_resolved","session_id":"a","request_id":"r1",
         "payload":{"outcome":"cancelled"}}
        """))
        XCTAssertNil(model.permission)
        XCTAssertEqual(model.sessions["Claude Code::a"]?.status, "running")
    }

    @MainActor
    func testSnapshotRestoresSessionsAndRequests() throws {
        let model = IslandModel()
        let snapshot = try JSONDecoder().decode(Snapshot.self, from: Data("""
        {"type":"snapshot","sessions":[{"agent":"Claude Code","session_id":"a","status":"waiting",
          "message":"Bash  ls","model":"opus 4.5","ctx":"82k","title":"dulus-bar"}],
         "permissions":[{"request_id":"r1","agent":"Claude Code","session_id":"a","tool":"Bash","args":"ls",
          "detail":"","can_always":false,"always_label":""}]}
        """.utf8))
        model.load(snapshot)
        XCTAssertEqual(model.sessions["Claude Code::a"]?.title, "dulus-bar")
        XCTAssertEqual(model.permission?.label, "Bash  ls")
        XCTAssertTrue(model.expanded)
    }

    @MainActor
    func testLateHookEventsNeverStepBack() throws {
        let model = IslandModel()
        model.consume(try decode("""
        {"agent":"Claude Code","type":"completed","session_id":"a","ts":200,"payload":{"text":"Done"}}
        """))
        model.consume(try decode("""
        {"agent":"Claude Code","type":"tool_started","session_id":"a","ts":100,"payload":{"tool":"Bash"}}
        """))
        XCTAssertEqual(model.sessions["Claude Code::a"]?.status, "done")
        model.consume(try decode("""
        {"agent":"Claude Code","type":"tool_request","session_id":"a","ts":100,"request_id":"r1","payload":{"tool":"Bash"}}
        """))
        XCTAssertEqual(model.permission?.requestID, "r1")  // requests are never dropped
    }

    @MainActor
    func testAgentErrorsAreShownAndHugeNumbersDontCrash() throws {
        let model = IslandModel()
        let client = WebSocketClient(url: URL(string: "ws://127.0.0.1:1")!, token: "", model: model)
        client.handle(Data(#"{"type":"welcome","v":2}"#.utf8))
        client.handle(Data(#"{"agent":"CI","type":"error","session_id":"b","payload":{"text":1e19}}"#.utf8))
        XCTAssertEqual(model.sessions["CI::b"]?.status, "error")
        XCTAssertTrue(model.connected)  // an agent's error is not the hub's
        client.handle(Data(#"{"type":"error","error":"unauthorized"}"#.utf8))
        XCTAssertFalse(model.connected)
    }

    /// Messages exactly as the Python hub sends them to an island (recorded
    /// from `dulus_bar.server`, ids and clocks normalized): the contract
    /// between the hub and this app.
    private static let hubTraffic = #"""
{"type":"welcome","v":2,"server":"dulus-bar/test","role":"ui","authed":true,"host_id":"linux:test"}
{"type":"snapshot","v":2,"sessions":[{"agent":"Claude Code","session_id":"s1","status":"running","message":"Started","model":"opus 4.5","ctx":"","cwd":"/home/kev/dulus-bar","title":"dulus-bar","pid":1,"pid_host":"linux:test","terminal_hint":"","started":1792632500.0,"updated":1792632500.0,"status_since":1792632500.0,"last_ts":1792632500.0}],"permissions":[]}
{"agent":"Claude Code","type":"tool_request","session_id":"s1","payload":{"tool":"Bash","args":"rm -rf build/","detail":"rm -rf build/\n# clean","can_always":true,"always_label":"Bash(rm:*)"},"ts":1792632500.0,"request_id":"r1"}
{"agent":"Dulus","type":"session_started","session_id":"d1","payload":{"model":"kimi-k2.5","ctx":"38%"},"ts":1792632500.0}
{"agent":"Dulus","type":"tool_request","session_id":"d1","payload":{"tool":"Allow this action? (Y/n)","args":""},"request_id":"r2","ts":1792632500.0}
{"type":"request_resolved","agent":"Claude Code","session_id":"s1","request_id":"r1","payload":{"outcome":"approved","by":"ui"},"ts":1792632500.0}
{"type":"request_resolved","agent":"Dulus","session_id":"d1","request_id":"r2","payload":{"outcome":"cancelled","by":"agent"},"ts":1792632500.0}
{"agent":"Dulus","type":"disconnected","session_id":"d1","payload":{},"ts":1792632500.0}
{"agent":"Claude Code","type":"completed","session_id":"s1","payload":{"text":"Rebuilt"},"ts":1792632500.0}
"""#

    @MainActor
    func testReplaysRealHubTraffic() throws {
        let model = IslandModel()
        let client = WebSocketClient(url: URL(string: "ws://127.0.0.1:1")!, token: "", model: model)
        let lines = Self.hubTraffic.split(separator: "\n").map(String.init)
        func step(_ index: Int) { client.handle(Data(lines[index].utf8)) }

        step(0)
        XCTAssertTrue(model.connected)
        step(1)
        XCTAssertEqual(model.sessions["Claude Code::s1"]?.title, "dulus-bar")
        XCTAssertEqual(model.sessions["Claude Code::s1"]?.model, "opus 4.5")
        step(2)
        XCTAssertEqual(model.permission?.requestID, "r1")
        XCTAssertEqual(model.permission?.detailLine, "rm -rf build/")
        XCTAssertEqual(model.permission?.alwaysLabel, "Bash(rm:*)")
        XCTAssertEqual(model.sessions["Claude Code::s1"]?.status, "waiting")
        step(3); step(4)
        XCTAssertEqual(model.permissions.map(\.requestID), ["r1", "r2"])
        XCTAssertEqual(model.sessions["Dulus::d1"]?.context, "38%")
        step(5)
        XCTAssertEqual(model.permission?.requestID, "r2")
        XCTAssertEqual(model.sessions["Claude Code::s1"]?.status, "running")
        XCTAssertEqual(model.sessions["Claude Code::s1"]?.message, "Approved")
        step(6)
        XCTAssertNil(model.permission)
        step(7)
        XCTAssertNil(model.sessions["Dulus::d1"])
        step(8)
        XCTAssertEqual(model.sessions["Claude Code::s1"]?.status, "done")
        XCTAssertEqual(model.sessions["Claude Code::s1"]?.message, "Rebuilt")
    }

    private func decode(_ json: String) throws -> AgentEvent {
        try JSONDecoder().decode(AgentEvent.self, from: Data(json.utf8))
    }
}

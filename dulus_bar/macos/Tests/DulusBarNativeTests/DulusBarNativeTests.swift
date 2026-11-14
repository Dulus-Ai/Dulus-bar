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

    // MARK: - Hold Allow for "always"

    func testAClickAllowsOnce() {
        var gate = HoldGate(canAlways: true)
        gate.press()
        XCTAssertEqual(gate.release(inside: true), .allow)
        XCTAssertEqual(gate.release(inside: true), .none)  // one press, one answer
    }

    func testAHoldAlwaysAllowsAndItsReleaseIsNotAClick() {
        var gate = HoldGate(canAlways: true)
        gate.press()
        XCTAssertEqual(gate.holdElapsed(buttonDown: true), .always)
        XCTAssertEqual(gate.holdElapsed(buttonDown: true), .none)
        // The release must not answer whatever card slid in underneath.
        XCTAssertEqual(gate.release(inside: true), .none)
    }

    func testHoldingWithoutAnAlwaysOfferIsJustASlowClick() {
        var gate = HoldGate(canAlways: false)
        gate.press()
        XCTAssertEqual(gate.holdElapsed(buttonDown: true), .none)
        XCTAssertEqual(gate.release(inside: true), .allow)
    }

    func testDraggingOffTheButtonBacksOut() {
        var gate = HoldGate(canAlways: true)
        gate.press()
        gate.leave()
        XCTAssertEqual(gate.holdElapsed(buttonDown: true), .none)
        XCTAssertEqual(gate.release(inside: true), .none)
        gate.press()  // the next press starts clean
        XCTAssertEqual(gate.release(inside: false), .none)  // released elsewhere: nothing
    }

    func testAPressWhoseEndGotLostNeverBecomesAlways() {
        var gate = HoldGate(canAlways: true)
        gate.press()
        XCTAssertEqual(gate.holdElapsed(buttonDown: false), .none)
        XCTAssertEqual(gate.release(inside: true), .allow)
    }

    @MainActor
    func testDecisionsLandOnTheRequestTheyWereMadeOn() throws {
        let model = IslandModel()
        var sent: [(String, Bool, Bool)] = []
        model.sendDecision = { request, approved, always in sent.append((request.requestID, approved, always)) }
        model.consume(try decode("""
        {"agent":"Claude Code","type":"tool_request","session_id":"a","request_id":"r1","payload":{"tool":"Bash","can_always":true}}
        """))
        model.consume(try decode("""
        {"agent":"Dulus","type":"tool_request","session_id":"b","request_id":"r2","payload":{"tool":"Bash"}}
        """))
        // r1 is answered in the terminal while "always" is being confirmed on its card:
        model.consume(try decode("""
        {"agent":"Claude Code","type":"request_resolved","session_id":"a","request_id":"r1","payload":{"outcome":"cancelled"}}
        """))
        model.decide(true, always: true, requestID: "r1")
        XCTAssertTrue(sent.isEmpty)  // not r2, which slid into r1's place
        XCTAssertEqual(model.permission?.requestID, "r2")
        model.decide(true, always: true, requestID: "r2")
        XCTAssertEqual(sent.first?.0, "r2")
        XCTAssertEqual(sent.first?.2, false)  // r2 never offered "always"
    }

    // MARK: - Sessions

    @MainActor
    func testSessionsOrderAttentionFirstAndKeepTheirProcess() throws {
        let model = IslandModel()
        model.consume(try decode("""
        {"agent":"Codex","type":"session_started","session_id":"c","payload":{"pid":4242,"pid_host":"darwin:mac"}}
        """))
        model.consume(try decode("""
        {"agent":"Claude Code","type":"error","session_id":"e","payload":{"text":"boom"}}
        """))
        model.consume(try decode("""
        {"agent":"Dulus","type":"tool_request","session_id":"d","request_id":"r1","payload":{"tool":"Bash"}}
        """))
        XCTAssertEqual(model.ordered.map(\.agent), ["Dulus", "Claude Code", "Codex"])
        XCTAssertEqual(model.lights.map(\.status), ["waiting", "error", "running"])
        let codex = try XCTUnwrap(model.sessions["Codex::c"])
        XCTAssertEqual(codex.pid, 4242)
        model.hostID = "darwin:mac"
        XCTAssertEqual(model.localPID(of: codex), 4242)
        model.hostID = "linux:elsewhere"
        XCTAssertNil(model.localPID(of: codex))  // another machine's pid is not ours to focus
    }

    @MainActor
    func testAFinishedAgentGetsTheSpotlightWhileItPeeks() throws {
        let model = IslandModel()
        for id in ["a", "b", "c"] {
            model.consume(try decode("""
            {"agent":"Agent \(id)","type":"session_started","session_id":"\(id)","payload":{}}
            """))
        }
        model.consume(try decode("""
        {"agent":"Agent b","type":"completed","session_id":"b","payload":{"text":"All green"}}
        """))
        XCTAssertTrue(model.expanded)
        XCTAssertEqual(model.spotlight, "Agent b::b")
        XCTAssertEqual(model.rows(limit: 4).map(\.message), ["All green"])  // the peek: just that one
        model.hoverExpanded = true
        XCTAssertEqual(model.rows(limit: 4).count, 3)  // hovering: everyone, the finished one first
        XCTAssertEqual(model.rows(limit: 4).first?.agent, "Agent b")
    }

    @MainActor
    func testASnapshotWithoutTheShownRequestLetsTheIslandClose() throws {
        let model = IslandModel()
        model.consume(try decode("""
        {"agent":"Claude Code","type":"tool_request","session_id":"a","request_id":"r1","payload":{"tool":"Bash"}}
        """))
        XCTAssertTrue(model.expanded)
        // The hub restarted: the request is gone with it.
        model.load(try JSONDecoder().decode(Snapshot.self, from: Data(#"{"sessions":[],"permissions":[]}"#.utf8)))
        XCTAssertNil(model.permission)
        XCTAssertTrue(model.eventExpanded)  // a brief settle, not stuck open
    }

    @MainActor
    func testSnapshotKeepsWhenThingsHappened() throws {
        let model = IslandModel()
        model.load(try JSONDecoder().decode(Snapshot.self, from: Data("""
        {"sessions":[{"agent":"Codex","session_id":"c","status":"running","started":1000,"status_since":1500,
          "updated":1600,"pid":"77"}],"permissions":[]}
        """.utf8)))
        let session = try XCTUnwrap(model.sessions["Codex::c"])
        XCTAssertEqual(session.startedAt.timeIntervalSince1970, 1000)
        XCTAssertEqual(session.statusSince.timeIntervalSince1970, 1500)
        XCTAssertEqual(session.pid, 77)
    }

    @MainActor
    func testTheHubSaysWhichMachineItIs() {
        let model = IslandModel()
        let client = WebSocketClient(url: URL(string: "ws://127.0.0.1:1")!, token: "", model: model)
        client.handle(Data(#"{"type":"welcome","v":2,"host_id":"darwin:my-mac"}"#.utf8))
        XCTAssertEqual(model.hostID, "darwin:my-mac")
    }

    // MARK: - Agents look the same as on the Qt island

    func testAgentStylesMatchThePythonRegistry() {
        XCTAssertEqual(AgentStyle.of("Claude Code").key, "claude")
        XCTAssertEqual(AgentStyle.of("claudecode").key, "claude")
        XCTAssertEqual(AgentStyle.of("gpt-5 runner").key, "codex")
        XCTAssertEqual(AgentStyle.of("Roo Code").key, "cline")
        XCTAssertEqual(AgentStyle.of("Interant").display, "Dulus")
        // Short aliases only as whole words ("Example" is not Amp, "McNulty" not Continue).
        XCTAssertEqual(AgentStyle.of("Example Agent").glyph, "●")
        XCTAssertEqual(AgentStyle.of("McNulty").glyph, "●")
        // Unknown names: the same stable color dulus_bar.agents picks.
        XCTAssertEqual(AgentStyle.of("My Agent").accent, 0xE879F9)
        XCTAssertEqual(AgentStyle.of("Example Agent").accent, 0xFBBF24)
        XCTAssertEqual(AgentStyle.of("CI").accent, 0xFB7185)
        XCTAssertEqual(AgentStyle.of("").display, "Agent")
        XCTAssertEqual(AgentStyle.of("").accent, 0x38BDF8)
    }

    func testDarkAccentsAreLiftedToStayReadable() {
        XCTAssertEqual(readableColor(0x111827), 0xA0A3A9)  // Grok
        XCTAssertEqual(readableColor(0x000000), 0x999999)  // Ollama
        XCTAssertEqual(readableColor(0x2563EB), 0xA8C1F7)
        XCTAssertEqual(readableColor(0xD97757), 0xD97757)  // bright enough already
    }

    // MARK: - What the card shows

    func testHiddenCharactersAreShownNotObeyed() {
        XCTAssertEqual(revealHidden("a\u{202E}b\rc\u{1B}d\r\ne\tf\u{85}g"),
                       "a⟨U+202E⟩b⟨U+000D⟩c⟨U+001B⟩d\ne\tf⟨U+0085⟩g")
        var request = PermissionRequest(requestID: "r", agent: "a", sessionID: "s")
        request.tool = "Bash\u{202E}"
        request.detail = "echo \u{202E}x/~ fr- mr"
        XCTAssertEqual(request.title, "Bash⟨U+202E⟩")
        XCTAssertFalse(request.code.unicodeScalars.contains("\u{202E}"))
        // The rule you're about to grant forever, most of all.
        request.alwaysLabel = "Bash(\u{202E}*:fr- mr)"
        XCTAssertEqual(request.alwaysText, "Bash(⟨U+202E⟩*:fr- mr)")
    }

    func testCodeIsWrappedNeverCutSideways() {
        let wrapped = wrapCode("npm run build && npm test -- --coverage", columns: 16, maxLines: 10)
        XCTAssertEqual(wrapped.components(separatedBy: "\n"), ["npm run build ", "&& npm test -- ", "--coverage"])
        XCTAssertEqual(wrapCode(String(repeating: "x", count: 20), columns: 8, maxLines: 10),
                       "xxxxxxxx\nxxxxxxxx\nxxxx")
        let long = (1...12).map { "line \($0)" }.joined(separator: "\n")
        let shown = wrapCode(long, columns: 40, maxLines: 5).components(separatedBy: "\n")
        XCTAssertEqual(shown.count, 5)
        XCTAssertEqual(shown.last, "… 8 more lines")
    }

    func testSmallReadouts() {
        XCTAssertEqual(shortDuration(42), "42s")
        XCTAssertEqual(shortDuration(600), "10m")
        XCTAssertEqual(shortDuration(7300), "2h")
        XCTAssertEqual(contextPercent("ctx 87%"), 87)
        XCTAssertNil(contextPercent("82k"))
    }

    // MARK: - The menu opens agents wired to the island

    func testAgentsFromTheMenuRunUnderTheWrapper() {
        let env = ["DULUS_BAR_CLI": #"["/usr/bin/python3", "/opt/dulus_bar/main.py"]"#]
        XCTAssertEqual(Launch.wired("Codex", ["codex"], in: env),
                       ["/usr/bin/python3", "/opt/dulus_bar/main.py", "wrap", "Codex", "--", "codex"])
        XCTAssertEqual(Launch.wired("Codex", ["codex"], in: [:]), ["codex"])  // no hub info: as-is
        XCTAssertEqual(Launch.dulus(in: ["DULUS_BAR_OPEN_DULUS": #"["python3", "/w/dulus_wrapper.py"]"#]),
                       ["python3", "/w/dulus_wrapper.py"])
        XCTAssertEqual(Launch.dulus(in: env).suffix(4), ["wrap", "Dulus", "--", "dulus"])
        let script = Launch.terminalScript(["/Users/me/My \"AI\"/agent", "it's"])
        XCTAssertTrue(script.contains(#"do script "'/Users/me/My \"AI\"/agent' 'it'\\''s'""#), script)
    }

    private func decode(_ json: String) throws -> AgentEvent {
        try JSONDecoder().decode(AgentEvent.self, from: Data(json.utf8))
    }
}

import Foundation

/// ESP32 mode state — matches the JSON returned by GET /mode.
struct ESP32Mode: Equatable {
    var inputMode: String      // "wifi" | "wired"
    var outputDelivery: String  // "bluetooth" | "wired"
    var outputMode: String      // "mouse_keyboard" | "gamepad"

    static let unknown = ESP32Mode(inputMode: "unknown", outputDelivery: "unknown", outputMode: "unknown")
}

private enum ESP32WSError: Error {
    case notConnected
    case invalidResponse
    case timeout
}

/// HTTP client for sending HID commands to the ESP32.
/// Mirrors the Python `esp32_client.py` — WebSocket-first for commands with HTTP fallback.
class ESP32HTTPClient: ObservableObject {
    @Published var isReachable = false
    @Published var currentMode = ESP32Mode.unknown
    @Published var wsConnected = false

    private let session: URLSession
    private let log: (String) -> Void
    private var host: String
    private var port: Int
    private var wsPort: Int
    private var wsTask: URLSessionWebSocketTask?
    private var wsSequence: Int = 0
    private let wsSemaphore = DispatchSemaphore(value: 1)

    var baseURL: String { "http://\(host):\(port)" }
    var wsURL: String { "ws://\(host):\(wsPort)" }

    init(host: String = "192.168.1.100", port: Int = 80, wsPort: Int = 81,
         log: @escaping (String) -> Void = { print("ESP32: \($0)") }) {
        self.host = host
        self.port = port
        self.wsPort = wsPort
        self.log = log

        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 2.0
        // Keep-alive for low-latency repeated commands
        config.httpShouldUsePipelining = true
        self.session = URLSession(configuration: config)
    }

    func updateEndpoint(host: String, port: Int, wsPort: Int = 81) {
        let changed = self.host != host || self.port != port || self.wsPort != wsPort
        self.host = host
        self.port = port
        self.wsPort = wsPort
        if changed {
            disconnectWebSocket()
        }
    }

    /// Send a HID command to the ESP32 and return timing info.
    /// Returns (success, completedAt) for CommandAck construction.
    func sendCommand(action: String, params: [String: Double] = [:]) async -> (success: Bool, completedAt: Double, error: String?) {
        if wsConnected {
            do {
                let result = try await sendCommandOverWebSocket(action: action, params: params)
                let status = (result["status"] as? String) ?? "error"
                let ok = status == "ok"
                let err = ok ? nil : ((result["error"] as? String) ?? (result["reason"] as? String) ?? "WS status=\(status)")
                return (ok, Date().timeIntervalSince1970, err)
            } catch {
                log("WS command failed, falling back to HTTP: \(error.localizedDescription)")
                disconnectWebSocket()
            }
        }

        return await sendCommandOverHTTP(action: action, params: params)
    }

    private func sendCommandOverHTTP(action: String, params: [String: Double]) async -> (success: Bool, completedAt: Double, error: String?) {
        let url = URL(string: "\(baseURL)/command")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        var body: [String: Any] = ["action": action]
        for (key, value) in params {
            body[key] = value
        }

        guard let httpBody = try? JSONSerialization.data(withJSONObject: body) else {
            return (false, Date().timeIntervalSince1970, "Failed to encode command")
        }
        request.httpBody = httpBody

        do {
            let (_, response) = try await session.data(for: request)
            let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
            let completedAt = Date().timeIntervalSince1970
            let success = statusCode == 200
            if !success {
                log("Command \(action) failed: HTTP \(statusCode)")
            }
            return (success, completedAt, success ? nil : "HTTP \(statusCode)")
        } catch {
            let completedAt = Date().timeIntervalSince1970
            log("Command \(action) error: \(error.localizedDescription)")
            return (false, completedAt, error.localizedDescription)
        }
    }

    func connectWebSocket() async -> Bool {
        if wsConnected { return true }
        guard let url = URL(string: wsURL) else { return false }

        let task = session.webSocketTask(with: url)
        wsTask = task
        task.resume()

        do {
            _ = try await sendWSMessage(["type": "ping"])
            await MainActor.run { self.wsConnected = true }
            log("ESP32 WS connected (\(wsURL))")
            return true
        } catch {
            log("ESP32 WS connect failed: \(error.localizedDescription)")
            disconnectWebSocket()
            return false
        }
    }

    func disconnectWebSocket() {
        wsTask?.cancel(with: .goingAway, reason: nil)
        wsTask = nil
        Task { @MainActor in
            self.wsConnected = false
        }
    }

    func sendCommandOverWebSocket(action: String, params: [String: Double] = [:]) async throws -> [String: Any] {
        var payload: [String: Any] = ["type": "command", "action": action]
        for (key, value) in params { payload[key] = value }
        return try await sendWSMessage(payload)
    }

    private func sendWSMessage(_ payload: [String: Any]) async throws -> [String: Any] {
        guard let wsTask else { throw ESP32WSError.notConnected }

        wsSemaphore.wait()
        defer { wsSemaphore.signal() }

        wsSequence += 1
        let seq = wsSequence

        var body = payload
        body["seq"] = seq
        let data = try JSONSerialization.data(withJSONObject: body)
        guard let text = String(data: data, encoding: .utf8) else {
            throw ESP32WSError.invalidResponse
        }

        try await wsTask.send(.string(text))
        let deadline = Date().addingTimeInterval(2.0)

        while Date() < deadline {
            let message = try await receiveWSMessage(task: wsTask, timeout: 2.0)
            let rawText: String
            switch message {
            case .string(let text): rawText = text
            case .data(let data): rawText = String(data: data, encoding: .utf8) ?? ""
            @unknown default: rawText = ""
            }

            guard let jsonData = rawText.data(using: .utf8),
                  let dict = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any]
            else {
                continue
            }

            if let incomingSeq = dict["seq"] as? Int, incomingSeq == seq {
                if let type = dict["type"] as? String, (type == "ack" || type == "pong") {
                    return dict
                }
                // Some firmware responses may omit type but keep matching seq
                return dict
            }

            // Server hello/status packets can arrive first; keep waiting.
            if let type = dict["type"] as? String, type == "hello" {
                await MainActor.run { self.wsConnected = true }
            }
        }

        throw ESP32WSError.timeout
    }

    private func receiveWSMessage(task: URLSessionWebSocketTask, timeout: TimeInterval) async throws -> URLSessionWebSocketTask.Message {
        try await withThrowingTaskGroup(of: URLSessionWebSocketTask.Message.self) { group in
            group.addTask {
                try await task.receive()
            }
            group.addTask {
                try await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                throw ESP32WSError.timeout
            }

            guard let first = try await group.next() else {
                throw ESP32WSError.timeout
            }
            group.cancelAll()
            return first
        }
    }

    /// Ping the ESP32 to check reachability.
    func ping() async -> Bool {
        // Prefer WS ping if connected
        if wsConnected {
            do {
                _ = try await sendWSMessage(["type": "ping"])
                return true
            } catch {
                disconnectWebSocket()
            }
        }

        let url = URL(string: "\(baseURL)/ping")!
        do {
            let (_, response) = try await session.data(from: url)
            let ok = (response as? HTTPURLResponse)?.statusCode == 200
            await MainActor.run { self.isReachable = ok }
            return ok
        } catch {
            await MainActor.run { self.isReachable = false }
            return false
        }
    }

    /// Query ESP32 current mode (input, output delivery, output mode).
    /// Mirrors Python `esp32_client.get_mode()`.
    func getMode() async -> ESP32Mode? {
        let url = URL(string: "\(baseURL)/mode")!
        do {
            let (data, response) = try await session.data(from: url)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
            let mode = ESP32Mode(
                inputMode: json["input_mode"] as? String ?? "unknown",
                outputDelivery: json["output_delivery"] as? String ?? "unknown",
                outputMode: json["output_mode"] as? String ?? "unknown"
            )
            await MainActor.run { self.currentMode = mode }
            log("ESP32 mode: \(mode.inputMode)/\(mode.outputDelivery)/\(mode.outputMode)")
            return mode
        } catch {
            log("Failed to get ESP32 mode: \(error.localizedDescription)")
            return nil
        }
    }

    /// Set ESP32 mode. Accepted keys: input_mode, output_delivery, output_mode.
    /// Mirrors Python `esp32_client.set_mode()`; also supports v3 keys mode, delivery_policy.
    func setMode(inputMode: String? = nil, outputDelivery: String? = nil, outputMode: String? = nil,
                 mode: String? = nil, deliveryPolicy: String? = nil) async -> Bool {
        let url = URL(string: "\(baseURL)/mode")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        var body: [String: String] = [:]
        if let v = inputMode { body["input_mode"] = v }
        if let v = outputDelivery { body["output_delivery"] = v }
        if let v = outputMode { body["output_mode"] = v }
        if let v = mode { body["mode"] = v }
        if let v = deliveryPolicy { body["delivery_policy"] = v }

        guard let httpBody = try? JSONSerialization.data(withJSONObject: body) else { return false }
        request.httpBody = httpBody

        do {
            let (data, response) = try await session.data(for: request)
            let ok = (response as? HTTPURLResponse)?.statusCode == 200
            if ok, let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                let mode = ESP32Mode(
                    inputMode: json["input_mode"] as? String ?? "unknown",
                    outputDelivery: json["output_delivery"] as? String ?? "unknown",
                    outputMode: json["output_mode"] as? String ?? "unknown"
                )
                await MainActor.run { self.currentMode = mode }
            }
            return ok
        } catch {
            log("Failed to set ESP32 mode: \(error.localizedDescription)")
            return false
        }
    }

}

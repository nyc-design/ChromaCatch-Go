import Foundation

/// HTTP client for sending HID commands to the ESP32.
/// Mirrors the Python `esp32_client.py` — POST /command with keep-alive.
class ESP32HTTPClient: ObservableObject {
    @Published var isReachable = false

    private let session: URLSession
    private let log: (String) -> Void
    private var host: String
    private var port: Int

    var baseURL: String { "http://\(host):\(port)" }

    init(host: String = "192.168.1.100", port: Int = 80,
         log: @escaping (String) -> Void = { print("ESP32: \($0)") }) {
        self.host = host
        self.port = port
        self.log = log

        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 2.0
        // Keep-alive for low-latency repeated commands
        config.httpShouldUsePipelining = true
        self.session = URLSession(configuration: config)
    }

    func updateEndpoint(host: String, port: Int) {
        self.host = host
        self.port = port
    }

    /// Send a HID command to the ESP32 and return timing info.
    /// Returns (success, completedAt) for CommandAck construction.
    func sendCommand(action: String, params: [String: Double] = [:]) async -> (success: Bool, completedAt: Double, error: String?) {
        let url = URL(string: "\(baseURL)/command")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        var body: [String: Any] = ["action": action]
        if !params.isEmpty {
            body["params"] = params
        }

        guard let httpBody = try? JSONSerialization.data(withJSONObject: body) else {
            return (false, Date().timeIntervalSince1970, "Failed to encode command")
        }
        request.httpBody = httpBody

        do {
            let (_, response) = try await session.data(for: request)
            let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
            let completedAt = Date().timeIntervalSince1970
            let success = statusCode >= 200 && statusCode < 300
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

    /// Ping the ESP32 to check reachability.
    func ping() async -> Bool {
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
}

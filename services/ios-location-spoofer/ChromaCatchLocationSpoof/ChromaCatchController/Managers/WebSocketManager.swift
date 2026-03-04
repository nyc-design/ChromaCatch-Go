import Foundation

/// WebSocket client for connecting to ChromaCatch backend.
/// Receives LocationUpdateMessage and HIDCommandMessage from the backend.
/// Sends status updates and heartbeat pongs.
class WebSocketManager: NSObject, ObservableObject, URLSessionWebSocketDelegate {
    private var webSocketTask: URLSessionWebSocketTask?
    private var session: URLSession!
    private var url: URL
    private var apiKey: String
    private var clientId: String
    private var isIntentionalDisconnect = false
    private var pingTimer: Timer?
    private let log: (String) -> Void
    let label: String  // For debug logging (e.g., "control", "location")

    @Published var isConnected = false

    /// Called when a text message is received from the backend
    var onMessage: ((String) -> Void)?

    private let reconnectBaseDelay: TimeInterval = 3.0
    private let reconnectMaxDelay: TimeInterval = 30.0
    private var currentReconnectDelay: TimeInterval = 3.0

    init(url: URL, apiKey: String = "", clientId: String = "",
         label: String = "WS",
         log: @escaping (String) -> Void = { print("WS: \($0)") }) {
        self.url = url
        self.apiKey = apiKey
        self.clientId = clientId
        self.label = label
        self.log = log
        super.init()

        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = true
        self.session = URLSession(
            configuration: config,
            delegate: self,
            delegateQueue: OperationQueue()
        )
    }

    func updateURL(_ newURL: URL) {
        self.url = newURL
    }

    func updateClientId(_ newId: String) {
        self.clientId = newId
    }

    func connect() {
        isIntentionalDisconnect = false
        currentReconnectDelay = reconnectBaseDelay

        // Build URL with API key + client_id query params
        var components = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        var queryItems = components.queryItems ?? []
        if !apiKey.isEmpty {
            queryItems.append(URLQueryItem(name: "api_key", value: apiKey))
        }
        if !clientId.isEmpty {
            queryItems.append(URLQueryItem(name: "client_id", value: clientId))
        }
        components.queryItems = queryItems.isEmpty ? nil : queryItems

        var request = URLRequest(url: components.url!)
        if !apiKey.isEmpty {
            request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        }

        webSocketTask = session.webSocketTask(with: request)
        webSocketTask?.resume()
        receiveMessage()
        log("[\(label)] Connecting to \(url.absoluteString)...")
    }

    func disconnect() {
        isIntentionalDisconnect = true
        stopPingTimer()
        webSocketTask?.cancel(with: .normalClosure, reason: nil)
        DispatchQueue.main.async { self.isConnected = false }
        log("Disconnected intentionally")
    }

    func send(text: String) {
        webSocketTask?.send(.string(text)) { [weak self] error in
            if let error = error {
                self?.log("Send error: \(error.localizedDescription)")
            }
        }
    }

    func send(data: Data) {
        webSocketTask?.send(.data(data)) { [weak self] error in
            if let error = error {
                self?.log("Send binary error: \(error.localizedDescription)")
            }
        }
    }

    /// Send a Codable message as JSON
    func sendJSON<T: Encodable>(_ message: T) {
        guard let data = try? JSONEncoder().encode(message),
              let text = String(data: data, encoding: .utf8) else {
            log("Failed to encode message")
            return
        }
        send(text: text)
    }

    // MARK: - Receive Loop

    private func receiveMessage() {
        webSocketTask?.receive { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let message):
                switch message {
                case .string(let text):
                    self.onMessage?(text)
                case .data(let data):
                    if let text = String(data: data, encoding: .utf8) {
                        self.onMessage?(text)
                    }
                @unknown default:
                    break
                }
                self.receiveMessage()

            case .failure(let error):
                self.log("Receive error: \(error.localizedDescription)")
                self.handleDisconnect()
            }
        }
    }

    // MARK: - Reconnect

    private func handleDisconnect() {
        DispatchQueue.main.async { self.isConnected = false }
        stopPingTimer()
        guard !isIntentionalDisconnect else { return }

        log("Reconnecting in \(Int(currentReconnectDelay))s...")
        DispatchQueue.global().asyncAfter(deadline: .now() + currentReconnectDelay) { [weak self] in
            guard let self = self, !self.isIntentionalDisconnect else { return }
            self.connect()
        }
        currentReconnectDelay = min(currentReconnectDelay * 2, reconnectMaxDelay)
    }

    // MARK: - Ping Keepalive

    func startPingTimer() {
        stopPingTimer()
        DispatchQueue.main.async {
            self.pingTimer = Timer.scheduledTimer(withTimeInterval: 30.0, repeats: true) { [weak self] _ in
                self?.webSocketTask?.sendPing { error in
                    if let error = error {
                        self?.log("Ping failed: \(error.localizedDescription)")
                    }
                }
            }
        }
    }

    private func stopPingTimer() {
        pingTimer?.invalidate()
        pingTimer = nil
    }

    // MARK: - URLSessionWebSocketDelegate

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didOpenWithProtocol protocol: String?) {
        log("Connected")
        DispatchQueue.main.async { self.isConnected = true }
        currentReconnectDelay = reconnectBaseDelay
        startPingTimer()
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
                    reason: Data?) {
        log("Closed (code: \(closeCode.rawValue))")
        handleDisconnect()
    }
}

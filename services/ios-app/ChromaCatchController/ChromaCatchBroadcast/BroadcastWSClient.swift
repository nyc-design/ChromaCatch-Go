import Foundation

/// Simplified WebSocket client for the Broadcast Upload Extension process.
/// No SwiftUI — extensions can't use ObservableObject.
/// Sends H.264 AUs using the exact same two-message pattern as the CLI tool:
/// 1. JSON H264FrameMetadata text message
/// 2. Binary H.264 AU data message
class BroadcastWSClient: NSObject, URLSessionWebSocketDelegate {
    private var webSocketTask: URLSessionWebSocketTask?
    private var session: URLSession!
    private var url: URL
    private var apiKey: String
    private var clientId: String
    private(set) var isConnected = false
    private var sequence: Int = 0
    /// Buffers the most recent keyframe so it can be sent immediately on (re)connect.
    /// Without this, the first keyframe is dropped because the WS isn't connected yet.
    private var pendingKeyframe: (data: Data, timestamp: Double)?

    init(url: URL, apiKey: String = "", clientId: String = "ios-broadcast") {
        self.url = url
        self.apiKey = apiKey
        self.clientId = clientId
        super.init()

        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = true
        self.session = URLSession(
            configuration: config,
            delegate: self,
            delegateQueue: OperationQueue()
        )
    }

    func connect() {
        var components = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        var queryItems = components.queryItems ?? []
        if !apiKey.isEmpty {
            queryItems.append(URLQueryItem(name: "api_key", value: apiKey))
        }
        queryItems.append(URLQueryItem(name: "client_id", value: clientId))
        components.queryItems = queryItems.isEmpty ? nil : queryItems

        var request = URLRequest(url: components.url!)
        if !apiKey.isEmpty {
            request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        }

        webSocketTask = session.webSocketTask(with: request)
        webSocketTask?.resume()
        startReceiveLoop()
    }

    func disconnect() {
        webSocketTask?.cancel(with: .normalClosure, reason: nil)
        isConnected = false
        pendingKeyframe = nil
    }

    /// Send an H.264 Access Unit — exact same protocol as Python `WebSocketClient.send_h264_au()`.
    func sendH264AU(_ auData: Data, isKeyframe: Bool, captureTimestamp: Double) {
        // Buffer keyframes so we can replay on connect (the first keyframe
        // almost always arrives before the WebSocket handshake completes).
        if isKeyframe {
            pendingKeyframe = (data: auData, timestamp: captureTimestamp)
        }
        guard isConnected else { return }
        sequence += 1

        let metadata = H264FrameMetadata(
            sequence: sequence,
            isKeyframe: isKeyframe,
            captureTimestamp: captureTimestamp,
            byteLength: auData.count
        )

        // 1. JSON metadata text message
        if let jsonData = try? JSONEncoder().encode(metadata),
           let jsonText = String(data: jsonData, encoding: .utf8) {
            webSocketTask?.send(.string(jsonText)) { _ in }
        }

        // 2. Binary H.264 AU data
        webSocketTask?.send(.data(auData)) { _ in }
    }

    /// Send an audio chunk — same two-message pattern.
    func sendAudioChunk(_ pcmData: Data, sampleRate: Int, channels: Int,
                        captureTimestamp: Double) {
        guard isConnected else { return }
        sequence += 1

        let metadata = AudioChunkMetadata(
            sequence: sequence,
            sampleRate: sampleRate,
            channels: channels,
            captureTimestamp: captureTimestamp,
            byteLength: pcmData.count
        )

        if let jsonData = try? JSONEncoder().encode(metadata),
           let jsonText = String(data: jsonData, encoding: .utf8) {
            webSocketTask?.send(.string(jsonText)) { _ in }
        }

        webSocketTask?.send(.data(pcmData)) { _ in }
    }

    // MARK: - Private

    private func startReceiveLoop() {
        webSocketTask?.receive { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success:
                // We don't expect incoming messages on the frame channel,
                // but keep receiving to maintain the connection
                self.startReceiveLoop()
            case .failure(let error):
                self.isConnected = false
                NSLog("[BroadcastWS] Receive failed: %@, reconnecting in 3s", error.localizedDescription)
                // Auto-reconnect after 3 seconds
                DispatchQueue.global().asyncAfter(deadline: .now() + 3.0) { [weak self] in
                    self?.connect()
                }
            }
        }
    }

    // MARK: - URLSessionWebSocketDelegate

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didOpenWithProtocol protocol: String?) {
        isConnected = true
        NSLog("[BroadcastWS] Connected")
        // Flush buffered keyframe so the decoder can start immediately
        if let kf = pendingKeyframe {
            pendingKeyframe = nil
            sendH264AU(kf.data, isKeyframe: true, captureTimestamp: kf.timestamp)
        }
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
                    reason: Data?) {
        NSLog("[BroadcastWS] Closed with code: %d", closeCode.rawValue)
        isConnected = false
    }
}

import Foundation

// MARK: - Protocol Constants

enum MessageType {
    static let frame = "frame"
    static let h264Frame = "h264_frame"
    static let audioChunk = "audio_chunk"
    static let clientStatus = "client_status"
    static let hidCommand = "hid_command"
    static let commandAck = "command_ack"
    static let configUpdate = "config_update"
    static let locationUpdate = "location_update"
    static let error = "error"
    static let ping = "ping"
    static let pong = "pong"
}

// MARK: - Incoming Messages (Backend -> iOS App)

struct LocationUpdateMessage: Codable {
    let type: String
    let latitude: Double
    let longitude: Double
    let altitude: Double
    let speedKnots: Double
    let heading: Double
    let timestamp: Double
    let protocolVersion: String

    enum CodingKeys: String, CodingKey {
        case type, latitude, longitude, altitude, timestamp, heading
        case speedKnots = "speed_knots"
        case protocolVersion = "protocol_version"
    }
}

struct HIDCommandMessage: Codable {
    let type: String
    let action: String
    let params: [String: Double]?
    let commandId: String?
    let commandSequence: Int?
    let dispatchedAtBackend: Double?
    let timestamp: Double

    enum CodingKeys: String, CodingKey {
        case type, action, params, timestamp
        case commandId = "command_id"
        case commandSequence = "command_sequence"
        case dispatchedAtBackend = "dispatched_at_backend"
    }
}

struct HeartbeatPing: Codable {
    let type: String
    let timestamp: Double
}

// MARK: - Outgoing Messages (iOS App -> Backend)

/// Mirrors Python CommandAck — sent after forwarding a HID command to ESP32.
struct CommandAckMessage: Codable {
    let type: String
    let commandId: String
    let commandSequence: Int?
    let receivedAtClient: Double
    let forwardedAtClient: Double?
    let completedAtClient: Double
    let success: Bool
    let error: String?
    let timestamp: Double
    let protocolVersion: String

    enum CodingKeys: String, CodingKey {
        case type, success, error, timestamp
        case commandId = "command_id"
        case commandSequence = "command_sequence"
        case receivedAtClient = "received_at_client"
        case forwardedAtClient = "forwarded_at_client"
        case completedAtClient = "completed_at_client"
        case protocolVersion = "protocol_version"
    }

    init(commandId: String, commandSequence: Int?, receivedAt: Double,
         forwardedAt: Double?, completedAt: Double, success: Bool, error: String? = nil) {
        self.type = MessageType.commandAck
        self.commandId = commandId
        self.commandSequence = commandSequence
        self.receivedAtClient = receivedAt
        self.forwardedAtClient = forwardedAt
        self.completedAtClient = completedAt
        self.success = success
        self.error = error
        self.timestamp = Date().timeIntervalSince1970
        self.protocolVersion = "1.0"
    }
}

/// H.264 frame metadata — sent before binary H.264 Access Unit data.
/// Mirrors Python H264FrameMetadata.
struct H264FrameMetadata: Codable {
    let type: String
    let sequence: Int
    let isKeyframe: Bool
    let captureTimestamp: Double
    let sentTimestamp: Double?
    let byteLength: Int
    let timestamp: Double
    let protocolVersion: String

    enum CodingKeys: String, CodingKey {
        case type, sequence, timestamp
        case isKeyframe = "is_keyframe"
        case captureTimestamp = "capture_timestamp"
        case sentTimestamp = "sent_timestamp"
        case byteLength = "byte_length"
        case protocolVersion = "protocol_version"
    }

    init(sequence: Int, isKeyframe: Bool, captureTimestamp: Double, byteLength: Int) {
        self.type = MessageType.h264Frame
        self.sequence = sequence
        self.isKeyframe = isKeyframe
        self.captureTimestamp = captureTimestamp
        self.sentTimestamp = Date().timeIntervalSince1970
        self.byteLength = byteLength
        self.timestamp = Date().timeIntervalSince1970
        self.protocolVersion = "1.0"
    }
}

/// Audio chunk metadata — sent before binary audio data.
/// Mirrors Python AudioChunk.
struct AudioChunkMetadata: Codable {
    let type: String
    let sequence: Int
    let sampleRate: Int
    let channels: Int
    let sampleFormat: String
    let captureTimestamp: Double
    let sentTimestamp: Double?
    let byteLength: Int
    let timestamp: Double
    let protocolVersion: String

    enum CodingKeys: String, CodingKey {
        case type, sequence, channels, timestamp
        case sampleRate = "sample_rate"
        case sampleFormat = "sample_format"
        case captureTimestamp = "capture_timestamp"
        case sentTimestamp = "sent_timestamp"
        case byteLength = "byte_length"
        case protocolVersion = "protocol_version"
    }

    init(sequence: Int, sampleRate: Int, channels: Int,
         sampleFormat: String = "s16le", captureTimestamp: Double, byteLength: Int) {
        self.type = MessageType.audioChunk
        self.sequence = sequence
        self.sampleRate = sampleRate
        self.channels = channels
        self.sampleFormat = sampleFormat
        self.captureTimestamp = captureTimestamp
        self.sentTimestamp = Date().timeIntervalSince1970
        self.byteLength = byteLength
        self.timestamp = Date().timeIntervalSince1970
        self.protocolVersion = "1.0"
    }
}

/// Client status — mirrors Python ClientStatus with iOS-specific extras.
/// Pydantic ignores unknown fields, so the iOS extras are safe to include.
struct ClientStatus: Codable {
    let type: String
    // Standard fields (matching Python ClientStatus)
    let airplayRunning: Bool
    let esp32Reachable: Bool
    let esp32BleConnected: Bool?
    let framesCaptured: Int
    let framesSent: Int
    let captureSource: String
    let sourceRunning: Bool
    let controlChannelConnected: Bool
    let transportMode: String
    let transportConnected: Bool
    let commandsSent: Int
    let commandsAcked: Int
    let lastCommandRttMs: Double?
    let audioEnabled: Bool
    let audioSource: String?
    let audioChunksCaptured: Int
    let audioChunksSent: Int
    let uptimeSeconds: Double
    // iOS-specific extras (Pydantic ignores unknown fields)
    let eaConnected: Bool
    let bleConnected: Bool
    let dongleForwarding: Bool
    let currentLatitude: Double?
    let currentLongitude: Double?
    let timestamp: Double
    let protocolVersion: String

    enum CodingKeys: String, CodingKey {
        case type, timestamp
        case airplayRunning = "airplay_running"
        case esp32Reachable = "esp32_reachable"
        case esp32BleConnected = "esp32_ble_connected"
        case framesCaptured = "frames_captured"
        case framesSent = "frames_sent"
        case captureSource = "capture_source"
        case sourceRunning = "source_running"
        case controlChannelConnected = "control_channel_connected"
        case transportMode = "transport_mode"
        case transportConnected = "transport_connected"
        case commandsSent = "commands_sent"
        case commandsAcked = "commands_acked"
        case lastCommandRttMs = "last_command_rtt_ms"
        case audioEnabled = "audio_enabled"
        case audioSource = "audio_source"
        case audioChunksCaptured = "audio_chunks_captured"
        case audioChunksSent = "audio_chunks_sent"
        case uptimeSeconds = "uptime_seconds"
        case eaConnected = "ea_connected"
        case bleConnected = "ble_connected"
        case dongleForwarding = "dongle_forwarding"
        case currentLatitude = "current_latitude"
        case currentLongitude = "current_longitude"
        case protocolVersion = "protocol_version"
    }

    init(eaConnected: Bool, bleConnected: Bool, dongleForwarding: Bool,
         esp32Reachable: Bool = false, controlChannelConnected: Bool = false,
         transportMode: String = "ios-replaykit", transportConnected: Bool = false,
         framesCaptured: Int = 0, framesSent: Int = 0,
         commandsSent: Int = 0, commandsAcked: Int = 0,
         lastCommandRttMs: Double? = nil,
         audioChunksCaptured: Int = 0, audioChunksSent: Int = 0,
         currentLatitude: Double? = nil, currentLongitude: Double? = nil,
         uptimeSeconds: Double = 0) {
        self.type = MessageType.clientStatus
        self.airplayRunning = false  // iOS doesn't use AirPlay
        self.esp32Reachable = esp32Reachable
        self.esp32BleConnected = nil
        self.framesCaptured = framesCaptured
        self.framesSent = framesSent
        self.captureSource = "ios-replaykit"
        self.sourceRunning = false
        self.controlChannelConnected = controlChannelConnected
        self.transportMode = transportMode
        self.transportConnected = transportConnected
        self.commandsSent = commandsSent
        self.commandsAcked = commandsAcked
        self.lastCommandRttMs = lastCommandRttMs
        self.audioEnabled = false
        self.audioSource = nil
        self.audioChunksCaptured = audioChunksCaptured
        self.audioChunksSent = audioChunksSent
        self.uptimeSeconds = uptimeSeconds
        self.eaConnected = eaConnected
        self.bleConnected = bleConnected
        self.dongleForwarding = dongleForwarding
        self.currentLatitude = currentLatitude
        self.currentLongitude = currentLongitude
        self.timestamp = Date().timeIntervalSince1970
        self.protocolVersion = "1.0"
    }
}

struct HeartbeatPong: Codable {
    let type: String
    let timestamp: Double

    init() {
        self.type = MessageType.pong
        self.timestamp = Date().timeIntervalSince1970
    }
}

// MARK: - Message Parsing

enum IncomingMessage {
    case locationUpdate(LocationUpdateMessage)
    case hidCommand(HIDCommandMessage)
    case ping(HeartbeatPing)
    case unknown(String)

    static func parse(_ text: String) -> IncomingMessage? {
        guard let data = text.data(using: .utf8) else { return nil }

        // Peek at the type field
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["type"] as? String else {
            return nil
        }

        let decoder = JSONDecoder()
        switch type {
        case MessageType.locationUpdate:
            guard let msg = try? decoder.decode(LocationUpdateMessage.self, from: data) else { return nil }
            return .locationUpdate(msg)
        case MessageType.hidCommand:
            guard let msg = try? decoder.decode(HIDCommandMessage.self, from: data) else { return nil }
            return .hidCommand(msg)
        case MessageType.ping:
            guard let msg = try? decoder.decode(HeartbeatPing.self, from: data) else { return nil }
            return .ping(msg)
        default:
            return .unknown(type)
        }
    }
}

import Foundation

// MARK: - Protocol Constants

enum MessageType {
    static let locationUpdate = "location_update"
    static let locationStatus = "location_status"
    static let ping = "ping"
    static let pong = "pong"
}

// MARK: - Incoming Messages (Location Service -> iOS)

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

struct HeartbeatPing: Codable {
    let type: String
    let timestamp: Double
}

// MARK: - Outgoing Messages (iOS -> Location Service)

struct HeartbeatPong: Codable {
    let type: String
    let timestamp: Double

    init() {
        self.type = MessageType.pong
        self.timestamp = Date().timeIntervalSince1970
    }
}

/// GPS verification status sent to location service.
struct LocationStatusMessage: Codable {
    let type: String
    let spoofedLatitude: Double
    let spoofedLongitude: Double
    let actualLatitude: Double
    let actualLongitude: Double
    let driftMeters: Double
    let isAccurate: Bool
    let timestamp: Double
    let protocolVersion: String

    enum CodingKeys: String, CodingKey {
        case type, timestamp
        case spoofedLatitude = "spoofed_latitude"
        case spoofedLongitude = "spoofed_longitude"
        case actualLatitude = "actual_latitude"
        case actualLongitude = "actual_longitude"
        case driftMeters = "drift_meters"
        case isAccurate = "is_accurate"
        case protocolVersion = "protocol_version"
    }

    init(spoofedLat: Double, spoofedLon: Double,
         actualLat: Double, actualLon: Double,
         driftMeters: Double, isAccurate: Bool) {
        self.type = MessageType.locationStatus
        self.spoofedLatitude = spoofedLat
        self.spoofedLongitude = spoofedLon
        self.actualLatitude = actualLat
        self.actualLongitude = actualLon
        self.driftMeters = driftMeters
        self.isAccurate = isAccurate
        self.timestamp = Date().timeIntervalSince1970
        self.protocolVersion = "1.0"
    }
}

// MARK: - Parsing

enum IncomingMessage {
    case locationUpdate(LocationUpdateMessage)
    case ping(HeartbeatPing)
    case unknown(String)

    static func parse(_ text: String) -> IncomingMessage? {
        guard let data = text.data(using: .utf8) else { return nil }

        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["type"] as? String else {
            return nil
        }

        let decoder = JSONDecoder()
        switch type {
        case MessageType.locationUpdate:
            guard let msg = try? decoder.decode(LocationUpdateMessage.self, from: data) else { return nil }
            return .locationUpdate(msg)
        case MessageType.ping:
            guard let msg = try? decoder.decode(HeartbeatPing.self, from: data) else { return nil }
            return .ping(msg)
        default:
            return .unknown(type)
        }
    }
}

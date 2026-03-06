import Foundation

struct SniperHealthResponse: Decodable {
    let status: String
    let role: String
    let queueSize: Int
    let discordMonitorEnabled: Bool
    let discordMonitorConnected: Bool
}

struct SniperWatchBlocksResponse: Decodable {
    let watchBlocks: [SniperWatchBlock]
}

struct SniperWatchBlocksReplaceRequest: Encodable {
    let watchBlocks: [SniperWatchBlock]
}

struct SniperWatchBlock: Codable, Identifiable, Equatable {
    let id: String
    let serverId: String
    let channelId: String
    let userIds: [String]
    let geofence: SniperGeofence?
    let enabled: Bool

    init(
        id: String = UUID().uuidString,
        serverId: String,
        channelId: String,
        userIds: [String],
        geofence: SniperGeofence? = nil,
        enabled: Bool = true
    ) {
        self.id = id
        self.serverId = serverId
        self.channelId = channelId
        self.userIds = userIds
        self.geofence = geofence
        self.enabled = enabled
    }
}

struct SniperGeofence: Codable, Equatable {
    let latitude: Double
    let longitude: Double
    let radiusKm: Double
}

struct SniperQueueState: Codable {
    let size: Int
    let maxSize: Int
    let items: [SniperQueueItem]

    static let empty = SniperQueueState(size: 0, maxSize: 0, items: [])
}

struct SniperQueueItem: Codable, Identifiable {
    let id: String
    let latitude: Double
    let longitude: Double
    let source: String
    let matchedBlockId: String?
    let matchedServerId: String?
    let matchedChannelId: String?
    let matchedUserId: String?
    let queuedAt: Date
    let sourceMessageId: String?
    let despawnEpoch: Double?
}

struct SniperDispatchRequest: Encodable {
    let clientId: String?
    let altitude: Double?
    let speedKnots: Double?
    let heading: Double?
}

struct SniperDispatchResponse: Decodable {
    let success: Bool
    let sent: SniperQueueItem?
    let locationResponse: AnyDecodable?
    let queue: SniperQueueState
    let message: String?
}

struct SniperQueueEnqueueRequest: Encodable {
    let latitude: Double
    let longitude: Double
    let source: String
}

struct APIErrorResponse: Decodable {
    let detail: String
}

/// Lightweight type-erased decoder for arbitrary JSON value payloads.
struct AnyDecodable: Decodable {
    let value: Any

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let intValue = try? container.decode(Int.self) {
            value = intValue
        } else if let doubleValue = try? container.decode(Double.self) {
            value = doubleValue
        } else if let boolValue = try? container.decode(Bool.self) {
            value = boolValue
        } else if let stringValue = try? container.decode(String.self) {
            value = stringValue
        } else if let dictValue = try? container.decode([String: AnyDecodable].self) {
            value = dictValue.mapValues { $0.value }
        } else if let arrayValue = try? container.decode([AnyDecodable].self) {
            value = arrayValue.map { $0.value }
        } else if container.decodeNil() {
            value = NSNull()
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON type")
        }
    }
}

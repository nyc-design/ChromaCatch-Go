import Foundation

enum SniperAPIError: LocalizedError {
    case invalidURL
    case httpStatus(Int, String)
    case missingResponse

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid sniper API URL"
        case .httpStatus(let code, let detail):
            return detail.isEmpty ? "HTTP \(code)" : "HTTP \(code): \(detail)"
        case .missingResponse:
            return "Missing response payload"
        }
    }
}

final class SniperAPIClient {
    private var baseURL: URL
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    init(baseURL: URL, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.session = session

        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601
        self.decoder = decoder

        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        self.encoder = encoder
    }

    func updateBaseURL(_ url: URL) {
        self.baseURL = url
    }

    func getHealth() async throws -> SniperHealthResponse {
        try await request(path: "/health", method: "GET")
    }

    func getWatchBlocks() async throws -> [SniperWatchBlock] {
        let response: SniperWatchBlocksResponse = try await request(path: "/watch-blocks", method: "GET")
        return response.watchBlocks
    }

    func addWatchBlock(_ watchBlock: SniperWatchBlock, clientId: String?) async throws -> SniperWatchBlock {
        let queryItems = Self.clientIdQueryItems(clientId)
        return try await request(path: "/watch-blocks", method: "POST", queryItems: queryItems, body: watchBlock)
    }

    func deleteWatchBlock(id: String) async throws {
        let _: DeleteResponse = try await request(path: "/watch-blocks/\(id)", method: "DELETE")
    }

    func getQueue() async throws -> SniperQueueState {
        try await request(path: "/queue", method: "GET")
    }

    func clearQueue() async throws -> SniperQueueState {
        try await request(path: "/queue/clear", method: "POST")
    }

    func dispatchNext() async throws -> SniperDispatchResponse {
        try await request(path: "/queue/dispatch-next", method: "POST", body: SniperDispatchRequest(clientId: nil, altitude: nil, speedKnots: nil, heading: nil))
    }

    private static func clientIdQueryItems(_ clientId: String?) -> [URLQueryItem] {
        let trimmed = (clientId ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return [] }
        return [URLQueryItem(name: "client_id", value: trimmed)]
    }

    private func request<T: Decodable>(path: String, method: String, queryItems: [URLQueryItem] = []) async throws -> T {
        try await request(path: path, method: method, queryItems: queryItems, bodyData: nil)
    }

    private func request<T: Decodable, U: Encodable>(
        path: String,
        method: String,
        queryItems: [URLQueryItem] = [],
        body: U?
    ) async throws -> T {
        let bodyData = try body.map { try encoder.encode($0) }
        return try await request(path: path, method: method, queryItems: queryItems, bodyData: bodyData)
    }

    private func request<T: Decodable>(
        path: String,
        method: String,
        queryItems: [URLQueryItem],
        bodyData: Data?
    ) async throws -> T {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            throw SniperAPIError.invalidURL
        }

        let normalizedPath = components.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let suffixPath = path.hasPrefix("/") ? String(path.dropFirst()) : path
        components.path = "/" + [normalizedPath, suffixPath].filter { !$0.isEmpty }.joined(separator: "/")
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }

        guard let requestURL = components.url else {
            throw SniperAPIError.invalidURL
        }

        var request = URLRequest(url: requestURL)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let bodyData {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = bodyData
        }

        let (data, response) = try await session.data(for: request)

        guard let http = response as? HTTPURLResponse else {
            throw SniperAPIError.missingResponse
        }

        guard (200...299).contains(http.statusCode) else {
            if let apiError = try? decoder.decode(APIErrorResponse.self, from: data) {
                throw SniperAPIError.httpStatus(http.statusCode, apiError.detail)
            }
            let detail = String(data: data, encoding: .utf8) ?? ""
            throw SniperAPIError.httpStatus(http.statusCode, detail)
        }

        return try decoder.decode(T.self, from: data)
    }
}

private struct DeleteResponse: Decodable {
    let status: String
    let id: String
}

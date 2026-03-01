import Combine
import Foundation

/// Central orchestrator that coordinates EA, BLE, WebSockets, ESP32, and the NMEA dongle controller.
///
/// Dual WebSocket architecture:
/// - `wsManager` connects to main backend `/ws/control` (HID commands, status, pings)
/// - `locationWSManager` connects to location service `/ws/location` (GPS coordinates)
///
/// HID command flow: backend → wsManager → ESP32HTTPClient → CommandAck back to backend
@MainActor
class AppCoordinator: ObservableObject {
    let eaManager: EAManager
    let bleManager: BLEManager
    let dongleController: DongleController
    let wsManager: WebSocketManager         // Main backend (HID + status)
    let locationWSManager: WebSocketManager  // Location service (GPS coords)
    let esp32Client: ESP32HTTPClient

    @Published var logs: [LogEntry] = []
    @Published var isRunning = false
    @Published var commandsSent: Int = 0
    @Published var commandsAcked: Int = 0

    // Configuration (set from UI or stored in UserDefaults)
    @Published var backendURL: String {
        didSet { UserDefaults.standard.set(backendURL, forKey: "backendURL") }
    }
    @Published var locationServiceURL: String {
        didSet { UserDefaults.standard.set(locationServiceURL, forKey: "locationServiceURL") }
    }
    @Published var apiKey: String {
        didSet { UserDefaults.standard.set(apiKey, forKey: "apiKey") }
    }
    @Published var esp32Host: String {
        didSet { UserDefaults.standard.set(esp32Host, forKey: "esp32Host") }
    }
    @Published var esp32Port: String {
        didSet { UserDefaults.standard.set(esp32Port, forKey: "esp32Port") }
    }
    @Published var clientId: String {
        didSet {
            UserDefaults.standard.set(clientId, forKey: "clientId")
            sharedDefaults?.set(clientId, forKey: "clientId")
        }
    }

    private var cancellables = Set<AnyCancellable>()
    private var monitorTimer: Timer?
    private var statusTimer: Timer?
    private var esp32PingTimer: Timer?
    private let startTime = Date()
    private let sharedDefaults = UserDefaults(suiteName: "group.com.chromacatch")

    private static let defaultBackendURL = "wss://8000--main--chromacatch-go-agents--nyc-design.apps.coder.tapiavala.com/ws/control"
    private static let defaultLocationURL = "wss://8001--main--chromacatch-go-agents--nyc-design.apps.coder.tapiavala.com/ws/location"

    init() {
        // Migrate stale localhost defaults from older builds
        if let old = UserDefaults.standard.string(forKey: "backendURL"), old.contains("localhost") {
            UserDefaults.standard.removeObject(forKey: "backendURL")
        }
        if let old = UserDefaults.standard.string(forKey: "locationServiceURL"), old.contains("localhost") {
            UserDefaults.standard.removeObject(forKey: "locationServiceURL")
        }

        let savedBackendURL = UserDefaults.standard.string(forKey: "backendURL") ?? Self.defaultBackendURL
        let savedLocationURL = UserDefaults.standard.string(forKey: "locationServiceURL") ?? Self.defaultLocationURL
        let savedKey = UserDefaults.standard.string(forKey: "apiKey") ?? ""
        let savedESP32Host = UserDefaults.standard.string(forKey: "esp32Host") ?? "192.168.1.100"
        let savedESP32Port = UserDefaults.standard.string(forKey: "esp32Port") ?? "80"
        let savedClientId = UserDefaults.standard.string(forKey: "clientId") ?? "ios-app"

        self.backendURL = savedBackendURL
        self.locationServiceURL = savedLocationURL
        self.apiKey = savedKey
        self.esp32Host = savedESP32Host
        self.esp32Port = savedESP32Port
        self.clientId = savedClientId

        // Initialize managers with shared logging — all messages go to Xcode console + app UI
        var coordinator: AppCoordinator?
        let logFn: (String) -> Void = { message in
            Task { @MainActor in
                coordinator?.addLog(message)
            }
        }

        eaManager = EAManager(log: logFn)
        bleManager = BLEManager(log: logFn)
        dongleController = DongleController(bleManager: bleManager, log: logFn)
        wsManager = WebSocketManager(
            url: URL(string: savedBackendURL) ?? URL(string: "wss://localhost:8000/ws/control")!,
            apiKey: savedKey,
            clientId: savedClientId,
            label: "CTL",
            log: logFn
        )
        locationWSManager = WebSocketManager(
            url: URL(string: savedLocationURL) ?? URL(string: "wss://localhost:8001/ws/location")!,
            apiKey: savedKey,
            clientId: savedClientId,
            label: "LOC",
            log: logFn
        )
        esp32Client = ESP32HTTPClient(
            host: savedESP32Host,
            port: Int(savedESP32Port) ?? 80,
            log: logFn
        )

        coordinator = self

        // Write initial config to App Group for Broadcast Extension
        sharedDefaults?.set(savedBackendURL, forKey: "backendURL")
        sharedDefaults?.set(savedKey, forKey: "apiKey")
        sharedDefaults?.set(savedClientId, forKey: "clientId")

        setupCallbacks()
    }

    private func setupCallbacks() {
        // Main backend WS: HID commands + pings
        wsManager.onMessage = { [weak self] text in
            guard let self = self else { return }
            Task { @MainActor in
                self.handleControlMessage(text)
            }
        }

        // Location service WS: GPS coordinate updates + pings
        locationWSManager.onMessage = { [weak self] text in
            guard let self = self else { return }
            Task { @MainActor in
                self.handleLocationMessage(text)
            }
        }
    }

    // MARK: - Lifecycle

    func start() {
        guard !isRunning else { return }
        isRunning = true
        addLog("Starting ChromaCatch Controller...")

        // Update ESP32 endpoint from current settings
        esp32Client.updateEndpoint(host: esp32Host, port: Int(esp32Port) ?? 80)

        // Background keepalive: bluetooth-central mode keeps app alive
        // as long as BLE connection + FF02 notifications are active.
        // No CLLocationManager needed.

        // Monitor EA state
        startMonitoring()

        // Connect both WebSockets
        wsManager.connect()
        locationWSManager.connect()

        addLog("Waiting for connections...")
    }

    func stop() {
        isRunning = false
        dongleController.stop()
        wsManager.disconnect()
        locationWSManager.disconnect()
        monitorTimer?.invalidate()
        monitorTimer = nil
        statusTimer?.invalidate()
        statusTimer = nil
        esp32PingTimer?.invalidate()
        esp32PingTimer = nil
        addLog("Stopped")
    }

    // MARK: - Connection Monitoring

    private func startMonitoring() {
        // Monitor EA state and start BLE when EA is up
        monitorTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.checkConnections()
            }
        }

        // Send periodic status to backend
        statusTimer = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.sendStatus()
            }
        }

        // Periodic ESP32 ping
        esp32PingTimer = Timer.scheduledTimer(withTimeInterval: 10.0, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            Task { _ = await self.esp32Client.ping() }
        }
    }

    private func checkConnections() {
        // Retry EA session if not connected (dongle may have been paired after app start)
        if !eaManager.isConnected {
            eaManager.retryConnection()
        }
        // BLE connection is user-initiated via "Scan for Dongle" button.
        // DongleController auto-starts via BLEManager.onReady callback.
    }

    // MARK: - Dongle Pairing (user-initiated)

    func startDongleScan() {
        bleManager.startScanning()
    }

    func stopDongleScan() {
        bleManager.stopScanning()
    }

    func connectToDongle(_ device: DiscoveredDevice) {
        bleManager.connect(to: device)
    }

    func disconnectDongle() {
        dongleController.stop()
        bleManager.disconnect()
        addLog("Dongle disconnected")
    }

    // MARK: - Control Channel Message Handling (Main Backend)

    private func handleControlMessage(_ text: String) {
        guard let msg = IncomingMessage.parse(text) else {
            addLog("Unknown control msg: \(text.prefix(100))")
            return
        }

        switch msg {
        case .hidCommand(let cmd):
            handleHIDCommand(cmd)

        case .locationUpdate:
            // Location updates should come from location service, not control channel
            addLog("Warning: location update on control channel (ignoring)")

        case .ping:
            wsManager.sendJSON(HeartbeatPong())

        case .unknown(let type):
            addLog("Unhandled control type: \(type)")
        }
    }

    // MARK: - Location Channel Message Handling (Location Service)

    private func handleLocationMessage(_ text: String) {
        guard let msg = IncomingMessage.parse(text) else {
            addLog("Unknown location msg: \(text.prefix(100))")
            return
        }

        switch msg {
        case .locationUpdate(let loc):
            addLog("GPS: \(String(format: "%.6f", loc.latitude)), \(String(format: "%.6f", loc.longitude))")
            dongleController.updateCoordinates(
                lat: loc.latitude, lon: loc.longitude,
                altitude: loc.altitude,
                speed: loc.speedKnots, heading: loc.heading
            )

        case .ping:
            locationWSManager.sendJSON(HeartbeatPong())

        case .hidCommand:
            addLog("Warning: HID command on location channel (ignoring)")

        case .unknown(let type):
            addLog("Unhandled location type: \(type)")
        }
    }

    // MARK: - HID Command → ESP32 Forwarding

    private func handleHIDCommand(_ cmd: HIDCommandMessage) {
        let receivedAt = Date().timeIntervalSince1970
        commandsSent += 1
        addLog("HID: \(cmd.action) → ESP32")

        Task {
            let forwardedAt = Date().timeIntervalSince1970
            let result = await esp32Client.sendCommand(
                action: cmd.action,
                params: cmd.params ?? [:]
            )

            await MainActor.run {
                if result.success {
                    self.commandsAcked += 1
                }
            }

            // Send CommandAck back to backend
            let ack = CommandAckMessage(
                commandId: cmd.commandId ?? "",
                commandSequence: cmd.commandSequence,
                receivedAt: receivedAt,
                forwardedAt: forwardedAt,
                completedAt: result.completedAt,
                success: result.success,
                error: result.error
            )
            await MainActor.run {
                self.wsManager.sendJSON(ack)
            }
        }
    }

    // MARK: - Status Reporting

    private func sendStatus() {
        guard wsManager.isConnected else { return }
        let status = ClientStatus(
            eaConnected: eaManager.isConnected,
            bleConnected: bleManager.isConnected,
            dongleForwarding: dongleController.isForwarding,
            esp32Reachable: esp32Client.isReachable,
            controlChannelConnected: wsManager.isConnected,
            transportMode: "h264-ws",
            transportConnected: wsManager.isConnected,
            commandsSent: commandsSent,
            commandsAcked: commandsAcked,
            currentLatitude: dongleController.currentLat != 0 ? dongleController.currentLat : nil,
            currentLongitude: dongleController.currentLon != 0 ? dongleController.currentLon : nil,
            uptimeSeconds: Date().timeIntervalSince(startTime)
        )
        wsManager.sendJSON(status)
    }

    // MARK: - Manual Control

    func sendManualLocation(lat: Double, lon: Double) {
        dongleController.updateCoordinates(lat: lat, lon: lon)
        addLog("Manual location set: \(lat), \(lon)")
    }

    // MARK: - Logging

    func addLog(_ message: String) {
        print("[CC] \(message)")
        let entry = LogEntry(timestamp: Date(), message: message)
        logs.insert(entry, at: 0)
        if logs.count > 500 { logs.removeLast() }
    }
}

struct LogEntry: Identifiable {
    let id = UUID()
    let timestamp: Date
    let message: String
}

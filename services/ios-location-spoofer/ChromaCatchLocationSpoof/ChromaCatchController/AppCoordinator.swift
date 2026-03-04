import Combine
import Foundation

/// Central orchestrator that coordinates EA, BLE, WebSockets, ESP32, BLE HID, and the NMEA dongle controller.
///
/// Dual WebSocket architecture:
/// - `wsManager` connects to main backend `/ws/control` (HID commands, status, pings)
/// - `locationWSManager` connects to location service `/ws/location` (GPS coordinates)
///
/// Command routing: backend → wsManager → (ESP32 HTTP or BLE HID) → CommandAck back to backend
@MainActor
class AppCoordinator: ObservableObject {
    let eaManager: EAManager
    let bleManager: BLEManager
    let dongleController: DongleController
    let wsManager: WebSocketManager         // Main backend (HID + status)
    let locationWSManager: WebSocketManager  // Location service (GPS coords)
    let esp32Client: ESP32HTTPClient
    let bleHIDCommander: BLEHIDCommander    // BLE HID peripheral (mouse/kb/gamepad → target)
    let locationMonitor: LocationMonitor
    let locationKeepAlive: LocationKeepAlive
    let dnsFilterManager: DNSFilterManager

    @Published var logs: [LogEntry] = []
    @Published var isBackendRunning = false
    @Published var isLocationRunning = false
    var isRunning: Bool { isBackendRunning || isLocationRunning }

    @Published var commandsSent: Int = 0
    @Published var commandsAcked: Int = 0

    // ESP32 mode discovery
    @Published var esp32Mode = ESP32Mode.unknown

    // BLE HID — when enabled, commands route to BLE HID instead of ESP32
    @Published var useBLEHID: Bool {
        didSet { UserDefaults.standard.set(useBLEHID, forKey: "useBLEHID") }
    }
    @Published var bleHIDPreferredProfile: HIDProfile {
        didSet { UserDefaults.standard.set(bleHIDPreferredProfile.rawValue, forKey: "bleHIDPreferredProfile") }
    }

    // GPS verification — mirrored from LocationMonitor for SwiftUI binding
    @Published var gpsAccurate: Bool = false
    @Published var gpsDriftMeters: Double = 0
    @Published var iosReportedLat: Double = 0
    @Published var iosReportedLon: Double = 0

    // DNS filter — persisted toggle state
    @Published var dnsFilterEnabled: Bool {
        didSet { UserDefaults.standard.set(dnsFilterEnabled, forKey: "dnsFilterEnabled") }
    }

    // Configuration (set from UI or stored in UserDefaults)
    @Published var backendURL: String {
        didSet {
            UserDefaults.standard.set(backendURL, forKey: "backendURL")
            sharedDefaults?.set(backendURL, forKey: "backendURL")
        }
    }
    @Published var locationServiceURL: String {
        didSet { UserDefaults.standard.set(locationServiceURL, forKey: "locationServiceURL") }
    }
    @Published var apiKey: String {
        didSet {
            UserDefaults.standard.set(apiKey, forKey: "apiKey")
            sharedDefaults?.set(apiKey, forKey: "apiKey")
        }
    }
    @Published var esp32Host: String {
        didSet {
            UserDefaults.standard.set(esp32Host, forKey: "esp32Host")
            esp32Client.updateEndpoint(host: esp32Host, port: Int(esp32Port) ?? 80, wsPort: Int(esp32WSPort) ?? 81)
        }
    }
    @Published var esp32Port: String {
        didSet {
            UserDefaults.standard.set(esp32Port, forKey: "esp32Port")
            esp32Client.updateEndpoint(host: esp32Host, port: Int(esp32Port) ?? 80, wsPort: Int(esp32WSPort) ?? 81)
        }
    }
    @Published var esp32WSPort: String {
        didSet {
            UserDefaults.standard.set(esp32WSPort, forKey: "esp32WSPort")
            esp32Client.updateEndpoint(host: esp32Host, port: Int(esp32Port) ?? 80, wsPort: Int(esp32WSPort) ?? 81)
        }
    }
    @Published var clientId: String {
        didSet {
            UserDefaults.standard.set(clientId, forKey: "clientId")
            sharedDefaults?.set(clientId, forKey: "clientId")
        }
    }

    private var cancellables = Set<AnyCancellable>()
    private var statusTimer: Timer?
    private var esp32PingTimer: Timer?
    private var lastBackendBLEInputAt: TimeInterval = 0
    private let manualBLEBackoffWindowS: TimeInterval = 0.2
    let startTime = Date()
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
        let savedESP32WSPort = UserDefaults.standard.string(forKey: "esp32WSPort") ?? "81"
        let savedClientId = UserDefaults.standard.string(forKey: "clientId") ?? "ios-app"
        let savedDNSFilter = UserDefaults.standard.bool(forKey: "dnsFilterEnabled")
        let savedBLEProfile = HIDProfile(rawValue: UserDefaults.standard.string(forKey: "bleHIDPreferredProfile") ?? "") ?? .combo

        // Migration: reset BLE HID after stability fixes so users can re-enable manually.
        let savedUseBLEHID: Bool
        if UserDefaults.standard.bool(forKey: "useBLEHID") && !UserDefaults.standard.bool(forKey: "bleHIDFixedV4") {
            UserDefaults.standard.set(false, forKey: "useBLEHID")
            UserDefaults.standard.set(true, forKey: "bleHIDFixedV2")
            UserDefaults.standard.set(true, forKey: "bleHIDFixedV3")
            UserDefaults.standard.set(true, forKey: "bleHIDFixedV4")
            savedUseBLEHID = false
        } else {
            savedUseBLEHID = UserDefaults.standard.bool(forKey: "useBLEHID")
        }

        self.backendURL = savedBackendURL
        self.dnsFilterEnabled = savedDNSFilter
        self.useBLEHID = savedUseBLEHID
        self.bleHIDPreferredProfile = savedBLEProfile
        self.locationServiceURL = savedLocationURL
        self.apiKey = savedKey
        self.esp32Host = savedESP32Host
        self.esp32Port = savedESP32Port
        self.esp32WSPort = savedESP32WSPort
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
            wsPort: Int(savedESP32WSPort) ?? 81,
            log: logFn
        )
        bleHIDCommander = BLEHIDCommander()
        locationMonitor = LocationMonitor(log: logFn)
        locationKeepAlive = LocationKeepAlive(log: logFn)
        dnsFilterManager = DNSFilterManager(log: logFn)

        coordinator = self

        // Write initial config to App Group for Broadcast Extension
        sharedDefaults?.set(savedBackendURL, forKey: "backendURL")
        sharedDefaults?.set(savedKey, forKey: "apiKey")
        sharedDefaults?.set(savedClientId, forKey: "clientId")

        setupCallbacks()
        setupLocationMonitorBindings()

        // Restore DNS filter state from last session
        if savedDNSFilter {
            Task { await dnsFilterManager.enable() }
        }
    }

    private func setupCallbacks() {
        // Forward nested ObservableObject changes to trigger SwiftUI re-render
        bleHIDCommander.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)

        esp32Client.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)

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

    /// Mirror LocationMonitor's @Published properties into AppCoordinator for SwiftUI,
    /// and keep target coordinates in sync with dongle controller.
    private func setupLocationMonitorBindings() {
        locationMonitor.$isAccurate
            .receive(on: DispatchQueue.main)
            .assign(to: &$gpsAccurate)

        locationMonitor.$driftMeters
            .receive(on: DispatchQueue.main)
            .assign(to: &$gpsDriftMeters)

        locationMonitor.$iosReportedLat
            .receive(on: DispatchQueue.main)
            .assign(to: &$iosReportedLat)

        locationMonitor.$iosReportedLon
            .receive(on: DispatchQueue.main)
            .assign(to: &$iosReportedLon)

        // When dongle target coordinates change, update LocationMonitor's target
        dongleController.$currentLat
            .combineLatest(dongleController.$currentLon)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] lat, lon in
                self?.locationMonitor.targetLat = lat
                self?.locationMonitor.targetLon = lon
            }
            .store(in: &cancellables)

        // Send LocationStatusMessage to location service whenever drift changes
        locationMonitor.$driftMeters
            .combineLatest(locationMonitor.$isAccurate)
            .throttle(for: .seconds(5), scheduler: DispatchQueue.main, latest: true)
            .sink { [weak self] drift, accurate in
                self?.sendLocationStatus()
            }
            .store(in: &cancellables)
    }

    // MARK: - Lifecycle

    /// Convenience: start all subsystems at once.
    func start() {
        startBackend()
        startLocation()
    }

    /// Convenience: stop all subsystems at once.
    func stop() {
        stopBackend()
        stopLocation()
    }

    // MARK: - Backend Subsystem (Video + Commands)

    func startBackend() {
        guard !isBackendRunning else { return }
        isBackendRunning = true
        addLog("Starting backend connection...")

        // Update ESP32 endpoint from current settings
        esp32Client.updateEndpoint(host: esp32Host, port: Int(esp32Port) ?? 80, wsPort: Int(esp32WSPort) ?? 81)

        // Query ESP32 mode on startup
        Task { await queryESP32Mode() }

        // Start BLE HID if enabled
        if useBLEHID {
            if !bleHIDCommander.isRunning {
                bleHIDCommander.start(profile: bleHIDPreferredProfile)
                addLog("BLE HID commander started (\(bleHIDPreferredProfile.rawValue) profile)")
            }
        }

        // Connect backend WS
        wsManager.connect()

        // Send periodic status to backend
        statusTimer = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.sendStatus()
            }
        }

        // Periodic ESP32 ping — only when a real host is configured
        if !esp32Host.isEmpty && esp32Host != "192.168.1.100" {
            esp32PingTimer = Timer.scheduledTimer(withTimeInterval: 30.0, repeats: true) { [weak self] _ in
                guard let self = self else { return }
                Task { _ = await self.esp32Client.ping() }
            }
        }

        startSharedSubsystems()
        addLog("Backend connection started")
    }

    func stopBackend() {
        guard isBackendRunning else { return }
        isBackendRunning = false

        bleHIDCommander.stop()
        esp32Client.disconnectWebSocket()
        wsManager.disconnect()
        statusTimer?.invalidate()
        statusTimer = nil
        esp32PingTimer?.invalidate()
        esp32PingTimer = nil

        if !isLocationRunning { stopSharedSubsystems() }
        addLog("Backend stopped")
    }

    // MARK: - Location Subsystem (Spoofing)

    func startLocation() {
        guard !isLocationRunning else { return }
        isLocationRunning = true
        addLog("Starting location connection...")

        locationWSManager.connect()
        startSharedSubsystems()
        addLog("Location connection started")
    }

    func stopLocation() {
        guard isLocationRunning else { return }
        isLocationRunning = false

        dongleController.stop()
        locationWSManager.disconnect()

        if !isBackendRunning { stopSharedSubsystems() }
        addLog("Location stopped")
    }

    // MARK: - Shared Subsystems

    private func startSharedSubsystems() {
        locationKeepAlive.start()
        locationMonitor.startMonitoring()
    }

    private func stopSharedSubsystems() {
        locationKeepAlive.stop()
        locationMonitor.stopMonitoring()
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

        case .gameCommand(let cmd):
            handleGameCommand(cmd)

        case .setHidMode(let cmd):
            handleSetHIDMode(cmd)

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

        case .gameCommand:
            addLog("Warning: game command on location channel (ignoring)")

        case .setHidMode:
            addLog("Warning: HID mode change on location channel (ignoring)")

        case .unknown(let type):
            addLog("Unhandled location type: \(type)")
        }
    }

    // MARK: - ESP32 Mode Discovery

    func queryESP32Mode() async {
        if let mode = await esp32Client.getMode() {
            esp32Mode = mode
            addLog("ESP32 mode: \(mode.inputMode)/\(mode.outputDelivery)/\(mode.outputMode)")
        } else {
            addLog("ESP32 mode query failed (device may be offline)")
        }
    }

    func connectESP32CommandWebSocket() async {
        esp32Client.updateEndpoint(host: esp32Host, port: Int(esp32Port) ?? 80, wsPort: Int(esp32WSPort) ?? 81)
        let ok = await esp32Client.connectWebSocket()
        addLog(ok ? "ESP32 command WS connected" : "ESP32 command WS connect failed")
    }

    func disconnectESP32CommandWebSocket() {
        esp32Client.disconnectWebSocket()
        addLog("ESP32 command WS disconnected")
    }

    func toggleESP32CommandWebSocket() {
        if esp32Client.wsConnected {
            disconnectESP32CommandWebSocket()
        } else {
            Task { await connectESP32CommandWebSocket() }
        }
    }

    // MARK: - BLE HID Control

    func setBLEHIDEnabled(_ enabled: Bool) {
        guard useBLEHID != enabled else { return }

        useBLEHID = enabled
        if enabled {
            bleHIDCommander.start(profile: bleHIDPreferredProfile)
            addLog("BLE HID: enabled (\(bleHIDPreferredProfile.rawValue) profile)")
        } else {
            bleHIDCommander.stop()
            addLog("BLE HID: disabled")
        }
    }

    func toggleBLEHID() {
        setBLEHIDEnabled(!useBLEHID)
    }

    func setBLEHIDProfile(_ profile: HIDProfile) {
        guard bleHIDPreferredProfile != profile else { return }
        bleHIDPreferredProfile = profile

        if useBLEHID {
            bleHIDCommander.switchProfile(profile)
            addLog("BLE HID profile set: \(profile.rawValue)")
        }
    }

    func disconnectBLEHIDAndMakeDiscoverable() {
        bleHIDCommander.disconnectAndMakeDiscoverable()
        addLog("BLE HID: disconnected current host and restarted discoverable mode")
    }

    func startBLEHostScan() {
        bleHIDCommander.startHostScan()
        addLog("BLE HID host scan started")
    }

    func stopBLEHostScan() {
        bleHIDCommander.stopHostScan()
        addLog("BLE HID host scan stopped")
    }

    func connectBLEHost(_ host: HIDHostCandidate) {
        bleHIDCommander.connectToHost(host)
        addLog("BLE HID host connect requested: \(host.name)")
    }

    func disconnectBLEHost() {
        bleHIDCommander.disconnectHostConnection()
        addLog("BLE HID host disconnected")
    }

    // MARK: - HID Mode Change (Backend-driven)

    private func handleSetHIDMode(_ cmd: SetHIDModeMessage) {
        guard let profile = HIDProfile(rawValue: cmd.hidMode) else {
            addLog("Unknown HID mode: \(cmd.hidMode)")
            return
        }

        if useBLEHID {
            // Switch the iOS BLE HID commander profile
            bleHIDPreferredProfile = profile
            addLog("HID mode change → BLE HID: \(profile.rawValue)")
            bleHIDCommander.switchProfile(profile)
        } else {
            // Map HIDProfile to ESP32 mode API
            let esp32ModeString: String
            switch profile {
            case .gamepad: esp32ModeString = "gamepad"
            case .switchPro: esp32ModeString = "switch_controller"
            case .combo: esp32ModeString = "combo"
            case .mouse: esp32ModeString = "mouse_only"
            case .keyboard: esp32ModeString = "keyboard_only"
            }
            addLog("HID mode change → ESP32: \(esp32ModeString)")
            Task {
                let ok = await esp32Client.setMode(mode: esp32ModeString)
                if ok {
                    await queryESP32Mode()
                } else {
                    addLog("ESP32 mode change failed")
                }
            }
        }
    }

    // MARK: - HID Command → ESP32 or BLE HID Forwarding

    private func handleHIDCommand(_ cmd: HIDCommandMessage) {
        let receivedAt = Date().timeIntervalSince1970
        commandsSent += 1
        let target = useBLEHID ? "BLE HID" : "ESP32"
        addLog("HID: \(cmd.action) → \(target)")

        Task {
            let forwardedAt = Date().timeIntervalSince1970
            let result: (success: Bool, completedAt: Double, error: String?)

            if useBLEHID {
                await MainActor.run { self.lastBackendBLEInputAt = Date().timeIntervalSince1970 }
                result = routeHIDToBLEHID(action: cmd.action, params: cmd.params ?? [:])
            } else {
                result = await esp32Client.sendCommand(action: cmd.action, params: cmd.params ?? [:])
            }

            await MainActor.run {
                if result.success { self.commandsAcked += 1 }
            }

            let ack = CommandAckMessage(
                commandId: cmd.commandId ?? "",
                commandSequence: cmd.commandSequence,
                receivedAt: receivedAt,
                forwardedAt: forwardedAt,
                completedAt: result.completedAt,
                success: result.success,
                error: result.error
            )
            await MainActor.run { self.wsManager.sendJSON(ack) }
        }
    }

    // MARK: - Game Command → ESP32 or BLE HID Forwarding

    private func handleGameCommand(_ cmd: GameCommandMessage) {
        let receivedAt = Date().timeIntervalSince1970
        commandsSent += 1
        let target = useBLEHID ? "BLE HID" : "ESP32"
        addLog("Game: \(cmd.commandType)/\(cmd.action) → \(target)")

        // Convert AnyCodableValue params to [String: Double] for ESP32
        var doubleParams: [String: Double] = [:]
        if let params = cmd.params {
            for (k, v) in params { doubleParams[k] = v.doubleValue }
        }

        Task {
            let forwardedAt = Date().timeIntervalSince1970
            let result: (success: Bool, completedAt: Double, error: String?)

            if useBLEHID {
                await MainActor.run { self.lastBackendBLEInputAt = Date().timeIntervalSince1970 }
                result = routeGameToBLEHID(commandType: cmd.commandType, action: cmd.action, params: doubleParams)
            } else {
                result = await esp32Client.sendCommand(action: cmd.action, params: doubleParams)
            }

            await MainActor.run {
                if result.success { self.commandsAcked += 1 }
            }

            let ack = CommandAckMessage(
                commandId: cmd.commandId ?? "",
                commandSequence: cmd.commandSequence,
                receivedAt: receivedAt,
                forwardedAt: forwardedAt,
                completedAt: result.completedAt,
                success: result.success,
                error: result.error
            )
            await MainActor.run { self.wsManager.sendJSON(ack) }
        }
    }

    // MARK: - BLE HID Routing

    private func routeHIDToBLEHID(action: String, params: [String: Double]) -> (success: Bool, completedAt: Double, error: String?) {
        guard bleHIDCommander.isConnected else {
            return (false, Date().timeIntervalSince1970, "BLE HID not connected")
        }
        switch action {
        case "move":
            let dx = Int8(clamping: Int(params["dx"] ?? 0))
            let dy = Int8(clamping: Int(params["dy"] ?? 0))
            bleHIDCommander.mouseMove(dx: dx, dy: dy)
        case "click":
            bleHIDCommander.mouseClick()
        case "press":
            bleHIDCommander.mouseButton(buttons: 0x01)
        case "release":
            bleHIDCommander.mouseButton(buttons: 0x00)
        default:
            return (false, Date().timeIntervalSince1970, "Unknown HID action: \(action)")
        }
        return (true, Date().timeIntervalSince1970, nil)
    }

    private func routeGameToBLEHID(commandType: String, action: String, params: [String: Double]) -> (success: Bool, completedAt: Double, error: String?) {
        guard bleHIDCommander.isConnected else {
            return (false, Date().timeIntervalSince1970, "BLE HID not connected")
        }

        switch commandType {
        case "gamepad":
            switch action {
            case "button_press":
                let idx = Int(params["button_index"] ?? 0)
                bleHIDCommander.gamepadButtonPress(buttonIndex: idx)
            case "button_release":
                let idx = Int(params["button_index"] ?? 0)
                bleHIDCommander.gamepadButtonRelease(buttonIndex: idx)
            case "stick":
                let left = Int(params["stick_id"] ?? 0) == 0  // 0=left, 1=right
                let x = UInt8(clamping: Int(params["x"] ?? 128))
                let y = UInt8(clamping: Int(params["y"] ?? 128))
                bleHIDCommander.gamepadSetStick(left: left, x: x, y: y)
            case "dpad":
                let dir = UInt8(clamping: Int(params["direction"] ?? 0x0F))
                bleHIDCommander.gamepadSetHat(dir)
            default:
                return (false, Date().timeIntervalSince1970, "Unknown gamepad action: \(action)")
            }
        case "mouse":
            return routeHIDToBLEHID(action: action, params: params)
        case "keyboard":
            switch action {
            case "key_press":
                let key = UInt8(clamping: Int(params["key"] ?? 0))
                let mod = UInt8(clamping: Int(params["modifier"] ?? 0))
                bleHIDCommander.keyPress(modifier: mod, keys: [key])
            case "key_release":
                bleHIDCommander.keyRelease()
            default:
                return (false, Date().timeIntervalSince1970, "Unknown keyboard action: \(action)")
            }
        default:
            return (false, Date().timeIntervalSince1970, "Unknown command type: \(commandType)")
        }
        return (true, Date().timeIntervalSince1970, nil)
    }

    // MARK: - Manual BLE HID controls (local UI)

    private func canSendManualBLEInput() -> Bool {
        guard useBLEHID, bleHIDCommander.isConnected else { return false }
        if wsManager.isConnected {
            let now = Date().timeIntervalSince1970
            if now - lastBackendBLEInputAt < manualBLEBackoffWindowS {
                return false
            }
        }
        return true
    }

    func sendManualMouseDelta(dx: Int, dy: Int) {
        guard canSendManualBLEInput() else { return }
        let clampedDX = Int8(clamping: dx)
        let clampedDY = Int8(clamping: dy)
        bleHIDCommander.mouseMove(dx: clampedDX, dy: clampedDY)
    }

    func sendManualMouseClick() {
        guard canSendManualBLEInput() else { return }
        bleHIDCommander.mouseClick()
    }

    func sendManualKeyboardUsage(usage: UInt8, modifier: UInt8 = 0) {
        guard canSendManualBLEInput() else { return }
        bleHIDCommander.keyPress(modifier: modifier, keys: [usage])
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.03) { [weak self] in
            self?.bleHIDCommander.keyRelease()
        }
    }

    func sendManualKeyboardText(_ text: String) {
        guard canSendManualBLEInput() else { return }
        Task { @MainActor in
            for character in text {
                guard let (usage, modifier) = usageForCharacter(character) else { continue }
                bleHIDCommander.keyPress(modifier: modifier, keys: [usage])
                try? await Task.sleep(nanoseconds: 25_000_000)
                bleHIDCommander.keyRelease()
                try? await Task.sleep(nanoseconds: 15_000_000)
            }
        }
    }

    func sendManualGamepadButton(index: Int, pressed: Bool) {
        guard canSendManualBLEInput() else { return }
        if pressed {
            bleHIDCommander.gamepadButtonPress(buttonIndex: index)
        } else {
            bleHIDCommander.gamepadButtonRelease(buttonIndex: index)
        }
    }

    func sendManualGamepadHat(direction: UInt8) {
        guard canSendManualBLEInput() else { return }
        bleHIDCommander.gamepadSetHat(direction)
    }

    func clearManualGamepadHat() {
        guard canSendManualBLEInput() else { return }
        bleHIDCommander.gamepadSetHat(0x0F)
    }

    func sendManualSwitchSyncPress() {
        guard canSendManualBLEInput() else { return }
        bleHIDCommander.gamepadButtonPress(buttonIndex: 4) // L
        bleHIDCommander.gamepadButtonPress(buttonIndex: 5) // R
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in
            guard let self, self.canSendManualBLEInput() else { return }
            self.bleHIDCommander.gamepadButtonRelease(buttonIndex: 4)
            self.bleHIDCommander.gamepadButtonRelease(buttonIndex: 5)
        }
    }

    private func usageForCharacter(_ c: Character) -> (UInt8, UInt8)? {
        if let scalar = c.unicodeScalars.first?.value {
            switch scalar {
            case 97 ... 122: // a-z
                return (UInt8(scalar - 93), 0) // 'a' -> 0x04
            case 65 ... 90: // A-Z
                return (UInt8(scalar - 61), 0x02) // shift + key
            case 49 ... 57: // 1-9
                return (UInt8(scalar - 19), 0)
            case 48: // 0
                return (0x27, 0)
            case 32: return (0x2C, 0) // space
            case 10: return (0x28, 0) // enter
            case 44: return (0x36, 0) // ,
            case 46: return (0x37, 0) // .
            case 47: return (0x38, 0) // /
            case 59: return (0x33, 0) // ;
            case 39: return (0x34, 0) // '
            case 45: return (0x2D, 0) // -
            case 61: return (0x2E, 0) // =
            case 33: return (0x1E, 0x02) // !
            case 64: return (0x1F, 0x02) // @
            case 35: return (0x20, 0x02) // #
            case 36: return (0x21, 0x02) // $
            case 37: return (0x22, 0x02) // %
            case 94: return (0x23, 0x02) // ^
            case 38: return (0x24, 0x02) // &
            case 42: return (0x25, 0x02) // *
            case 40: return (0x26, 0x02) // (
            case 41: return (0x27, 0x02) // )
            default: return nil
            }
        }
        return nil
    }

    // MARK: - Status Reporting

    private func sendStatus() {
        guard wsManager.isConnected else { return }
        let hasTarget = dongleController.currentLat != 0 || dongleController.currentLon != 0
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
            currentLatitude: hasTarget ? dongleController.currentLat : nil,
            currentLongitude: hasTarget ? dongleController.currentLon : nil,
            gpsAccurate: hasTarget ? gpsAccurate : nil,
            gpsDriftMeters: hasTarget ? gpsDriftMeters : nil,
            uptimeSeconds: Date().timeIntervalSince(startTime)
        )
        wsManager.sendJSON(status)
    }

    private func sendLocationStatus() {
        guard locationWSManager.isConnected else { return }
        let target = (dongleController.currentLat, dongleController.currentLon)
        guard target.0 != 0 || target.1 != 0 else { return }
        guard iosReportedLat != 0 || iosReportedLon != 0 else { return }

        let msg = LocationStatusMessage(
            spoofedLat: target.0, spoofedLon: target.1,
            actualLat: iosReportedLat, actualLon: iosReportedLon,
            driftMeters: gpsDriftMeters, isAccurate: gpsAccurate
        )
        locationWSManager.sendJSON(msg)
    }

    // MARK: - DNS Filter Control

    func toggleDNSFilter() {
        dnsFilterEnabled.toggle()
        if dnsFilterEnabled {
            Task { await dnsFilterManager.enable() }
            addLog("DNS filter: enabling")
        } else {
            dnsFilterManager.disable()
            addLog("DNS filter: disabling")
        }
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

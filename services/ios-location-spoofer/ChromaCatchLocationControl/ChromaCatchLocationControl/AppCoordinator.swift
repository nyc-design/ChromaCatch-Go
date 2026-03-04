import Combine
import Foundation

/// Central orchestrator for location spoofing only.
///
/// Responsibilities:
/// - Connect to location service (`/ws/location`)
/// - Drive iTools dongle initialization + NMEA forwarding
/// - Verify spoof drift via CLLocationManager
/// - Control DNS location guard tunnel
@MainActor
class AppCoordinator: ObservableObject {
    let eaManager: EAManager
    let bleManager: BLEManager
    let dongleController: DongleController
    let locationWSManager: WebSocketManager
    let locationMonitor: LocationMonitor
    let locationKeepAlive: LocationKeepAlive
    let dnsFilterManager: DNSFilterManager

    @Published var logs: [LogEntry] = []
    @Published var isLocationRunning = false

    // GPS verification — mirrored from LocationMonitor for SwiftUI binding
    @Published var gpsAccurate: Bool = false
    @Published var gpsDriftMeters: Double = 0
    @Published var iosReportedLat: Double = 0
    @Published var iosReportedLon: Double = 0

    // DNS filter toggle state
    @Published var dnsFilterEnabled: Bool {
        didSet { UserDefaults.standard.set(dnsFilterEnabled, forKey: "dnsFilterEnabled") }
    }

    // Configuration (persisted)
    @Published var locationServiceURL: String {
        didSet { UserDefaults.standard.set(locationServiceURL, forKey: "locationServiceURL") }
    }
    @Published var apiKey: String {
        didSet { UserDefaults.standard.set(apiKey, forKey: "apiKey") }
    }
    @Published var clientId: String {
        didSet { UserDefaults.standard.set(clientId, forKey: "clientId") }
    }

    private var cancellables = Set<AnyCancellable>()
    let startTime = Date()

    private static let defaultLocationURL = "wss://8001--main--chromacatch-go-agents--nyc-design.apps.coder.tapiavala.com/ws/location"

    init() {
        if let old = UserDefaults.standard.string(forKey: "locationServiceURL"), old.contains("localhost") {
            UserDefaults.standard.removeObject(forKey: "locationServiceURL")
        }

        let savedLocationURL = UserDefaults.standard.string(forKey: "locationServiceURL") ?? Self.defaultLocationURL
        let savedKey = UserDefaults.standard.string(forKey: "apiKey") ?? ""
        let savedClientId = UserDefaults.standard.string(forKey: "clientId") ?? "ios-location-spoofer"
        let savedDNSFilter = UserDefaults.standard.bool(forKey: "dnsFilterEnabled")

        self.locationServiceURL = savedLocationURL
        self.apiKey = savedKey
        self.clientId = savedClientId
        self.dnsFilterEnabled = savedDNSFilter

        var coordinator: AppCoordinator?
        let logFn: (String) -> Void = { message in
            Task { @MainActor in
                coordinator?.addLog(message)
            }
        }

        eaManager = EAManager(log: logFn)
        bleManager = BLEManager(log: logFn)
        dongleController = DongleController(bleManager: bleManager, log: logFn)
        locationWSManager = WebSocketManager(
            url: URL(string: savedLocationURL) ?? URL(string: "wss://localhost:8001/ws/location")!,
            apiKey: savedKey,
            clientId: savedClientId,
            label: "LOC",
            log: logFn
        )
        locationMonitor = LocationMonitor(log: logFn)
        locationKeepAlive = LocationKeepAlive(log: logFn)
        dnsFilterManager = DNSFilterManager(log: logFn)

        coordinator = self

        setupCallbacks()
        setupLocationMonitorBindings()

        if savedDNSFilter {
            Task { await dnsFilterManager.enable() }
        }
    }

    private func setupCallbacks() {
        locationWSManager.onMessage = { [weak self] text in
            guard let self = self else { return }
            Task { @MainActor in
                self.handleLocationMessage(text)
            }
        }
    }

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

        // keep verification target synced with dongle target
        dongleController.$currentLat
            .combineLatest(dongleController.$currentLon)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] lat, lon in
                self?.locationMonitor.targetLat = lat
                self?.locationMonitor.targetLon = lon
            }
            .store(in: &cancellables)

        // report verification state periodically
        locationMonitor.$driftMeters
            .combineLatest(locationMonitor.$isAccurate)
            .throttle(for: .seconds(5), scheduler: DispatchQueue.main, latest: true)
            .sink { [weak self] _, _ in
                self?.sendLocationStatus()
            }
            .store(in: &cancellables)
    }

    // MARK: - Lifecycle

    func start() {
        startLocation()
    }

    func stop() {
        stopLocation()
    }

    // MARK: - Location Subsystem

    func startLocation() {
        guard !isLocationRunning else { return }
        isLocationRunning = true
        addLog("Starting location connection...")

        locationWSManager.connect()
        locationKeepAlive.start()
        locationMonitor.startMonitoring()

        addLog("Location connection started")
    }

    func stopLocation() {
        guard isLocationRunning else { return }
        isLocationRunning = false

        dongleController.stop()
        locationWSManager.disconnect()
        locationKeepAlive.stop()
        locationMonitor.stopMonitoring()

        addLog("Location stopped")
    }

    // MARK: - Dongle Pairing

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

    // MARK: - WS Message Handling

    private func handleLocationMessage(_ text: String) {
        guard let msg = IncomingMessage.parse(text) else {
            addLog("Unknown location msg: \(text.prefix(100))")
            return
        }

        switch msg {
        case .locationUpdate(let loc):
            addLog("GPS: \(String(format: "%.6f", loc.latitude)), \(String(format: "%.6f", loc.longitude))")
            dongleController.updateCoordinates(
                lat: loc.latitude,
                lon: loc.longitude,
                altitude: loc.altitude,
                speed: loc.speedKnots,
                heading: loc.heading
            )
        case .ping:
            locationWSManager.sendJSON(HeartbeatPong())
        case .unknown(let type):
            addLog("Unhandled location type: \(type)")
        }
    }

    // MARK: - Location Status Reporting

    private func sendLocationStatus() {
        guard locationWSManager.isConnected else { return }

        let target = (dongleController.currentLat, dongleController.currentLon)
        guard target.0 != 0 || target.1 != 0 else { return }
        guard iosReportedLat != 0 || iosReportedLon != 0 else { return }

        let msg = LocationStatusMessage(
            spoofedLat: target.0,
            spoofedLon: target.1,
            actualLat: iosReportedLat,
            actualLon: iosReportedLon,
            driftMeters: gpsDriftMeters,
            isAccurate: gpsAccurate
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
        print("[CC-SPOOF] \(message)")
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

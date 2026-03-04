import Foundation
import Combine

/// Orchestrates the iTools dongle BLE protocol:
/// 1. AT+CN initialization (activate connection)
/// 2. AT+RP status checks
/// 3. Continuous NMEA (RMC + GGA) transmission at ~1s intervals
class DongleController: ObservableObject {
    private let bleManager: BLEManager
    private let nmeaGenerator = NMEAGenerator()
    private let log: (String) -> Void

    @Published var isInitialized = false
    @Published var isForwarding = false
    @Published var currentLat: Double = 0.0
    @Published var currentLon: Double = 0.0
    @Published var currentAltitude: Double = 10.0
    @Published var currentSpeed: Double = 0.0
    @Published var currentHeading: Double = 0.0
    @Published var nmeaSentCount: Int = 0

    private var nmeaTimer: Timer?
    private var cancellables = Set<AnyCancellable>()
    private var initPhase: InitPhase = .idle

    private enum InitPhase {
        case idle, sentCN, waitingCN, sentRP, waitingRP, ready
    }
    private var cnRetryCount = 0
    private static let maxCNRetries = 3

    init(bleManager: BLEManager, log: @escaping (String) -> Void = { print("Dongle: \($0)") }) {
        self.bleManager = bleManager
        self.log = log

        // When BLE characteristics are discovered, start init sequence
        bleManager.onReady = { [weak self] in
            self?.startInitSequence()
        }

        // Monitor FF02 notifications for ACK parsing
        bleManager.onNotification = { [weak self] data in
            self?.handleNotification(data)
        }

        // Re-initialize if BLE reconnects
        bleManager.$isConnected
            .receive(on: DispatchQueue.main)
            .sink { [weak self] connected in
                if !connected {
                    self?.stopNMEALoop()
                    self?.isInitialized = false
                    self?.isForwarding = false
                    self?.initPhase = .idle
                }
            }
            .store(in: &cancellables)
    }

    /// Update the target coordinates (called when backend sends LocationUpdateMessage)
    func updateCoordinates(lat: Double, lon: Double, altitude: Double = 10.0,
                           speed: Double = 0.0, heading: Double = 0.0) {
        currentLat = lat
        currentLon = lon
        currentAltitude = altitude
        currentSpeed = speed
        currentHeading = heading
        log("Coordinates updated: \(lat), \(lon) alt=\(altitude)")
    }

    /// Stop sending NMEA and disconnect
    func stop() {
        stopNMEALoop()
        isInitialized = false
        isForwarding = false
        initPhase = .idle
    }

    // MARK: - Initialization Sequence

    private func startInitSequence() {
        cnRetryCount = 0
        sendCNPair()
    }

    private func sendCNPair() {
        cnRetryCount += 1
        log("AT+CN init attempt \(cnRetryCount)/\(Self.maxCNRetries)...")
        initPhase = .sentCN
        bleManager.writeString("AT+CN\r\n")
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.bleManager.writeString("AT+CN\r\n")
            self?.initPhase = .waitingCN
        }
    }

    private func handleNotification(_ data: Data) {
        guard let str = String(data: data, encoding: .ascii) else {
            log("Non-ASCII notification: \(data.map { String(format: "%02x", $0) }.joined())")
            return
        }

        let trimmed = str.trimmingCharacters(in: .controlCharacters)

        switch initPhase {
        case .waitingCN:
            if trimmed.contains("+CN=01") {
                log("AT+CN acknowledged (+CN=01)")
                initPhase = .sentRP
                DispatchQueue.global().asyncAfter(deadline: .now() + 0.2) { [weak self] in
                    self?.bleManager.writeString("AT+RP\r\n")
                    self?.initPhase = .waitingRP
                }
            } else if trimmed.contains("+CN=00") {
                log("AT+CN returned +CN=00 (not ready)")
                if cnRetryCount < Self.maxCNRetries {
                    DispatchQueue.global().asyncAfter(deadline: .now() + 1.0) { [weak self] in
                        self?.sendCNPair()
                    }
                } else {
                    log("AT+CN failed after \(Self.maxCNRetries) attempts — try power cycling the dongle")
                }
            }

        case .waitingRP:
            if trimmed.hasPrefix("RP=") {
                let status = parseRPStatus(trimmed)
                log("Initial AT+RP response, status: \(status)")
                DispatchQueue.main.async {
                    self.isInitialized = true
                    self.isForwarding = (status == ">" || status == "A")
                }
                initPhase = .ready
                startNMEALoop()
            }

        case .ready:
            // During NMEA loop, monitor RP responses
            if trimmed.hasPrefix("RP=") {
                let status = parseRPStatus(trimmed)
                DispatchQueue.main.async {
                    self.isForwarding = (status == ">" || status == "A")
                }
                if status == "." {
                    log("Warning: dongle not forwarding (RP status '.') — EA session may not be active")
                }
            }

        default:
            break
        }
    }

    /// Parse the status byte from an RP response (e.g., "RP=01\x00\x0000>\x0B" → ">")
    private func parseRPStatus(_ response: String) -> String {
        // Status byte is at index 6 in the RP=01... response
        guard response.count >= 7 else { return "?" }
        let idx = response.index(response.startIndex, offsetBy: 6)
        return String(response[idx])
    }

    // MARK: - NMEA Loop

    private func startNMEALoop() {
        guard nmeaTimer == nil else { return }
        log("Starting NMEA loop (1s interval)")

        // Send first NMEA pair immediately
        sendNMEAPair()

        // Then repeat every 1 second
        DispatchQueue.main.async {
            self.nmeaTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
                self?.sendNMEAPair()
            }
        }
    }

    private func stopNMEALoop() {
        nmeaTimer?.invalidate()
        nmeaTimer = nil
        log("NMEA loop stopped")
    }

    private func sendNMEAPair() {
        guard bleManager.isReady else { return }
        guard currentLat != 0.0 || currentLon != 0.0 else {
            // Don't send 0,0 — wait for first coordinate update
            return
        }

        let (rmc, gga) = nmeaGenerator.makePair(
            lat: currentLat, lon: currentLon,
            altitude: currentAltitude,
            speed: currentSpeed, heading: currentHeading
        )

        // Write RMC → GGA → AT+RP (sequential with small delays)
        bleManager.writeString(rmc)
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.05) { [weak self] in
            self?.bleManager.writeString(gga)
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.1) { [weak self] in
            self?.bleManager.writeString("AT+RP\r\n")
        }

        DispatchQueue.main.async {
            self.nmeaSentCount += 1
        }
    }
}

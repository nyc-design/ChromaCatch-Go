import ExternalAccessory
import Foundation

/// Manages the External Accessory session with the iTools dongle (BT-01414-CORE).
///
/// The EA session activates the dongle's GPS forwarding mode via iAP2/MFi handshake.
/// Without an active EA session, the dongle may stop forwarding NMEA after ~30 seconds.
///
/// EA accessory discovery is notification-driven — no polling needed.
/// If BT-01414-CORE is paired in iPhone Settings > Bluetooth, iOS will fire
/// .EAAccessoryDidConnect when the iAP2 layer completes.
class EAManager: NSObject, ObservableObject, StreamDelegate {
    static let protocolString = "com.feasycom.BLEAssistant"

    private var accessory: EAAccessory?
    private var session: EASession?
    private var inputStream: InputStream?
    private var outputStream: OutputStream?
    private let log: (String) -> Void

    @Published var isConnected = false

    init(log: @escaping (String) -> Void = { print("EA: \($0)") }) {
        self.log = log
        super.init()

        NotificationCenter.default.addObserver(
            self,
            selector: #selector(accessoryDidConnect(_:)),
            name: .EAAccessoryDidConnect,
            object: nil
        )
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(accessoryDidDisconnect(_:)),
            name: .EAAccessoryDidDisconnect,
            object: nil
        )
        EAAccessoryManager.shared().registerForLocalNotifications()

        // Log diagnostics and check for already-connected accessories
        logDiagnostics()
    }

    deinit {
        NotificationCenter.default.removeObserver(self)
        closeSession()
    }

    // MARK: - Diagnostics

    private func logDiagnostics() {
        let declared = Bundle.main.object(forInfoDictionaryKey: "UISupportedExternalAccessoryProtocols") as? [String]
        print("[EA] Info.plist EA protocols: \(declared ?? ["MISSING!"])")

        let bgModes = Bundle.main.object(forInfoDictionaryKey: "UIBackgroundModes") as? [String]
        print("[EA] Info.plist UIBackgroundModes: \(bgModes ?? ["MISSING!"])")

        let btDesc = Bundle.main.object(forInfoDictionaryKey: "NSBluetoothAlwaysUsageDescription") as? String
        print("[EA] Info.plist NSBluetoothAlwaysUsageDescription: \(btDesc != nil ? "present" : "MISSING!")")

        let accessories = EAAccessoryManager.shared().connectedAccessories
        print("[EA] Found \(accessories.count) connected EA accessories")

        if accessories.isEmpty {
            // BT-01414-CORE may be paired in Settings but the iAP2/MFi handshake
            // hasn't completed. This is normal — the dongle's MFi coprocessor may
            // not pass Apple's authentication. Location spoofing can still work
            // via BLE NMEA injection but may time out after ~30s without EA.
            log("EA: 0 accessories (iAP2 auth may not have completed for BT-01414-CORE)")
        } else {
            for acc in accessories {
                let match = acc.protocolStrings.contains(Self.protocolString) ? "MATCH" : "no match"
                log("EA: \(acc.name) — protocols: \(acc.protocolStrings) [\(match)]")
                print("[EA]   \(acc.name) manufacturer=\(acc.manufacturer) model=\(acc.modelNumber) serial=\(acc.serialNumber) fw=\(acc.firmwareRevision) hw=\(acc.hardwareRevision) protocols=\(acc.protocolStrings)")
            }
            // Try to open session with matching accessory
            if let target = accessories.first(where: { $0.protocolStrings.contains(Self.protocolString) }) {
                openSession(with: target)
            } else {
                log("EA: no accessory matches protocol '\(Self.protocolString)'")
            }
        }
    }

    // MARK: - Session Management

    private func openSession(with accessory: EAAccessory) {
        guard let session = EASession(
            accessory: accessory,
            forProtocol: Self.protocolString
        ) else {
            log("EA: failed to open session — protocol '\(Self.protocolString)' rejected")
            return
        }

        self.accessory = accessory
        self.session = session

        if let input = session.inputStream {
            self.inputStream = input
            input.delegate = self
            input.schedule(in: .main, forMode: .default)
            input.open()
        }

        if let output = session.outputStream {
            self.outputStream = output
            output.delegate = self
            output.schedule(in: .main, forMode: .default)
            output.open()
        }

        DispatchQueue.main.async { self.isConnected = true }
        log("EA: session opened with \(accessory.name) (ID: \(accessory.connectionID))")
    }

    private func closeSession() {
        inputStream?.close()
        inputStream?.remove(from: .main, forMode: .default)
        inputStream = nil

        outputStream?.close()
        outputStream?.remove(from: .main, forMode: .default)
        outputStream = nil

        session = nil
        accessory = nil
        DispatchQueue.main.async { self.isConnected = false }
    }

    // MARK: - Notifications (auto-fires when accessory completes iAP2 handshake)

    @objc private func accessoryDidConnect(_ notification: Notification) {
        guard let accessory = notification.userInfo?[EAAccessoryKey] as? EAAccessory else { return }
        log("EA: accessory connected — \(accessory.name) protocols: \(accessory.protocolStrings)")
        if accessory.protocolStrings.contains(Self.protocolString) {
            openSession(with: accessory)
        }
    }

    @objc private func accessoryDidDisconnect(_ notification: Notification) {
        guard let disconnected = notification.userInfo?[EAAccessoryKey] as? EAAccessory,
              disconnected.connectionID == accessory?.connectionID else { return }
        log("EA: accessory disconnected")
        closeSession()
    }

    // MARK: - StreamDelegate

    func stream(_ aStream: Stream, handle eventCode: Stream.Event) {
        switch eventCode {
        case .hasBytesAvailable:
            guard let input = aStream as? InputStream else { return }
            var buffer = [UInt8](repeating: 0, count: 1024)
            let bytesRead = input.read(&buffer, maxLength: buffer.count)
            if bytesRead > 0 {
                let data = Data(buffer[0..<bytesRead])
                log("EA: received \(bytesRead)B: \(data.map { String(format: "%02x", $0) }.joined())")
            }

        case .hasSpaceAvailable:
            break

        case .errorOccurred:
            log("EA: stream error — \(aStream.streamError?.localizedDescription ?? "unknown")")
            closeSession()
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                self?.logDiagnostics()
            }

        case .endEncountered:
            log("EA: stream ended")
            closeSession()

        default:
            break
        }
    }
}

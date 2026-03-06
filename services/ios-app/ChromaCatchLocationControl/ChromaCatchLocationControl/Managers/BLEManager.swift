import CoreBluetooth
import Foundation

/// Discovered BLE device for the device picker UI.
struct DiscoveredDevice: Identifiable {
    let id: UUID  // peripheral.identifier
    let name: String
    let peripheral: CBPeripheral
    let rssi: Int
}

/// Manages BLE connection to iTools dongle's BT-01414-APP identity.
/// Scanning is manual (user-initiated), connection is user-selected from device list.
class BLEManager: NSObject, ObservableObject {
    // GATT UUIDs for iTools dongle
    static let serviceUUID = CBUUID(string: "FF12")
    static let writeNoResponseChar = CBUUID(string: "FF01")
    static let notifyChar = CBUUID(string: "FF02")
    static let readWriteChar = CBUUID(string: "FF03")

    static let deviceNamePrefix = "BT-01414"

    private var centralManager: CBCentralManager!
    private var peripheral: CBPeripheral?

    private var ff01Char: CBCharacteristic?
    private var ff02Char: CBCharacteristic?
    private var ff03Char: CBCharacteristic?

    @Published var isConnected = false
    @Published var lastACKData: Data?
    @Published var rpStatus: String = "?"
    @Published var discoveredDevices: [DiscoveredDevice] = []
    @Published var isScanning = false
    @Published var connectedDeviceName: String?

    /// Called when data arrives on FF02 (notifications)
    var onNotification: ((Data) -> Void)?

    /// Called when a write to FF03 completes (success/failure)
    var onWriteComplete: ((Bool, Error?) -> Void)?

    /// Called when all characteristics are discovered and ready
    var onReady: (() -> Void)?

    private let log: (String) -> Void

    init(log: @escaping (String) -> Void = { print("BLE: \($0)") }) {
        self.log = log
        super.init()
        centralManager = CBCentralManager(
            delegate: self,
            queue: DispatchQueue(label: "ble.queue"),
            options: [CBCentralManagerOptionRestoreIdentifierKey: "iToolsBLECentral"]
        )
    }

    func startScanning() {
        guard centralManager.state == .poweredOn else {
            log("Cannot scan — Bluetooth not powered on")
            return
        }
        guard !isScanning else { return }
        DispatchQueue.main.async {
            self.discoveredDevices = []
            self.isScanning = true
        }
        centralManager.scanForPeripherals(
            withServices: nil,
            options: [CBCentralManagerScanOptionAllowDuplicatesKey: false]
        )
        log("Scanning for dongles...")
    }

    func stopScanning() {
        centralManager.stopScan()
        DispatchQueue.main.async { self.isScanning = false }
    }

    /// User-initiated connection to a selected device from the picker.
    func connect(to device: DiscoveredDevice) {
        stopScanning()
        self.peripheral = device.peripheral
        centralManager.connect(device.peripheral, options: nil)
        log("Connecting to \(device.name)...")
    }

    /// Write data to FF03 (write with response — reliable for NMEA/AT commands)
    func writeToFF03(_ data: Data) {
        guard let char = ff03Char, let peripheral = peripheral else {
            log("Cannot write — FF03 not discovered or not connected")
            return
        }
        peripheral.writeValue(data, for: char, type: .withResponse)
    }

    /// Write string to FF03 (convenience for AT commands and NMEA)
    func writeString(_ string: String) {
        guard let data = string.data(using: .ascii) else {
            log("Failed to encode string to ASCII")
            return
        }
        writeToFF03(data)
    }

    /// Write data to FF01 (write without response — fire and forget)
    func writeToFF01(_ data: Data) {
        guard let char = ff01Char, let peripheral = peripheral else { return }
        peripheral.writeValue(data, for: char, type: .withoutResponse)
    }

    var isReady: Bool {
        isConnected && ff03Char != nil && ff02Char != nil
    }

    func disconnect() {
        if let peripheral = peripheral {
            centralManager.cancelPeripheralConnection(peripheral)
        }
        self.peripheral = nil
        DispatchQueue.main.async {
            self.connectedDeviceName = nil
        }
    }
}

// MARK: - CBCentralManagerDelegate
extension BLEManager: CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        let stateNames = [
            0: "unknown", 1: "resetting", 2: "unsupported",
            3: "unauthorized", 4: "poweredOff", 5: "poweredOn",
        ]
        let stateName = stateNames[central.state.rawValue] ?? "rawValue(\(central.state.rawValue))"

        switch central.state {
        case .poweredOn:
            log("Bluetooth powered on")
        case .poweredOff:
            log("Bluetooth powered off")
            DispatchQueue.main.async {
                self.isScanning = false
                self.isConnected = false
            }
        case .unauthorized:
            log("Bluetooth UNAUTHORIZED — check Settings > Privacy > Bluetooth")
        case .unsupported:
            log("Bluetooth unsupported on this device")
        case .resetting:
            log("Bluetooth resetting...")
            DispatchQueue.main.async { self.isScanning = false }
        default:
            log("Bluetooth state: \(stateName)")
        }
    }

    func centralManager(_ central: CBCentralManager,
                        didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any],
                        rssi RSSI: NSNumber) {
        let name = peripheral.name ?? advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? ""
        guard name.hasPrefix(Self.deviceNamePrefix) else { return }

        let device = DiscoveredDevice(
            id: peripheral.identifier,
            name: name,
            peripheral: peripheral,
            rssi: RSSI.intValue
        )

        DispatchQueue.main.async {
            // Deduplicate by peripheral identifier
            if !self.discoveredDevices.contains(where: { $0.id == device.id }) {
                self.discoveredDevices.append(device)
                self.log("Found: \(name) (RSSI: \(RSSI))")
            }
        }
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        log("Connected to \(peripheral.name ?? "unknown")")
        peripheral.delegate = self
        peripheral.discoverServices([Self.serviceUUID])
        DispatchQueue.main.async {
            self.isConnected = true
            self.connectedDeviceName = peripheral.name
        }
    }

    func centralManager(_ central: CBCentralManager,
                        didDisconnectPeripheral peripheral: CBPeripheral,
                        error: Error?) {
        log("Disconnected: \(error?.localizedDescription ?? "clean")")
        DispatchQueue.main.async {
            self.isConnected = false
            self.rpStatus = "?"
            self.connectedDeviceName = nil
        }
        ff01Char = nil
        ff02Char = nil
        ff03Char = nil

        // Auto-reconnect only if we had a paired peripheral (not user-initiated disconnect)
        if self.peripheral != nil {
            centralManager.connect(peripheral, options: nil)
            log("Attempting reconnect...")
        }
    }

    func centralManager(_ central: CBCentralManager,
                        didFailToConnect peripheral: CBPeripheral,
                        error: Error?) {
        log("Failed to connect: \(error?.localizedDescription ?? "unknown")")
    }

    // State restoration (background relaunch)
    // Don't auto-reconnect — user must manually scan and connect via the UI.
    func centralManager(_ central: CBCentralManager, willRestoreState dict: [String: Any]) {
        if let peripherals = dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral],
           let restored = peripherals.first {
            log("Restored peripheral: \(restored.name ?? "unknown") — disconnecting (manual pairing required)")
            central.cancelPeripheralConnection(restored)
        }
    }
}

// MARK: - CBPeripheralDelegate
extension BLEManager: CBPeripheralDelegate {
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard let services = peripheral.services else { return }
        for service in services where service.uuid == Self.serviceUUID {
            log("Discovered service FF12")
            peripheral.discoverCharacteristics(
                [Self.writeNoResponseChar, Self.notifyChar, Self.readWriteChar],
                for: service
            )
        }
    }

    func peripheral(_ peripheral: CBPeripheral,
                    didDiscoverCharacteristicsFor service: CBService,
                    error: Error?) {
        guard let characteristics = service.characteristics else { return }
        for char in characteristics {
            switch char.uuid {
            case Self.writeNoResponseChar:
                ff01Char = char
                log("Found FF01 (write-no-response)")
            case Self.notifyChar:
                ff02Char = char
                peripheral.setNotifyValue(true, for: char)
                log("Found FF02 (notify) — subscribed")
            case Self.readWriteChar:
                ff03Char = char
                let maxLen = peripheral.maximumWriteValueLength(for: .withResponse)
                log("Found FF03 (write+read) — max write: \(maxLen) bytes")
            default:
                break
            }
        }

        if ff03Char != nil && ff02Char != nil {
            log("All characteristics ready")
            onReady?()
        }
    }

    func peripheral(_ peripheral: CBPeripheral,
                    didUpdateValueFor characteristic: CBCharacteristic,
                    error: Error?) {
        guard let data = characteristic.value else { return }

        if characteristic.uuid == Self.notifyChar {
            DispatchQueue.main.async { self.lastACKData = data }

            // Parse RP status byte from response
            if let str = String(data: data, encoding: .ascii) {
                if str.hasPrefix("RP=") && str.count >= 7 {
                    let statusByte = String(str[str.index(str.startIndex, offsetBy: 6)])
                    DispatchQueue.main.async { self.rpStatus = statusByte }
                } else if str.hasPrefix("+CN=") {
                    log("AT+CN response: \(str.trimmingCharacters(in: .whitespacesAndNewlines))")
                }
            }

            onNotification?(data)
        }
    }

    func peripheral(_ peripheral: CBPeripheral,
                    didWriteValueFor characteristic: CBCharacteristic,
                    error: Error?) {
        let success = error == nil
        if let error = error {
            log("Write error on \(characteristic.uuid): \(error.localizedDescription)")
        }
        onWriteComplete?(success, error)
    }

    func peripheral(_ peripheral: CBPeripheral,
                    didUpdateNotificationStateFor characteristic: CBCharacteristic,
                    error: Error?) {
        if let error = error {
            log("Notify state error: \(error.localizedDescription)")
        }
    }
}

import CoreBluetooth
import Foundation

/// Manages BLE connection to iTools dongle's BT-01414-APP identity.
/// Handles scanning, connecting, characteristic discovery, write/notify.
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

    /// Called when data arrives on FF02 (notifications)
    var onNotification: ((Data) -> Void)?

    /// Called when a write to FF03 completes (success/failure)
    var onWriteComplete: ((Bool, Error?) -> Void)?

    /// Called when all characteristics are discovered and ready
    var onReady: (() -> Void)?

    private let log: (String) -> Void
    private var isScanning = false

    init(log: @escaping (String) -> Void = { print("BLE: \($0)") }) {
        self.log = log
        super.init()
        print("[BLE] BLEManager init — creating CBCentralManager")
        centralManager = CBCentralManager(
            delegate: self,
            queue: DispatchQueue(label: "ble.queue"),
            options: [CBCentralManagerOptionRestoreIdentifierKey: "iToolsBLECentral"]
        )
        print("[BLE] CBCentralManager created, waiting for state callback")
    }

    func startScanning() {
        guard centralManager.state == .poweredOn else {
            // Only log this once, not on every timer tick
            return
        }
        guard !isScanning else { return }
        isScanning = true
        // Scan without service UUID filter — many BLE devices (including iTools)
        // don't advertise service UUIDs. We filter by device name in didDiscover instead.
        centralManager.scanForPeripherals(
            withServices: nil,
            options: [CBCentralManagerScanOptionAllowDuplicatesKey: false]
        )
        log("Scanning for BT-01414 devices (any service)...")
        print("[BLE] Started scanning (no service filter, matching by name prefix)")
    }

    func stopScanning() {
        centralManager.stopScan()
        isScanning = false
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
    }
}

// MARK: - CBCentralManagerDelegate
extension BLEManager: CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        let stateNames = [
            0: "unknown", 1: "resetting", 2: "unsupported",
            3: "unauthorized", 4: "poweredOff", 5: "poweredOn"
        ]
        let stateName = stateNames[central.state.rawValue] ?? "rawValue(\(central.state.rawValue))"
        print("[BLE] centralManagerDidUpdateState: \(stateName) (\(central.state.rawValue))")

        switch central.state {
        case .poweredOn:
            log("Bluetooth powered on")
            print("[BLE] Powered on — starting scan for service FF12")
            startScanning()
        case .poweredOff:
            log("Bluetooth powered off")
            isScanning = false
            DispatchQueue.main.async { self.isConnected = false }
        case .unauthorized:
            log("Bluetooth UNAUTHORIZED — check Settings > Privacy > Bluetooth")
            print("[BLE] UNAUTHORIZED — user denied Bluetooth permission or NSBluetoothAlwaysUsageDescription missing from Info.plist")
        case .unsupported:
            log("Bluetooth unsupported on this device")
        case .resetting:
            log("Bluetooth resetting...")
            isScanning = false
        default:
            log("Bluetooth state: \(stateName) (\(central.state.rawValue))")
        }
    }

    func centralManager(_ central: CBCentralManager,
                        didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any],
                        rssi RSSI: NSNumber) {
        let name = peripheral.name ?? advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? ""

        // Only log named devices to avoid spam from unnamed beacons
        if !name.isEmpty {
            let serviceUUIDs = advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? []
            print("[BLE] Discovered: \(name) RSSI=\(RSSI) services=\(serviceUUIDs)")
        }

        guard name.hasPrefix(Self.deviceNamePrefix) else { return }

        log("Found dongle: \(name) (RSSI: \(RSSI))")
        self.peripheral = peripheral
        centralManager.stopScan()
        isScanning = false
        centralManager.connect(peripheral, options: nil)
        log("Connecting to \(name)...")
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        log("Connected to \(peripheral.name ?? "unknown")")
        peripheral.delegate = self
        peripheral.discoverServices([Self.serviceUUID])
        DispatchQueue.main.async { self.isConnected = true }
    }

    func centralManager(_ central: CBCentralManager,
                        didDisconnectPeripheral peripheral: CBPeripheral,
                        error: Error?) {
        log("Disconnected: \(error?.localizedDescription ?? "clean")")
        DispatchQueue.main.async {
            self.isConnected = false
            self.rpStatus = "?"
        }
        ff01Char = nil
        ff02Char = nil
        ff03Char = nil

        // Auto-reconnect
        centralManager.connect(peripheral, options: nil)
        log("Attempting reconnect...")
    }

    func centralManager(_ central: CBCentralManager,
                        didFailToConnect peripheral: CBPeripheral,
                        error: Error?) {
        log("Failed to connect: \(error?.localizedDescription ?? "unknown")")
        isScanning = false
        DispatchQueue.global().asyncAfter(deadline: .now() + 2.0) { [weak self] in
            self?.startScanning()
        }
    }

    // State restoration (background relaunch)
    func centralManager(_ central: CBCentralManager, willRestoreState dict: [String: Any]) {
        if let peripherals = dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral],
           let restored = peripherals.first {
            log("Restored peripheral: \(restored.name ?? "unknown")")
            self.peripheral = restored
            restored.delegate = self
            if restored.state == .connected {
                DispatchQueue.main.async { self.isConnected = true }
                restored.discoverServices([Self.serviceUUID])
            }
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

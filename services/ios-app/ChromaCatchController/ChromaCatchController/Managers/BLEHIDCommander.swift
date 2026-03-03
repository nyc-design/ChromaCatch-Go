//
//  BLEHIDCommander.swift
//  ChromaCatchController
//
//  BLE HID peripheral — allows iOS to act as a Bluetooth mouse/keyboard/gamepad
//  to target devices (Switch, 3DS, PC, Android) without requiring ESP32 hardware.
//
//  Reference: https://gist.github.com/conath/c606d95d58bbcb50e9715864eeeecf07
//  Proven approach: BluTouch (App Store) works on iOS 15.5+.
//
//  Key implementation details:
//  - Expanded 128-bit UUIDs bypass Apple's short-form HID blocklist (iOS 14+)
//  - Single HID Report characteristic (2A4D) with Report ID as first byte
//  - Report Reference descriptor (2908) tells hosts which Report ID this char maps to
//  - Generic Access Service with Appearance tells hosts what device type we are
//  - Must handle didReceiveRead for Windows/Mac compatibility
//  - CBPeripheralManager on dedicated queue, UI updates on main thread
//

import CoreBluetooth
import Foundation
import os

// MARK: - BLE UUIDs (expanded 128-bit to bypass Apple's short-form blocklist)

private let kHIDServiceUUID = CBUUID(string: "00001812-0000-1000-8000-00805F9B34FB")
private let kHIDInfoUUID = CBUUID(string: "00002A4A-0000-1000-8000-00805F9B34FB")
private let kHIDReportMapUUID = CBUUID(string: "00002A4B-0000-1000-8000-00805F9B34FB")
private let kHIDControlPointUUID = CBUUID(string: "00002A4C-0000-1000-8000-00805F9B34FB")
private let kHIDReportUUID = CBUUID(string: "00002A4D-0000-1000-8000-00805F9B34FB")
private let kProtocolModeUUID = CBUUID(string: "00002A4E-0000-1000-8000-00805F9B34FB")

private let kGenericAccessUUID = CBUUID(string: "00001800-0000-1000-8000-00805F9B34FB")
private let kAppearanceUUID = CBUUID(string: "00002A01-0000-1000-8000-00805F9B34FB")

private let kBatteryServiceUUID = CBUUID(string: "0000180F-0000-1000-8000-00805F9B34FB")
private let kBatteryLevelUUID = CBUUID(string: "00002A19-0000-1000-8000-00805F9B34FB")

private let kDeviceInfoServiceUUID = CBUUID(string: "0000180A-0000-1000-8000-00805F9B34FB")
private let kManufacturerNameUUID = CBUUID(string: "00002A29-0000-1000-8000-00805F9B34FB")
private let kPnPIDUUID = CBUUID(string: "00002A50-0000-1000-8000-00805F9B34FB")

private let kReportReferenceUUID = CBUUID(string: "00002908-0000-1000-8000-00805F9B34FB")

private let hidLog = Logger(subsystem: "com.chromacatch", category: "BLEHID")

// MARK: - HID Profile

enum HIDProfile: String, CaseIterable {
    case mouse
    case keyboard
    case gamepad
    case combo // mouse + keyboard
}

// MARK: - Report Descriptors

/// Mouse: Report ID 1 — buttons(3bit)+pad(5bit), X(8), Y(8), wheel(8) = 4 bytes
private let mouseReportDescriptor: [UInt8] = [
    0x05, 0x01, // Usage Page (Generic Desktop)
    0x09, 0x02, // Usage (Mouse)
    0xA1, 0x01, // Collection (Application)
    0x85, 0x01, //   Report ID (1)
    0x09, 0x01, //   Usage (Pointer)
    0xA1, 0x00, //   Collection (Physical)
    0x05, 0x09, //     Usage Page (Button)
    0x19, 0x01, //     Usage Minimum (1)
    0x29, 0x03, //     Usage Maximum (3)
    0x15, 0x00, //     Logical Minimum (0)
    0x25, 0x01, //     Logical Maximum (1)
    0x95, 0x03, //     Report Count (3)
    0x75, 0x01, //     Report Size (1)
    0x81, 0x02, //     Input (Data, Variable, Absolute)
    0x95, 0x01, //     Report Count (1)
    0x75, 0x05, //     Report Size (5)
    0x81, 0x01, //     Input (Constant) - padding
    0x05, 0x01, //     Usage Page (Generic Desktop)
    0x09, 0x30, //     Usage (X)
    0x09, 0x31, //     Usage (Y)
    0x09, 0x38, //     Usage (Wheel)
    0x15, 0x81, //     Logical Minimum (-127)
    0x25, 0x7F, //     Logical Maximum (127)
    0x75, 0x08, //     Report Size (8)
    0x95, 0x03, //     Report Count (3)
    0x81, 0x06, //     Input (Data, Variable, Relative)
    0xC0, //   End Collection
    0xC0, // End Collection
]

/// Keyboard: Report ID 2 — modifier(8), reserved(8), keys[6] = 8 bytes
private let keyboardReportDescriptor: [UInt8] = [
    0x05, 0x01, // Usage Page (Generic Desktop)
    0x09, 0x06, // Usage (Keyboard)
    0xA1, 0x01, // Collection (Application)
    0x85, 0x02, //   Report ID (2)
    0x05, 0x07, //   Usage Page (Keyboard/Keypad)
    0x19, 0xE0, //   Usage Minimum (Left Control)
    0x29, 0xE7, //   Usage Maximum (Right GUI)
    0x15, 0x00, //   Logical Minimum (0)
    0x25, 0x01, //   Logical Maximum (1)
    0x75, 0x01, //   Report Size (1)
    0x95, 0x08, //   Report Count (8)
    0x81, 0x02, //   Input (Data, Variable, Absolute) - Modifiers
    0x95, 0x01, //   Report Count (1)
    0x75, 0x08, //   Report Size (8)
    0x81, 0x01, //   Input (Constant) - Reserved
    0x95, 0x06, //   Report Count (6)
    0x75, 0x08, //   Report Size (8)
    0x15, 0x00, //   Logical Minimum (0)
    0x25, 0x65, //   Logical Maximum (101)
    0x05, 0x07, //   Usage Page (Keyboard/Keypad)
    0x19, 0x00, //   Usage Minimum (0)
    0x29, 0x65, //   Usage Maximum (101)
    0x81, 0x00, //   Input (Data, Array)
    0xC0, // End Collection
]

/// Gamepad: Report ID 3 — buttons(16), hat(4)+pad(4), sticks(4x8) = 7 bytes
private let gamepadReportDescriptor: [UInt8] = [
    0x05, 0x01, // Usage Page (Generic Desktop)
    0x09, 0x05, // Usage (Gamepad)
    0xA1, 0x01, // Collection (Application)
    0x85, 0x03, //   Report ID (3)
    // 16 buttons
    0x05, 0x09, //   Usage Page (Button)
    0x19, 0x01, //   Usage Minimum (1)
    0x29, 0x10, //   Usage Maximum (16)
    0x15, 0x00, //   Logical Minimum (0)
    0x25, 0x01, //   Logical Maximum (1)
    0x75, 0x01, //   Report Size (1)
    0x95, 0x10, //   Report Count (16)
    0x81, 0x02, //   Input (Data, Variable, Absolute)
    // Hat switch (D-pad)
    0x05, 0x01, //   Usage Page (Generic Desktop)
    0x09, 0x39, //   Usage (Hat Switch)
    0x15, 0x00, //   Logical Minimum (0)
    0x25, 0x07, //   Logical Maximum (7)
    0x35, 0x00, //   Physical Minimum (0)
    0x46, 0x3B, 0x01, // Physical Maximum (315)
    0x65, 0x14, //   Unit (Degrees)
    0x75, 0x04, //   Report Size (4)
    0x95, 0x01, //   Report Count (1)
    0x81, 0x42, //   Input (Data, Variable, Absolute, Null State)
    0x75, 0x04, //   Report Size (4) - padding
    0x95, 0x01, //   Report Count (1)
    0x81, 0x01, //   Input (Constant)
    // Left stick X, Y + Right stick Z, Rz
    0x09, 0x30, //   Usage (X)
    0x09, 0x31, //   Usage (Y)
    0x09, 0x32, //   Usage (Z)
    0x09, 0x35, //   Usage (Rz)
    0x15, 0x00, //   Logical Minimum (0)
    0x26, 0xFF, 0x00, // Logical Maximum (255)
    0x75, 0x08, //   Report Size (8)
    0x95, 0x04, //   Report Count (4)
    0x81, 0x02, //   Input (Data, Variable, Absolute)
    0xC0, // End Collection
]

// MARK: - Appearance values (Bluetooth SIG assigned numbers)

private let kAppearanceMouse: UInt16 = 0x03C2
private let kAppearanceKeyboard: UInt16 = 0x03C1
private let kAppearanceGamepad: UInt16 = 0x03C4
private let kAppearanceHIDGeneric: UInt16 = 0x03C0

// MARK: - BLEHIDCommander

/// BLE HID peripheral that makes the iOS device act as a mouse/keyboard/gamepad.
/// NOT @MainActor — CBPeripheralManager delegate runs on bleQueue, UI updates dispatched to main.
class BLEHIDCommander: NSObject, ObservableObject {
    // Published properties — only mutated on main thread
    @Published var isAdvertising = false
    @Published var isConnected = false
    @Published var connectedDeviceName: String?
    @Published var currentProfile: HIDProfile = .combo

    // BLE internals
    private var peripheralManager: CBPeripheralManager?
    private var reportCharacteristic: CBMutableCharacteristic?
    private let bleQueue = DispatchQueue(label: "com.chromacatch.ble-hid", qos: .userInitiated)

    // Thread-safe central tracking
    private var subscribedCentrals: [CBCentral] = []
    private let lock = NSLock()

    // Service setup tracking
    private var servicesAdded = 0
    private var totalServices = 0

    // Current gamepad state
    private var gamepadButtons: UInt16 = 0
    private var gamepadHat: UInt8 = 0x0F // centered (null state)
    private var gamepadLeftX: UInt8 = 128
    private var gamepadLeftY: UInt8 = 128
    private var gamepadRightX: UInt8 = 128
    private var gamepadRightY: UInt8 = 128

    // Last sent report (for read requests)
    private var lastReport = Data(repeating: 0, count: 9)

    override init() {
        super.init()
    }

    // MARK: - Lifecycle

    func start(profile: HIDProfile = .combo) {
        guard peripheralManager == nil else {
            hidLog.warning("BLE HID already started")
            return
        }
        hidLog.info("Starting BLE HID with profile: \(profile.rawValue)")
        DispatchQueue.main.async { self.currentProfile = profile }
        peripheralManager = CBPeripheralManager(
            delegate: self,
            queue: bleQueue,
            options: [CBPeripheralManagerOptionShowPowerAlertKey: true]
        )
    }

    func stop() {
        guard let pm = peripheralManager else { return }
        hidLog.info("Stopping BLE HID")
        peripheralManager = nil
        reportCharacteristic = nil

        bleQueue.async {
            pm.stopAdvertising()
            pm.removeAllServices()
        }

        lock.lock()
        subscribedCentrals.removeAll()
        lock.unlock()
        servicesAdded = 0

        DispatchQueue.main.async {
            self.isAdvertising = false
            self.isConnected = false
            self.connectedDeviceName = nil
        }
    }

    /// Switch to a different HID profile at runtime.
    /// Stops the current session, changes profile, and restarts.
    /// Connected centrals will need to reconnect after the switch.
    func switchProfile(_ profile: HIDProfile) {
        let wasRunning = peripheralManager != nil
        hidLog.info("Switching HID profile: \(currentProfile.rawValue) → \(profile.rawValue)")
        if wasRunning { stop() }
        // Brief delay to let BLE stack clean up before re-advertising
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            guard let self = self else { return }
            if wasRunning {
                self.start(profile: profile)
            } else {
                self.currentProfile = profile
            }
        }
    }

    // MARK: - Mouse Commands (Report ID 1)

    /// Send mouse move (relative). Report: [0x01, buttons, dx, dy, wheel]
    func mouseMove(dx: Int8, dy: Int8, wheel: Int8 = 0) {
        sendReport(Data([0x01, 0x00, UInt8(bitPattern: dx), UInt8(bitPattern: dy), UInt8(bitPattern: wheel)]))
    }

    /// Send mouse button state. Report: [0x01, buttons, dx, dy, wheel]
    func mouseButton(buttons: UInt8, dx: Int8 = 0, dy: Int8 = 0) {
        sendReport(Data([0x01, buttons, UInt8(bitPattern: dx), UInt8(bitPattern: dy), 0x00]))
    }

    /// Click: press then release after 50ms
    func mouseClick(button: UInt8 = 0x01) {
        mouseButton(buttons: button)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { [weak self] in
            self?.mouseButton(buttons: 0x00)
        }
    }

    // MARK: - Keyboard Commands (Report ID 2)

    /// Send keyboard key press. Report: [0x02, modifier, reserved, key0..key5]
    func keyPress(modifier: UInt8 = 0, keys: [UInt8] = []) {
        var report = Data([0x02, modifier, 0x00])
        let padded = keys + Array(repeating: UInt8(0), count: max(0, 6 - keys.count))
        report.append(contentsOf: padded.prefix(6))
        sendReport(report)
    }

    /// Release all keys
    func keyRelease() {
        keyPress()
    }

    // MARK: - Gamepad Commands (Report ID 3)

    func gamepadButtonPress(buttonIndex: Int) {
        guard (0 ..< 16).contains(buttonIndex) else { return }
        gamepadButtons |= UInt16(1 << buttonIndex)
        sendGamepadReport()
    }

    func gamepadButtonRelease(buttonIndex: Int) {
        guard (0 ..< 16).contains(buttonIndex) else { return }
        gamepadButtons &= ~UInt16(1 << buttonIndex)
        sendGamepadReport()
    }

    /// Set hat switch: 0=N, 1=NE, 2=E, ..., 7=NW, 0x0F=centered
    func gamepadSetHat(_ direction: UInt8) {
        gamepadHat = direction
        sendGamepadReport()
    }

    /// Set stick values (0-255, 128 = center)
    func gamepadSetStick(left: Bool, x: UInt8, y: UInt8) {
        if left {
            gamepadLeftX = x; gamepadLeftY = y
        } else {
            gamepadRightX = x; gamepadRightY = y
        }
        sendGamepadReport()
    }

    /// Report: [0x03, buttonsLo, buttonsHi, hat, lx, ly, rx, ry] = 8 bytes
    private func sendGamepadReport() {
        let lo = UInt8(gamepadButtons & 0xFF)
        let hi = UInt8((gamepadButtons >> 8) & 0xFF)
        sendReport(Data([0x03, lo, hi, gamepadHat, gamepadLeftX, gamepadLeftY, gamepadRightX, gamepadRightY]))
    }

    // MARK: - Report Transmission

    private func sendReport(_ report: Data) {
        guard let char = reportCharacteristic, let pm = peripheralManager else { return }
        lastReport = report

        lock.lock()
        let hasSubs = !subscribedCentrals.isEmpty
        lock.unlock()
        guard hasSubs else { return }

        if !pm.updateValue(report, for: char, onSubscribedCentrals: nil) {
            hidLog.warning("HID report queued (transmit buffer full)")
        }
    }

    // MARK: - GATT Service Setup

    private func setupServices() {
        guard let pm = peripheralManager else { return }
        hidLog.info("Setting up GATT services for profile: \(currentProfile.rawValue)")

        pm.removeAllServices()
        servicesAdded = 0
        reportCharacteristic = nil

        // --- 1. Generic Access Service (Appearance tells host what we are) ---
        let appearance: UInt16
        switch currentProfile {
        case .mouse: appearance = kAppearanceMouse
        case .keyboard: appearance = kAppearanceKeyboard
        case .gamepad: appearance = kAppearanceGamepad
        case .combo: appearance = kAppearanceKeyboard // most compatible for combo
        }
        var appearanceLE = appearance.littleEndian
        let appearanceData = Data(bytes: &appearanceLE, count: 2)
        let appearanceChar = CBMutableCharacteristic(
            type: kAppearanceUUID, properties: [.read], value: appearanceData, permissions: [.readable]
        )
        let genericAccessService = CBMutableService(type: kGenericAccessUUID, primary: false)
        genericAccessService.characteristics = [appearanceChar]

        // --- 2. Device Information Service ---
        let mfgChar = CBMutableCharacteristic(
            type: kManufacturerNameUUID, properties: .read,
            value: "ChromaCatch".data(using: .utf8), permissions: .readable
        )
        // PnP ID: vendorIdSource=0x02(USB), vendorId=0x046D, productId=0x0001, version=0x0001
        let pnpChar = CBMutableCharacteristic(
            type: kPnPIDUUID, properties: .read,
            value: Data([0x02, 0x6D, 0x04, 0x01, 0x00, 0x01, 0x00]),
            permissions: .readable
        )
        let deviceInfoService = CBMutableService(type: kDeviceInfoServiceUUID, primary: false)
        deviceInfoService.characteristics = [mfgChar, pnpChar]

        // --- 3. Battery Service ---
        let batteryChar = CBMutableCharacteristic(
            type: kBatteryLevelUUID, properties: [.read, .notify],
            value: Data([100]), permissions: .readable
        )
        let batteryService = CBMutableService(type: kBatteryServiceUUID, primary: false)
        batteryService.characteristics = [batteryChar]

        // --- 4. HID Service (primary) ---
        let reportDescriptor: [UInt8]
        switch currentProfile {
        case .mouse: reportDescriptor = mouseReportDescriptor
        case .keyboard: reportDescriptor = keyboardReportDescriptor
        case .gamepad: reportDescriptor = gamepadReportDescriptor
        case .combo: reportDescriptor = mouseReportDescriptor + keyboardReportDescriptor
        }

        // HID Information: bcdHID=1.11, bCountryCode=0, Flags=0x02 (normally connectable)
        let hidInfoChar = CBMutableCharacteristic(
            type: kHIDInfoUUID, properties: .read,
            value: Data([0x11, 0x01, 0x00, 0x02]), permissions: .readable
        )

        // Report Map — the full HID report descriptor
        let reportMapChar = CBMutableCharacteristic(
            type: kHIDReportMapUUID, properties: .read,
            value: Data(reportDescriptor), permissions: .readable
        )

        // Control Point — host writes here to control HID
        let controlPointChar = CBMutableCharacteristic(
            type: kHIDControlPointUUID, properties: .writeWithoutResponse,
            value: nil, permissions: .writeable
        )

        // Protocol Mode — 0x01 = Report Protocol
        let protocolModeChar = CBMutableCharacteristic(
            type: kProtocolModeUUID, properties: [.read, .writeWithoutResponse],
            value: Data([0x01]), permissions: [.readable, .writeable]
        )

        // Single HID Report characteristic — Report ID is first byte of each notification
        let reportChar = CBMutableCharacteristic(
            type: kHIDReportUUID,
            properties: [.read, .notify],
            value: nil,
            permissions: [.readable]
        )

        // Report Reference descriptor (0x2908): tells host the Report ID and type
        // Value: [ReportID=0x00 (multiplexed via first byte), ReportType=0x01 (Input)]
        let reportRef = CBMutableDescriptor(
            type: kReportReferenceUUID,
            value: Data([0x00, 0x01])
        )
        reportChar.descriptors = [reportRef]

        reportCharacteristic = reportChar

        let hidService = CBMutableService(type: kHIDServiceUUID, primary: true)
        hidService.characteristics = [hidInfoChar, reportMapChar, controlPointChar, protocolModeChar, reportChar]

        // Add services — advertising starts after all are added
        totalServices = 4
        pm.add(genericAccessService)
        pm.add(deviceInfoService)
        pm.add(batteryService)
        pm.add(hidService)
    }

    private func startAdvertising() {
        guard let pm = peripheralManager else { return }
        let adData: [String: Any] = [
            CBAdvertisementDataLocalNameKey: "ChromaCatch HID",
            CBAdvertisementDataServiceUUIDsKey: [kHIDServiceUUID],
        ]
        pm.startAdvertising(adData)
    }
}

// MARK: - CBPeripheralManagerDelegate

extension BLEHIDCommander: CBPeripheralManagerDelegate {
    func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        hidLog.info("BLE peripheral state: \(peripheral.state.rawValue)")
        switch peripheral.state {
        case .poweredOn:
            hidLog.info("Bluetooth powered on — setting up HID services")
            setupServices()
        case .poweredOff:
            hidLog.warning("Bluetooth powered off")
            DispatchQueue.main.async {
                self.isAdvertising = false
                self.isConnected = false
                self.connectedDeviceName = nil
            }
        case .unauthorized:
            hidLog.error("Bluetooth unauthorized")
        case .unsupported:
            hidLog.error("Bluetooth unsupported on this device")
        default:
            break
        }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        if let error = error {
            hidLog.error("Failed to add service \(service.uuid): \(error.localizedDescription)")
            return
        }
        hidLog.info("Service added: \(service.uuid)")
        servicesAdded += 1
        if servicesAdded >= totalServices {
            hidLog.info("All \(totalServices) services added — starting advertising")
            startAdvertising()
        }
    }

    func peripheralManagerDidStartAdvertising(_ peripheral: CBPeripheralManager, error: Error?) {
        if let error = error {
            hidLog.error("Advertising failed: \(error.localizedDescription)")
            DispatchQueue.main.async { self.isAdvertising = false }
            return
        }
        hidLog.info("BLE HID advertising started")
        DispatchQueue.main.async { self.isAdvertising = true }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didSubscribeTo characteristic: CBCharacteristic) {
        hidLog.info("Central subscribed: \(central.identifier.uuidString.prefix(8))")
        lock.lock()
        if !subscribedCentrals.contains(where: { $0.identifier == central.identifier }) {
            subscribedCentrals.append(central)
        }
        lock.unlock()
        DispatchQueue.main.async {
            self.isConnected = true
            self.connectedDeviceName = String(central.identifier.uuidString.prefix(8)) + "..."
        }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didUnsubscribeFrom characteristic: CBCharacteristic) {
        hidLog.info("Central unsubscribed: \(central.identifier.uuidString.prefix(8))")
        lock.lock()
        subscribedCentrals.removeAll { $0.identifier == central.identifier }
        let empty = subscribedCentrals.isEmpty
        lock.unlock()
        if empty {
            DispatchQueue.main.async {
                self.isConnected = false
                self.connectedDeviceName = nil
            }
        }
    }

    /// Windows/Mac send read requests — must respond or connection fails
    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveRead request: CBATTRequest) {
        hidLog.debug("Read request for \(request.characteristic.uuid)")
        if request.characteristic.uuid == kHIDReportUUID {
            request.value = lastReport
            peripheral.respond(to: request, withResult: .success)
        } else {
            // Let CoreBluetooth handle reads for characteristics with static values
            peripheral.respond(to: request, withResult: .success)
        }
    }

    /// Accept write requests (Control Point, Protocol Mode)
    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]) {
        hidLog.debug("Write request(s): \(requests.count)")
        for request in requests {
            peripheral.respond(to: request, withResult: .success)
        }
    }

    func peripheralManagerIsReady(toUpdateSubscribers peripheral: CBPeripheralManager) {
        hidLog.debug("Transmit buffer ready")
    }
}

//
//  BLEHIDCommander.swift
//  ChromaCatchController
//
//  BLE HID peripheral — allows iOS to act as a Bluetooth mouse/keyboard/gamepad
//  to target devices (Switch, 3DS, PC, Android) without requiring ESP32 hardware.
//
//  Reference: https://gist.github.com/conath/c606d95d58bbcb50e9715864eeeecf07
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
private let kBootKeyboardInputReportUUID = CBUUID(string: "00002A22-0000-1000-8000-00805F9B34FB")
private let kBootMouseInputReportUUID = CBUUID(string: "00002A33-0000-1000-8000-00805F9B34FB")

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

/// Combo descriptor captured from Bluetouch-style HID profile:
/// Mouse (ID 1), Keyboard input (ID 2), Keyboard output LEDs (ID 3),
/// Consumer control (ID 6), System control (ID 5).
private let comboReportDescriptor: [UInt8] = [
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, 0x01, 0x09, 0x01, 0xA1, 0x00,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x03, 0x75, 0x01, 0x95, 0x03, 0x15, 0x00,
    0x25, 0x01, 0x81, 0x02, 0x95, 0x05, 0x81, 0x03, 0x05, 0x01, 0x09, 0x30,
    0x09, 0x31, 0x09, 0x38, 0x75, 0x08, 0x95, 0x03, 0x15, 0x81, 0x25, 0x7F,
    0x81, 0x06, 0xC0, 0xC0, 0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x85, 0x02,
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x75, 0x01, 0x95, 0x08, 0x15, 0x00,
    0x25, 0x01, 0x81, 0x02, 0x95, 0x01, 0x75, 0x08, 0x81, 0x01, 0x19, 0x00,
    0x29, 0xDD, 0x95, 0x06, 0x25, 0xDD, 0x81, 0x00, 0x85, 0x03, 0x05, 0x08,
    0x19, 0x01, 0x29, 0x05, 0x95, 0x05, 0x75, 0x01, 0x25, 0x01, 0x91, 0x02,
    0x95, 0x03, 0x91, 0x03, 0xC0, 0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01, 0x85,
    0x04, 0x05, 0x06, 0x09, 0x20, 0x75, 0x08, 0x95, 0x01, 0x15, 0x00, 0x25,
    0x64, 0x81, 0x02, 0xC0, 0x05, 0x01, 0x09, 0x80, 0xA1, 0x01, 0x85, 0x05,
    0x09, 0x81, 0x09, 0x82, 0x09, 0x8E, 0x09, 0xA8, 0x09, 0x8F, 0x09, 0x85,
    0x09, 0x86, 0x09, 0xA7, 0x75, 0x01, 0x95, 0x08, 0x15, 0x00, 0x25, 0x01,
    0x81, 0x06, 0xC0, 0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x06, 0x19,
    0x00, 0x2A, 0x74, 0x01, 0x75, 0x10, 0x95, 0x01, 0x15, 0x00, 0x26, 0x74,
    0x01, 0x81, 0x00, 0x1A, 0x81, 0x01, 0x2A, 0xCB, 0x01, 0x95, 0x01, 0x75,
    0x08, 0x15, 0x01, 0x25, 0x4B, 0x81, 0x00, 0x1A, 0x01, 0x02, 0x2A, 0xB0,
    0x02, 0x25, 0xB0, 0x81, 0x00, 0xA1, 0x03, 0x19, 0x00, 0x29, 0xFF, 0x95,
    0x01, 0x75, 0x08, 0x15, 0x00, 0x25, 0xFF, 0x81, 0x00, 0xC0, 0xC0,
]

// MARK: - Appearance values (Bluetooth SIG assigned numbers)

private let kAppearanceMouse: UInt16 = 0x03C2
private let kAppearanceKeyboard: UInt16 = 0x03C1
private let kAppearanceGamepad: UInt16 = 0x03C4

// MARK: - BLEHIDCommander

/// BLE HID peripheral that makes the iOS device act as a mouse/keyboard/gamepad.
/// CoreBluetooth callbacks are handled on main queue for maximum compatibility.
final class BLEHIDCommander: NSObject, ObservableObject {
    // Published properties — only mutated on main thread.
    @Published var isAdvertising = false
    @Published var isConnected = false
    @Published var connectedDeviceName: String?
    @Published var currentProfile: HIDProfile = .combo

    // BLE internals.
    private var peripheralManager: CBPeripheralManager?
    private var systemControlInputReportCharacteristic: CBMutableCharacteristic?
    private var consumerInputReportCharacteristic: CBMutableCharacteristic?
    private var mouseInputReportCharacteristic: CBMutableCharacteristic?
    private var keyboardInputReportCharacteristic: CBMutableCharacteristic?
    private var keyboardOutputReportCharacteristic: CBMutableCharacteristic?
    private var gamepadInputReportCharacteristic: CBMutableCharacteristic?
    private var bootKeyboardInputCharacteristic: CBMutableCharacteristic?
    private var bootMouseInputCharacteristic: CBMutableCharacteristic?

    // Thread-safe central tracking.
    private var subscribedCentrals: [CBCentral] = []
    private let lock = NSLock()

    // Service setup tracking.
    private var servicesAdded = 0
    private var totalServices = 0

    // Active profile used on BLE queue.
    private var activeProfile: HIDProfile = .combo

    // Protocol/control point values.
    private var protocolMode: UInt8 = 0x01 // 0 = boot, 1 = report
    private var controlPoint: UInt8 = 0x00

    // Current gamepad state.
    private var gamepadButtons: UInt16 = 0
    private var gamepadHat: UInt8 = 0x0F // centered (null state)
    private var gamepadLeftX: UInt8 = 128
    private var gamepadLeftY: UInt8 = 128
    private var gamepadRightX: UInt8 = 128
    private var gamepadRightY: UInt8 = 128

    // Last sent reports (for read requests).
    private var lastSystemControlInputReport = Data([0x00])
    private var lastConsumerInputReport = Data([0x00, 0x00, 0x00, 0x00, 0x00])
    private var lastMouseInputReport = Data([0x00, 0x00, 0x00, 0x00])
    private var lastKeyboardInputReport = Data([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    private var lastKeyboardOutputReport = Data([0x00])
    private var lastGamepadInputReport = Data([0x00, 0x00, 0x0F, 0x80, 0x80, 0x80, 0x80])
    private var batteryLevel: UInt8 = 100
    private var lastKeyboardBootReport = Data(repeating: 0, count: 8)
    private var lastMouseBootReport = Data(repeating: 0, count: 3)

    override init() {
        super.init()
    }

    // MARK: - Lifecycle

    func start(profile: HIDProfile = .combo) {
        guard peripheralManager == nil else {
            hidLog.warning("BLE HID already started")
            return
        }

        activeProfile = profile
        publishCurrentProfile(profile)
        protocolMode = 0x01
        controlPoint = 0x00

        hidLog.info("Starting BLE HID with profile: \(profile.rawValue)")
        peripheralManager = CBPeripheralManager(
            delegate: self,
            queue: nil,
            options: [CBPeripheralManagerOptionShowPowerAlertKey: true]
        )
    }

    func stop() {
        guard let pm = peripheralManager else { return }

        hidLog.info("Stopping BLE HID")
        peripheralManager = nil
        systemControlInputReportCharacteristic = nil
        consumerInputReportCharacteristic = nil
        mouseInputReportCharacteristic = nil
        keyboardInputReportCharacteristic = nil
        keyboardOutputReportCharacteristic = nil
        gamepadInputReportCharacteristic = nil
        bootKeyboardInputCharacteristic = nil
        bootMouseInputCharacteristic = nil

        pm.stopAdvertising()
        pm.removeAllServices()
        lock.lock()
        subscribedCentrals.removeAll()
        lock.unlock()
        servicesAdded = 0
        totalServices = 0

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
        hidLog.info("Switching HID profile: \(self.activeProfile.rawValue) → \(profile.rawValue)")
        activeProfile = profile
        publishCurrentProfile(profile)

        if wasRunning { stop() }

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            guard let self else { return }
            if wasRunning {
                self.start(profile: profile)
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

    /// Release all keys.
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

    /// Set hat switch: 0=N, 1=NE, 2=E, ..., 7=NW, 0x0F=centered.
    func gamepadSetHat(_ direction: UInt8) {
        gamepadHat = direction
        sendGamepadReport()
    }

    /// Set stick values (0-255, 128 = center).
    func gamepadSetStick(left: Bool, x: UInt8, y: UInt8) {
        if left {
            gamepadLeftX = x
            gamepadLeftY = y
        } else {
            gamepadRightX = x
            gamepadRightY = y
        }
        sendGamepadReport()
    }

    /// Report: [0x03, buttonsLo, buttonsHi, hat, lx, ly, rx, ry] = 8 bytes.
    private func sendGamepadReport() {
        let lo = UInt8(gamepadButtons & 0xFF)
        let hi = UInt8((gamepadButtons >> 8) & 0xFF)
        sendReport(Data([0x03, lo, hi, gamepadHat, gamepadLeftX, gamepadLeftY, gamepadRightX, gamepadRightY]))
    }

    // MARK: - Report Transmission

    private func sendReport(_ report: Data) {
        DispatchQueue.main.async { [weak self] in
            self?.sendReportOnMainQueue(report)
        }
    }

    private func sendReportOnMainQueue(_ report: Data) {
        guard let pm = peripheralManager else { return }

        guard let reportID = report.first else { return }
        let payload = Data(report.dropFirst())
        updateCachedReports(reportID: reportID, payload: payload)

        lock.lock()
        let hasSubscribers = !subscribedCentrals.isEmpty
        lock.unlock()
        guard hasSubscribers else { return }

        sendIfAvailable(payload, to: characteristic(forInputReportID: reportID), manager: pm, label: "report \(reportID)")

        // Boot protocol compatibility for hosts that subscribe/read boot reports (2A22 / 2A33).
        if reportID == 0x02 {
            sendIfAvailable(lastKeyboardBootReport, to: bootKeyboardInputCharacteristic, manager: pm, label: "boot keyboard")
        } else if reportID == 0x01 {
            sendIfAvailable(lastMouseBootReport, to: bootMouseInputCharacteristic, manager: pm, label: "boot mouse")
        }
    }

    private func updateCachedReports(reportID: UInt8, payload: Data) {
        switch reportID {
        case 0x01: // mouse input
            lastMouseInputReport = normalized(payload, length: 4)
            lastMouseBootReport = Data(lastMouseInputReport.prefix(3))
        case 0x02: // keyboard input
            lastKeyboardInputReport = normalized(payload, length: 8)
            lastKeyboardBootReport = lastKeyboardInputReport
        case 0x03: // gamepad input (gamepad profile) OR keyboard LED output
            if activeProfile == .gamepad {
                lastGamepadInputReport = normalized(payload, length: 7)
            } else {
                lastKeyboardOutputReport = normalized(payload, length: 1)
            }
        case 0x05: // system control input
            lastSystemControlInputReport = normalized(payload, length: 1)
        case 0x06: // consumer input
            lastConsumerInputReport = normalized(payload, length: 5)
        default:
            break
        }
    }

    private func normalized(_ payload: Data, length: Int) -> Data {
        var bytes = Array(payload.prefix(length))
        if bytes.count < length {
            bytes.append(contentsOf: Array(repeating: UInt8(0), count: length - bytes.count))
        }
        return Data(bytes)
    }

    private func sendIfAvailable(_ payload: Data,
                                 to characteristic: CBMutableCharacteristic?,
                                 manager: CBPeripheralManager,
                                 label: String)
    {
        guard let characteristic else { return }
        if !manager.updateValue(payload, for: characteristic, onSubscribedCentrals: nil) {
            hidLog.warning("HID report queued (\(label, privacy: .public) transmit buffer full)")
        }
    }

    private func characteristic(forInputReportID reportID: UInt8) -> CBMutableCharacteristic? {
        switch reportID {
        case 0x01: return mouseInputReportCharacteristic
        case 0x02: return keyboardInputReportCharacteristic
        case 0x03: return gamepadInputReportCharacteristic
        case 0x05: return systemControlInputReportCharacteristic
        case 0x06: return consumerInputReportCharacteristic
        default: return nil
        }
    }

    // MARK: - GATT Service Setup

    private func setupServices() {
        guard let pm = peripheralManager else { return }

        hidLog.info("Setting up GATT services for profile: \(self.activeProfile.rawValue)")

        pm.removeAllServices()
        servicesAdded = 0
        systemControlInputReportCharacteristic = nil
        consumerInputReportCharacteristic = nil
        mouseInputReportCharacteristic = nil
        keyboardInputReportCharacteristic = nil
        keyboardOutputReportCharacteristic = nil
        gamepadInputReportCharacteristic = nil
        bootKeyboardInputCharacteristic = nil
        bootMouseInputCharacteristic = nil

        // --- 1) Battery Service ---
        let batteryChar = CBMutableCharacteristic(
            type: kBatteryLevelUUID,
            properties: [.read, .notify],
            value: nil,
            permissions: [.readable]
        )
        let batteryService = CBMutableService(type: kBatteryServiceUUID, primary: false)
        batteryService.characteristics = [batteryChar]

        // --- 2) HID Service (primary) ---
        let reportDescriptor: [UInt8]
        switch self.activeProfile {
        case .mouse: reportDescriptor = mouseReportDescriptor
        case .keyboard: reportDescriptor = keyboardReportDescriptor
        case .gamepad: reportDescriptor = gamepadReportDescriptor
        case .combo: reportDescriptor = comboReportDescriptor
        }

        // HID Information: bcdHID=1.11, bCountryCode=0, Flags=0x03
        let hidInfoChar = CBMutableCharacteristic(
            type: kHIDInfoUUID,
            properties: [.read],
            value: Data([0x11, 0x01, 0x00, 0x03]),
            permissions: [.readable]
        )

        // Report Map — full HID report descriptor.
        let reportMapChar = CBMutableCharacteristic(
            type: kHIDReportMapUUID,
            properties: [.read],
            value: Data(reportDescriptor),
            permissions: [.readable]
        )
        // External Report Reference descriptor links HID report map to Battery Level (2A19).
        reportMapChar.descriptors = [CBMutableDescriptor(type: CBUUID(string: "00002907-0000-1000-8000-00805F9B34FB"), value: Data([0x19, 0x2A]))]

        // Control Point — host writes suspend/exit suspend.
        let controlPointChar = CBMutableCharacteristic(
            type: kHIDControlPointUUID,
            properties: [.writeWithoutResponse],
            value: nil,
            permissions: [.writeable]
        )

        // Protocol Mode — host writes 0x00 (boot) / 0x01 (report).
        let protocolModeChar = CBMutableCharacteristic(
            type: kProtocolModeUUID,
            properties: [.read, .writeWithoutResponse],
            value: nil,
            permissions: [.readable, .writeable]
        )

        var hidCharacteristics: [CBMutableCharacteristic] = [protocolModeChar, hidInfoChar, controlPointChar, reportMapChar]

        if self.activeProfile == .keyboard || self.activeProfile == .combo {
            let bootKeyboardChar = CBMutableCharacteristic(
                type: kBootKeyboardInputReportUUID,
                properties: [.read, .notify],
                value: nil,
                permissions: [.readable]
            )
            bootKeyboardInputCharacteristic = bootKeyboardChar
            hidCharacteristics.append(bootKeyboardChar)
        }

        if self.activeProfile == .mouse || self.activeProfile == .combo {
            let bootMouseChar = CBMutableCharacteristic(
                type: kBootMouseInputReportUUID,
                properties: [.read, .notify],
                value: nil,
                permissions: [.readable]
            )
            bootMouseInputCharacteristic = bootMouseChar
            hidCharacteristics.append(bootMouseChar)
        }

        switch self.activeProfile {
        case .combo:
            systemControlInputReportCharacteristic = createReportCharacteristic(reportID: 0x05, reportType: 0x01, properties: [.read, .notify], permissions: [.readable])
            consumerInputReportCharacteristic = createReportCharacteristic(reportID: 0x06, reportType: 0x01, properties: [.read, .notify], permissions: [.readable])
            mouseInputReportCharacteristic = createReportCharacteristic(reportID: 0x01, reportType: 0x01, properties: [.read, .notify], permissions: [.readable])
            keyboardInputReportCharacteristic = createReportCharacteristic(reportID: 0x02, reportType: 0x01, properties: [.read, .notify], permissions: [.readable])
            keyboardOutputReportCharacteristic = createReportCharacteristic(reportID: 0x03, reportType: 0x02, properties: [.read, .write, .writeWithoutResponse], permissions: [.readable, .writeable])
            hidCharacteristics.append(contentsOf: [
                systemControlInputReportCharacteristic!,
                consumerInputReportCharacteristic!,
                mouseInputReportCharacteristic!,
                keyboardInputReportCharacteristic!,
                keyboardOutputReportCharacteristic!,
            ])
        case .mouse:
            mouseInputReportCharacteristic = createReportCharacteristic(reportID: 0x01, reportType: 0x01, properties: [.read, .notify], permissions: [.readable])
            hidCharacteristics.append(mouseInputReportCharacteristic!)
        case .keyboard:
            keyboardInputReportCharacteristic = createReportCharacteristic(reportID: 0x02, reportType: 0x01, properties: [.read, .notify], permissions: [.readable])
            keyboardOutputReportCharacteristic = createReportCharacteristic(reportID: 0x03, reportType: 0x02, properties: [.read, .write, .writeWithoutResponse], permissions: [.readable, .writeable])
            hidCharacteristics.append(contentsOf: [keyboardInputReportCharacteristic!, keyboardOutputReportCharacteristic!])
        case .gamepad:
            gamepadInputReportCharacteristic = createReportCharacteristic(reportID: 0x03, reportType: 0x01, properties: [.read, .notify], permissions: [.readable])
            hidCharacteristics.append(gamepadInputReportCharacteristic!)
        }

        let hidService = CBMutableService(type: kHIDServiceUUID, primary: true)
        hidService.characteristics = hidCharacteristics

        totalServices = 2
        pm.add(batteryService)
        pm.add(hidService)
    }

    private func createReportCharacteristic(reportID: UInt8,
                                            reportType: UInt8,
                                            properties: CBCharacteristicProperties,
                                            permissions: CBAttributePermissions) -> CBMutableCharacteristic
    {
        let characteristic = CBMutableCharacteristic(
            type: kHIDReportUUID,
            properties: properties,
            value: nil,
            permissions: permissions
        )
        characteristic.descriptors = [CBMutableDescriptor(type: kReportReferenceUUID, value: Data([reportID, reportType]))]
        return characteristic
    }

    private func startAdvertising() {
        guard let pm = peripheralManager else { return }
        pm.startAdvertising([
            CBAdvertisementDataLocalNameKey: "ChromaCatch HID",
            CBAdvertisementDataServiceUUIDsKey: [kHIDServiceUUID],
        ])
    }

    private func publishCurrentProfile(_ profile: HIDProfile) {
        DispatchQueue.main.async {
            self.currentProfile = profile
        }
    }

    private func respondRead(_ request: CBATTRequest, with value: Data, on peripheral: CBPeripheralManager) {
        guard request.offset <= value.count else {
            peripheral.respond(to: request, withResult: .invalidOffset)
            return
        }

        request.value = value.subdata(in: request.offset ..< value.count)
        peripheral.respond(to: request, withResult: .success)
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
        if let error {
            hidLog.error("Failed to add service \(service.uuid): \(error.localizedDescription)")
            return
        }

        hidLog.info("Service added: \(service.uuid)")
        servicesAdded += 1

        if servicesAdded >= totalServices {
            hidLog.info("All \(self.totalServices) services added — starting advertising")
            startAdvertising()
        }
    }

    func peripheralManagerDidStartAdvertising(_ peripheral: CBPeripheralManager, error: Error?) {
        if let error {
            hidLog.error("Advertising failed: \(error.localizedDescription)")
            DispatchQueue.main.async { self.isAdvertising = false }
            return
        }

        hidLog.info("BLE HID advertising started")
        DispatchQueue.main.async { self.isAdvertising = true }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didSubscribeTo characteristic: CBCharacteristic) {
        hidLog.info("Central subscribed: \(central.identifier.uuidString.prefix(8)) to \(characteristic.uuid)")

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
        hidLog.info("Central unsubscribed: \(central.identifier.uuidString.prefix(8)) from \(characteristic.uuid)")

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

    /// Windows/macOS frequently issue read requests during HID handshake.
    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveRead request: CBATTRequest) {
        let uuid = request.characteristic.uuid
        hidLog.debug("Read request for \(uuid)")

        if uuid == kBootKeyboardInputReportUUID {
            respondRead(request, with: lastKeyboardBootReport, on: peripheral)
            return
        }
        if uuid == kBootMouseInputReportUUID {
            respondRead(request, with: lastMouseBootReport, on: peripheral)
            return
        }
        if uuid == kProtocolModeUUID {
            respondRead(request, with: Data([protocolMode]), on: peripheral)
            return
        }
        if uuid == kBatteryLevelUUID {
            respondRead(request, with: Data([batteryLevel]), on: peripheral)
            return
        }

        // Multiple 2A4D report instances in combo profile; match by characteristic instance.
        if let value = valueForReportCharacteristic(request.characteristic) {
            respondRead(request, with: value, on: peripheral)
            return
        }

        if let staticValue = request.characteristic.value {
            respondRead(request, with: staticValue, on: peripheral)
        } else {
            peripheral.respond(to: request, withResult: .attributeNotFound)
        }
    }

    /// Control Point / Protocol Mode writes. Per CoreBluetooth contract, respond once per callback.
    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]) {
        guard let first = requests.first else { return }

        var result: CBATTError.Code = .success

        for request in requests {
            if request.offset != 0 {
                result = .invalidOffset
                break
            }

            switch request.characteristic.uuid {
            case kProtocolModeUUID:
                guard let mode = request.value?.first, mode == 0x00 || mode == 0x01 else {
                    result = .unlikelyError
                    break
                }
                protocolMode = mode
                hidLog.info("Protocol mode set to \(mode == 0x00 ? "boot" : "report")")
            case kHIDControlPointUUID:
                controlPoint = request.value?.first ?? 0
                hidLog.debug("Control point write: \(self.controlPoint)")
            case kHIDReportUUID:
                // Only output reports should be writable (e.g. keyboard LEDs).
                if isSameCharacteristic(request.characteristic, as: keyboardOutputReportCharacteristic) {
                    lastKeyboardOutputReport = normalized(request.value ?? Data(), length: 1)
                } else {
                    result = .requestNotSupported
                }
            default:
                result = .requestNotSupported
            }

            if result != .success { break }
        }

        peripheral.respond(to: first, withResult: result)
    }

    func peripheralManagerIsReady(toUpdateSubscribers peripheral: CBPeripheralManager) {
        hidLog.debug("Transmit buffer ready")
    }

    private func valueForReportCharacteristic(_ characteristic: CBCharacteristic) -> Data? {
        if isSameCharacteristic(characteristic, as: systemControlInputReportCharacteristic) { return lastSystemControlInputReport }
        if isSameCharacteristic(characteristic, as: consumerInputReportCharacteristic) { return lastConsumerInputReport }
        if isSameCharacteristic(characteristic, as: mouseInputReportCharacteristic) { return lastMouseInputReport }
        if isSameCharacteristic(characteristic, as: keyboardInputReportCharacteristic) { return lastKeyboardInputReport }
        if isSameCharacteristic(characteristic, as: keyboardOutputReportCharacteristic) { return lastKeyboardOutputReport }
        if isSameCharacteristic(characteristic, as: gamepadInputReportCharacteristic) { return lastGamepadInputReport }
        return nil
    }

    private func isSameCharacteristic(_ lhs: CBCharacteristic, as rhs: CBMutableCharacteristic?) -> Bool {
        guard let rhs else { return false }
        return lhs === rhs
    }
}

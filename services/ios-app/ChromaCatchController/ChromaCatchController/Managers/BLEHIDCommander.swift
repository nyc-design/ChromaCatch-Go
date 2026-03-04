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
private let kHIDServiceShortUUID = CBUUID(string: "1812")
private let kHIDInfoUUID = CBUUID(string: "00002A4A-0000-1000-8000-00805F9B34FB")
private let kHIDReportMapUUID = CBUUID(string: "00002A4B-0000-1000-8000-00805F9B34FB")
private let kHIDControlPointUUID = CBUUID(string: "00002A4C-0000-1000-8000-00805F9B34FB")
private let kHIDReportUUID = CBUUID(string: "00002A4D-0000-1000-8000-00805F9B34FB")
private let kProtocolModeUUID = CBUUID(string: "00002A4E-0000-1000-8000-00805F9B34FB")
private let kBootKeyboardInputReportUUID = CBUUID(string: "00002A22-0000-1000-8000-00805F9B34FB")
private let kBootMouseInputReportUUID = CBUUID(string: "00002A33-0000-1000-8000-00805F9B34FB")

private let kGenericAccessUUID = CBUUID(string: "00001800-0000-1000-8000-00805F9B34FB")
private let kDeviceNameUUID = CBUUID(string: "00002A00-0000-1000-8000-00805F9B34FB")
private let kAppearanceUUID = CBUUID(string: "00002A01-0000-1000-8000-00805F9B34FB")

private let kBatteryServiceUUID = CBUUID(string: "0000180F-0000-1000-8000-00805F9B34FB")
private let kBatteryLevelUUID = CBUUID(string: "00002A19-0000-1000-8000-00805F9B34FB")

private let kDeviceInfoServiceUUID = CBUUID(string: "0000180A-0000-1000-8000-00805F9B34FB")
private let kManufacturerNameUUID = CBUUID(string: "00002A29-0000-1000-8000-00805F9B34FB")
private let kModelNumberUUID = CBUUID(string: "00002A24-0000-1000-8000-00805F9B34FB")
private let kPnPIDUUID = CBUUID(string: "00002A50-0000-1000-8000-00805F9B34FB")

private let kReportReferenceUUID = CBUUID(string: "00002908-0000-1000-8000-00805F9B34FB")

private let hidLog = Logger(subsystem: "com.chromacatch", category: "BLEHID")

// MARK: - HID Profile

enum HIDProfile: String, CaseIterable {
    case mouse
    case keyboard
    case gamepad
    case combo // mouse + keyboard
    case switchPro = "switch_pro" // Nintendo Switch / Switch 2 experimental profile
}

struct HIDHostCandidate: Identifiable, Equatable {
    let id: UUID
    let name: String
    let peripheral: CBPeripheral
    let rssi: Int

    static func == (lhs: HIDHostCandidate, rhs: HIDHostCandidate) -> Bool {
        lhs.id == rhs.id
    }
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

/// Nintendo-style report map used by NXBT's Switch Pro emulation profile.
/// This keeps report IDs and sizes aligned with the known-working NXBT handshake.
private let switchProReportDescriptor: [UInt8] = [
    0x05, 0x01, 0x09, 0x05, 0xA1, 0x01, 0x06, 0x01, 0xFF, 0x85, 0x21, 0x09,
    0x21, 0x75, 0x08, 0x95, 0x30, 0x81, 0x02, 0x85, 0x30, 0x09, 0x30, 0x75,
    0x08, 0x95, 0x30, 0x81, 0x02, 0x85, 0x31, 0x09, 0x31, 0x75, 0x08, 0x96,
    0x69, 0x01, 0x81, 0x02, 0x85, 0x32, 0x09, 0x32, 0x75, 0x08, 0x96, 0x69,
    0x01, 0x81, 0x02, 0x85, 0x33, 0x09, 0x33, 0x75, 0x08, 0x96, 0x69, 0x01,
    0x81, 0x02, 0x85, 0x3F, 0x05, 0x09, 0x19, 0x01, 0x29, 0x10, 0x15, 0x00,
    0x25, 0x01, 0x75, 0x01, 0x95, 0x10, 0x81, 0x02, 0x05, 0x01, 0x09, 0x39,
    0x15, 0x00, 0x25, 0x07, 0x75, 0x04, 0x95, 0x01, 0x81, 0x42, 0x05, 0x09,
    0x75, 0x04, 0x95, 0x01, 0x81, 0x01, 0x05, 0x01, 0x09, 0x30, 0x09, 0x31,
    0x09, 0x33, 0x09, 0x34, 0x16, 0x00, 0x00, 0x27, 0xFF, 0xFF, 0x00, 0x00,
    0x75, 0x10, 0x95, 0x04, 0x81, 0x02, 0x06, 0x01, 0xFF, 0x85, 0x01, 0x09,
    0x01, 0x75, 0x08, 0x95, 0x30, 0x91, 0x02, 0x85, 0x10, 0x09, 0x10, 0x75,
    0x08, 0x95, 0x30, 0x91, 0x02, 0x85, 0x11, 0x09, 0x11, 0x75, 0x08, 0x95,
    0x30, 0x91, 0x02, 0x85, 0x12, 0x09, 0x12, 0x75, 0x08, 0x95, 0x30, 0x91,
    0x02, 0xC0,
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
    @Published var discoveredHosts: [HIDHostCandidate] = []
    @Published var isScanningHosts = false
    @Published var connectedHostName: String?

    // BLE internals.
    private var peripheralManager: CBPeripheralManager?
    private var centralManager: CBCentralManager?
    private var hostPeripheral: CBPeripheral?
    private var systemControlInputReportCharacteristic: CBMutableCharacteristic?
    private var consumerInputReportCharacteristic: CBMutableCharacteristic?
    private var mouseInputReportCharacteristic: CBMutableCharacteristic?
    private var keyboardInputReportCharacteristic: CBMutableCharacteristic?
    private var keyboardOutputReportCharacteristic: CBMutableCharacteristic?
    private var gamepadInputReportCharacteristic: CBMutableCharacteristic?
    private var bootKeyboardInputCharacteristic: CBMutableCharacteristic?
    private var bootMouseInputCharacteristic: CBMutableCharacteristic?
    private var switchInputReport21Characteristic: CBMutableCharacteristic?
    private var switchInputReport30Characteristic: CBMutableCharacteristic?
    private var switchInputReport31Characteristic: CBMutableCharacteristic?
    private var switchInputReport32Characteristic: CBMutableCharacteristic?
    private var switchInputReport33Characteristic: CBMutableCharacteristic?
    private var switchInputReport3FCharacteristic: CBMutableCharacteristic?
    private var switchOutputReport01Characteristic: CBMutableCharacteristic?
    private var switchOutputReport10Characteristic: CBMutableCharacteristic?
    private var switchOutputReport11Characteristic: CBMutableCharacteristic?
    private var switchOutputReport12Characteristic: CBMutableCharacteristic?
    private var switchInputStreamingTimer: DispatchSourceTimer?

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
    private var lastSwitchInputReport21 = Data(repeating: 0, count: 0x30)
    private var lastSwitchInputReport30 = Data(repeating: 0, count: 0x30)
    private var lastSwitchInputReport31 = Data(repeating: 0, count: 0x169)
    private var lastSwitchInputReport32 = Data(repeating: 0, count: 0x169)
    private var lastSwitchInputReport33 = Data(repeating: 0, count: 0x169)
    private var lastSwitchInputReport3F = Data(repeating: 0, count: 11)
    private var lastSwitchOutputReport01 = Data(repeating: 0, count: 0x30)
    private var lastSwitchOutputReport10 = Data(repeating: 0, count: 0x30)
    private var lastSwitchOutputReport11 = Data(repeating: 0, count: 0x30)
    private var lastSwitchOutputReport12 = Data(repeating: 0, count: 0x30)
    private var batteryLevel: UInt8 = 100
    private var lastKeyboardBootReport = Data(repeating: 0, count: 8)
    private var lastMouseBootReport = Data(repeating: 0, count: 3)
    private var switchInputMode: UInt8 = 0x30
    private var switchVibrationEnabled = false
    private var switchIMUEnabled = false
    private var switchPlayerLights: UInt8 = 0x00
    private var switchPlayerNumber: Int?
    private var switchDeviceInfoQueried = false
    private var switchConnectionInfo: UInt8 = 0x00 // Pro Controller
    private var switchBatteryLevel: UInt8 = 0x90 // high nibble battery
    private var switchVibratorByte: UInt8 = 0xA0
    private var switchInputTimer: UInt8 = 0x00
    private var switchLastTimerTimestamp: TimeInterval?
    private let switchVibratorCandidates: [UInt8] = [0xA0, 0xB0, 0xC0, 0x90]
    private let switchBodyColor: [UInt8] = [0x82, 0x82, 0x82]
    private let switchButtonColor: [UInt8] = [0x0F, 0x0F, 0x0F]
    private let switchControllerAddress: [UInt8] = [0x7C, 0xBB, 0x8A, 0x52, 0x34, 0x12]

    override init() {
        super.init()
    }

    var isRunning: Bool { peripheralManager != nil }

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
        switchInputMode = 0x30
        switchVibrationEnabled = false
        switchIMUEnabled = false
        switchPlayerLights = 0x00
        switchPlayerNumber = nil
        switchDeviceInfoQueried = false
        switchConnectionInfo = 0x00
        switchBatteryLevel = 0x90
        switchVibratorByte = switchVibratorCandidates.randomElement() ?? 0xA0
        switchInputTimer = 0x00
        switchLastTimerTimestamp = nil

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
        // Also stop any optional host-central flow so we don't silently reconnect to a previously selected host.
        centralManager?.stopScan()
        if let hostPeripheral {
            centralManager?.cancelPeripheralConnection(hostPeripheral)
            self.hostPeripheral = nil
        }

        peripheralManager = nil
        systemControlInputReportCharacteristic = nil
        consumerInputReportCharacteristic = nil
        mouseInputReportCharacteristic = nil
        keyboardInputReportCharacteristic = nil
        keyboardOutputReportCharacteristic = nil
        gamepadInputReportCharacteristic = nil
        bootKeyboardInputCharacteristic = nil
        bootMouseInputCharacteristic = nil
        switchInputReport21Characteristic = nil
        switchInputReport30Characteristic = nil
        switchInputReport31Characteristic = nil
        switchInputReport32Characteristic = nil
        switchInputReport33Characteristic = nil
        switchInputReport3FCharacteristic = nil
        switchOutputReport01Characteristic = nil
        switchOutputReport10Characteristic = nil
        switchOutputReport11Characteristic = nil
        switchOutputReport12Characteristic = nil
        stopSwitchInputStreaming()

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
            self.isScanningHosts = false
            self.connectedHostName = nil
            self.discoveredHosts = []
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

    /// Force-disconnect current central subscriptions and restart advertising.
    func disconnectAndMakeDiscoverable() {
        guard peripheralManager != nil else { return }
        hidLog.info("Disconnecting active HID links and restarting discoverable advertising")

        // Ensure we also drop any app-initiated central connection to a host.
        disconnectHostConnection()
        stopHostScan()

        stop()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
            guard let self else { return }
            self.start(profile: self.activeProfile)
        }
    }

    // MARK: - Optional host scan/connect (Bluetouch-style)

    func startHostScan() {
        if centralManager == nil {
            centralManager = CBCentralManager(delegate: self, queue: nil)
        }

        guard let central = centralManager else { return }
        DispatchQueue.main.async {
            self.discoveredHosts = []
            self.isScanningHosts = true
        }

        guard central.state == .poweredOn else {
            hidLog.warning("Host scan requested before Bluetooth central was powered on")
            return
        }

        central.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
        hidLog.info("Started scanning for host devices")
    }

    func stopHostScan() {
        centralManager?.stopScan()
        DispatchQueue.main.async {
            self.isScanningHosts = false
        }
    }

    func connectToHost(_ host: HIDHostCandidate) {
        if centralManager == nil {
            centralManager = CBCentralManager(delegate: self, queue: nil)
        }
        guard let central = centralManager else { return }
        guard central.state == .poweredOn else {
            hidLog.warning("Cannot connect to host while central manager not powered on")
            return
        }

        stopHostScan()
        hostPeripheral = host.peripheral
        connectedHostName = host.name
        central.connect(host.peripheral, options: nil)
        hidLog.info("Connecting to host candidate: \(host.name, privacy: .public)")
    }

    func disconnectHostConnection() {
        guard let peripheral = hostPeripheral else { return }
        centralManager?.cancelPeripheralConnection(peripheral)
        hostPeripheral = nil
        DispatchQueue.main.async {
            self.connectedHostName = nil
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
        if activeProfile == .switchPro {
            sendCurrentSwitchInputReport()
            return
        }
        let lo = UInt8(gamepadButtons & 0xFF)
        let hi = UInt8((gamepadButtons >> 8) & 0xFF)
        sendReport(Data([0x03, lo, hi, gamepadHat, gamepadLeftX, gamepadLeftY, gamepadRightX, gamepadRightY]))
    }

    // MARK: - Switch Pro (experimental) protocol helpers

    private func sendSwitchInputReport30() {
        var payload = [UInt8](repeating: 0, count: 0x30)
        applySwitchStandardInput(to: &payload)
        applySwitchIMUDataIfNeeded(to: &payload)
        var report = Data([0x30])
        report.append(Data(payload))
        sendReport(report)
    }

    private func sendSwitchInputReport3F() {
        var payload = [UInt8](repeating: 0, count: 11)
        let buttonBytes = switchButtonStatusBytes()
        payload[0] = buttonBytes.0
        payload[1] = buttonBytes.2
        payload[2] = (gamepadHat <= 7) ? gamepadHat : 0x0F

        func axis16(_ v: UInt8) -> (UInt8, UInt8) {
            let value = UInt16((Int(v) * 65535) / 255)
            return (UInt8(value & 0xFF), UInt8((value >> 8) & 0xFF))
        }

        let (lx0, lx1) = axis16(gamepadLeftX)
        let (ly0, ly1) = axis16(gamepadLeftY)
        let (rx0, rx1) = axis16(gamepadRightX)
        let (ry0, ry1) = axis16(gamepadRightY)
        payload[3] = lx0
        payload[4] = lx1
        payload[5] = ly0
        payload[6] = ly1
        payload[7] = rx0
        payload[8] = rx1
        payload[9] = ry0
        payload[10] = ry1

        var report = Data([0x3F])
        report.append(Data(payload))
        sendReport(report)
    }

    private func sendCurrentSwitchInputReport() {
        if switchInputMode == 0x3F {
            sendSwitchInputReport3F()
        } else {
            sendSwitchInputReport30()
        }
    }

    private func sendSwitchSubcommandReply(ack: UInt8, subcommand: UInt8, extraData: [UInt8] = []) {
        var payload = [UInt8](repeating: 0, count: 0x30)
        switchVibratorByte = switchVibratorCandidates.randomElement() ?? 0xA0
        applySwitchStandardInput(to: &payload)
        payload[12] = ack
        payload[13] = subcommand
        if !extraData.isEmpty {
            let count = min(extraData.count, payload.count - 14)
            payload.replaceSubrange(14 ..< (14 + count), with: extraData.prefix(count))
        }
        var report = Data([0x21])
        report.append(Data(payload))
        sendReport(report)
    }

    private func sendSwitchHandshakeBurst(count: Int = 24) {
        for idx in 0 ..< count {
            DispatchQueue.main.asyncAfter(deadline: .now() + (Double(idx) / 15.0)) { [weak self] in
                guard let self, self.activeProfile == .switchPro else { return }
                self.sendCurrentSwitchInputReport()
            }
        }
    }

    private func startSwitchInputStreamingIfNeeded() {
        guard activeProfile == .switchPro else { return }
        guard switchInputStreamingTimer == nil else { return }

        lock.lock()
        let hasSubscribers = !subscribedCentrals.isEmpty
        lock.unlock()
        guard hasSubscribers else { return }

        let timer = DispatchSource.makeTimerSource(queue: .main)
        timer.schedule(deadline: .now() + .milliseconds(10), repeating: .milliseconds(8), leeway: .milliseconds(2)) // ~125Hz
        timer.setEventHandler { [weak self] in
            guard let self else { return }
            guard self.activeProfile == .switchPro else { return }
            self.sendCurrentSwitchInputReport()
        }
        switchInputStreamingTimer = timer
        timer.resume()
    }

    private func stopSwitchInputStreaming() {
        switchInputStreamingTimer?.cancel()
        switchInputStreamingTimer = nil
    }

    private func applySwitchStandardInput(to payload: inout [UInt8]) {
        let now = Date().timeIntervalSinceReferenceDate
        if let last = switchLastTimerTimestamp {
            let deltaMS = (now - last) * 1000.0
            let elapsedTicks = Int(deltaMS * 4.0)
            switchInputTimer = UInt8((Int(switchInputTimer) + elapsedTicks) & 0xFF)
        } else {
            switchInputTimer = 0
        }
        switchLastTimerTimestamp = now
        payload[0] = switchInputTimer

        guard switchDeviceInfoQueried else { return }

        payload[1] = switchBatteryLevel &+ switchConnectionInfo

        let buttonBytes = switchButtonStatusBytes()
        payload[2] = buttonBytes.0
        payload[3] = buttonBytes.1
        payload[4] = buttonBytes.2

        let left = encodeSwitchStick(x: gamepadLeftX, y: gamepadLeftY)
        let right = encodeSwitchStick(x: gamepadRightX, y: gamepadRightY)
        payload[5] = left.0
        payload[6] = left.1
        payload[7] = left.2
        payload[8] = right.0
        payload[9] = right.1
        payload[10] = right.2
        payload[11] = switchVibratorByte
    }

    private func encodeSwitchStick(x: UInt8, y: UInt8) -> (UInt8, UInt8, UInt8) {
        let sx = UInt16((Int(x) * 4095) / 255)
        let sy = UInt16((Int(y) * 4095) / 255)
        let b0 = UInt8(sx & 0xFF)
        let b1 = UInt8(((sx >> 8) & 0x0F) | ((sy & 0x0F) << 4))
        let b2 = UInt8((sy >> 4) & 0xFF)
        return (b0, b1, b2)
    }

    private func switchButtonStatusBytes() -> (UInt8, UInt8, UInt8) {
        var right: UInt8 = 0
        var shared: UInt8 = 0
        var left: UInt8 = 0

        func set(_ buttonIndex: Int, in storage: inout UInt8, bit: UInt8) {
            if gamepadButtons & UInt16(1 << buttonIndex) != 0 {
                storage |= bit
            }
        }

        // Face buttons (A/B/X/Y)
        set(3, in: &right, bit: 0x01) // Y
        set(2, in: &right, bit: 0x02) // X
        set(1, in: &right, bit: 0x04) // B
        set(0, in: &right, bit: 0x08) // A

        // Shoulder / triggers
        set(5, in: &right, bit: 0x40) // R
        set(7, in: &right, bit: 0x80) // ZR
        set(4, in: &left, bit: 0x40) // L
        set(6, in: &left, bit: 0x80) // ZL

        // Shared buttons
        set(8, in: &shared, bit: 0x02) // Minus
        set(9, in: &shared, bit: 0x01) // Plus
        set(11, in: &shared, bit: 0x04) // R stick click
        set(10, in: &shared, bit: 0x08) // L stick click
        set(12, in: &shared, bit: 0x10) // Home
        set(13, in: &shared, bit: 0x20) // Capture

        // D-pad via hat
        switch gamepadHat {
        case 0x00: left |= 0x02 // up
        case 0x01: left |= (0x02 | 0x04) // up-right
        case 0x02: left |= 0x04 // right
        case 0x03: left |= (0x01 | 0x04) // down-right
        case 0x04: left |= 0x01 // down
        case 0x05: left |= (0x01 | 0x08) // down-left
        case 0x06: left |= 0x08 // left
        case 0x07: left |= (0x02 | 0x08) // up-left
        default: break
        }

        return (right, shared, left)
    }

    private func applySwitchIMUDataIfNeeded(to payload: inout [UInt8]) {
        guard switchIMUEnabled else { return }
        let imuData: [UInt8] = [
            0x75, 0xFD, 0xFD, 0xFF, 0x09, 0x10, 0x21, 0x00, 0xD5, 0xFF, 0xE0, 0xFF,
            0x72, 0xFD, 0xF9, 0xFF, 0x0A, 0x10, 0x22, 0x00, 0xD5, 0xFF, 0xE0, 0xFF,
            0x76, 0xFD, 0xFC, 0xFF, 0x09, 0x10, 0x23, 0x00, 0xD5, 0xFF, 0xE0, 0xFF,
        ]
        let start = 12
        guard start < payload.count else { return }
        let count = min(imuData.count, payload.count - start)
        payload.replaceSubrange(start ..< (start + count), with: imuData.prefix(count))
    }

    private func switchSPIReadExtraData(subData: [UInt8]) -> [UInt8] {
        guard subData.count >= 5 else { return [] }

        let addrBottom = subData[0]
        let addrTop = subData[1]
        let readLength = min(Int(subData[4]), 0x1D)
        var memory = [UInt8](repeating: 0x00, count: readLength)

        func fill(_ offset: Int, _ values: [UInt8]) {
            guard offset < memory.count else { return }
            let count = min(values.count, memory.count - offset)
            memory.replaceSubrange(offset ..< (offset + count), with: values.prefix(count))
        }

        // Stick calibration / parameters values from NXBT's protocol emulation.
        let params: [UInt8] = [
            0x0F, 0x30, 0x61, 0x96, 0x30, 0xF3, 0xD4, 0x14, 0x54,
            0x41, 0x15, 0x54, 0xC7, 0x79, 0x9C, 0x33, 0x36, 0x63,
        ]

        if addrTop == 0x60, addrBottom == 0x00 {
            fill(0, Array(repeating: 0xFF, count: min(16, readLength)))
        } else if addrTop == 0x60, addrBottom == 0x50 {
            fill(0, switchBodyColor)
            fill(3, switchButtonColor)
            fill(6, Array(repeating: 0xFF, count: min(7, max(0, readLength - 6))))
        } else if addrTop == 0x60, addrBottom == 0x80 {
            fill(0, [0x50, 0xFD, 0x00, 0x00, 0xC6, 0x0F])
            fill(6, params)
        } else if addrTop == 0x60, addrBottom == 0x98 {
            fill(0, params)
        } else if addrTop == 0x80, addrBottom == 0x10 {
            fill(0, Array(repeating: 0xFF, count: min(24, readLength)))
        } else if addrTop == 0x60, addrBottom == 0x3D {
            let lCalibration: [UInt8] = [0xBA, 0xF5, 0x62, 0x6F, 0xC8, 0x77, 0xED, 0x95, 0x5B]
            let rCalibration: [UInt8] = [0x16, 0xD8, 0x7D, 0xF2, 0xB5, 0x5F, 0x86, 0x65, 0x5E]
            fill(0, lCalibration)
            fill(9, rCalibration)
            fill(18, [0xFF])
            fill(19, switchBodyColor)
            fill(22, switchButtonColor)
        } else if addrTop == 0x60, addrBottom == 0x20 {
            let sixAxisCalibration: [UInt8] = [
                0xD3, 0xFF, 0xD5, 0xFF, 0x55, 0x01,
                0x00, 0x40, 0x00, 0x40, 0x00, 0x40,
                0x19, 0x00, 0xDD, 0xFF, 0xDC, 0xFF,
                0x3B, 0x34, 0x3B, 0x34, 0x3B, 0x34,
            ]
            fill(0, sixAxisCalibration)
        }

        return [addrBottom, addrTop, 0x00, 0x00, UInt8(readLength)] + memory
    }

    private func handleSwitchSubcommand(_ subcommand: UInt8, data: [UInt8]) {
        switch subcommand {
        case 0x02: // Request device info
            switchDeviceInfoQueried = true
            sendSwitchSubcommandReply(
                ack: 0x82,
                subcommand: 0x02,
                extraData: [0x03, 0x8B, 0x03, 0x02] + switchControllerAddress + [0x01, 0x01]
            )
        case 0x08: // Set shipment
            sendSwitchSubcommandReply(ack: 0x80, subcommand: 0x08)
        case 0x10: // SPI read
            sendSwitchSubcommandReply(ack: 0x90, subcommand: 0x10, extraData: switchSPIReadExtraData(subData: data))
        case 0x03: // Set mode
            if let mode = data.first { switchInputMode = mode }
            sendSwitchSubcommandReply(ack: 0x80, subcommand: 0x03)
            sendCurrentSwitchInputReport()
        case 0x04: // Trigger buttons elapsed time
            sendSwitchSubcommandReply(ack: 0x83, subcommand: 0x04)
        case 0x40: // Toggle IMU
            switchIMUEnabled = (data.first ?? 0x00) == 0x01
            sendSwitchSubcommandReply(ack: 0x80, subcommand: 0x40)
        case 0x48: // Enable vibration
            switchVibrationEnabled = true
            sendSwitchSubcommandReply(ack: 0x82, subcommand: 0x48)
        case 0x30: // Set player lights
            switchPlayerLights = data.first ?? 0
            switch switchPlayerLights {
            case 0x01, 0x10: switchPlayerNumber = 1
            case 0x03, 0x30: switchPlayerNumber = 2
            case 0x07, 0x70: switchPlayerNumber = 3
            case 0x0F, 0xF0: switchPlayerNumber = 4
            default: switchPlayerNumber = nil
            }
            sendSwitchSubcommandReply(ack: 0x80, subcommand: 0x30)
        case 0x22: // Set NFC/IR state
            sendSwitchSubcommandReply(ack: 0x80, subcommand: 0x22)
        case 0x21: // Set NFC/IR config
            var extra = [UInt8](repeating: 0x00, count: 34)
            let params: [UInt8] = [0x01, 0x00, 0xFF, 0x00, 0x08, 0x00, 0x1B, 0x01]
            extra.replaceSubrange(0 ..< params.count, with: params)
            extra[33] = 0xC8
            sendSwitchSubcommandReply(ack: 0xA0, subcommand: 0x21, extraData: extra)
        default:
            // Match NXBT behavior: ignore unknown subcommands and keep full input stream going.
            sendCurrentSwitchInputReport()
        }
    }

    private func parseSwitchOutputWrite(fallbackReportID: UInt8, data: Data) -> (reportID: UInt8, payload: [UInt8]) {
        let bytes = [UInt8](data)
        guard !bytes.isEmpty else { return (fallbackReportID, []) }

        if bytes[0] == 0xA2, bytes.count >= 2 {
            return (bytes[1], Array(bytes.dropFirst(2)))
        }

        if [UInt8(0x01), 0x10, 0x11, 0x12, 0x80, 0x82].contains(bytes[0]) {
            return (bytes[0], Array(bytes.dropFirst(1)))
        }

        return (fallbackReportID, bytes)
    }

    private func handleSwitchOutputReportWrite(reportID: UInt8, data: Data) {
        let parsed = parseSwitchOutputWrite(fallbackReportID: reportID, data: data)
        hidLog.info("Switch output write: report=0x\(String(parsed.reportID, radix: 16, uppercase: true), privacy: .public), len=\(parsed.payload.count)")
        switch parsed.reportID {
        case 0x10:
            switchVibrationEnabled = true
            lastSwitchOutputReport10 = normalized(Data(parsed.payload), length: 0x30)
            sendCurrentSwitchInputReport()
        case 0x11:
            lastSwitchOutputReport11 = normalized(Data(parsed.payload), length: 0x30)
            sendCurrentSwitchInputReport()
        case 0x12:
            lastSwitchOutputReport12 = normalized(Data(parsed.payload), length: 0x30)
            sendCurrentSwitchInputReport()
        case 0x01:
            lastSwitchOutputReport01 = normalized(Data(parsed.payload), length: 0x30)
            guard parsed.payload.count >= 10 else {
                sendCurrentSwitchInputReport()
                return
            }
            let subcommand = parsed.payload[9]
            let subData = Array(parsed.payload.dropFirst(10))
            hidLog.info("Switch subcommand: 0x\(String(subcommand, radix: 16, uppercase: true), privacy: .public), len=\(subData.count)")
            handleSwitchSubcommand(subcommand, data: subData)
        default:
            sendCurrentSwitchInputReport()
        }
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
        case 0x21: // switch subcommand response
            lastSwitchInputReport21 = normalized(payload, length: 0x30)
        case 0x30: // switch input stream
            lastSwitchInputReport30 = normalized(payload, length: 0x30)
        case 0x31:
            lastSwitchInputReport31 = normalized(payload, length: 0x169)
        case 0x32:
            lastSwitchInputReport32 = normalized(payload, length: 0x169)
        case 0x33:
            lastSwitchInputReport33 = normalized(payload, length: 0x169)
        case 0x3F:
            lastSwitchInputReport3F = normalized(payload, length: 11)
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
        case 0x21: return switchInputReport21Characteristic
        case 0x30: return switchInputReport30Characteristic
        case 0x31: return switchInputReport31Characteristic
        case 0x32: return switchInputReport32Characteristic
        case 0x33: return switchInputReport33Characteristic
        case 0x3F: return switchInputReport3FCharacteristic
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
        stopSwitchInputStreaming()
        servicesAdded = 0
        systemControlInputReportCharacteristic = nil
        consumerInputReportCharacteristic = nil
        mouseInputReportCharacteristic = nil
        keyboardInputReportCharacteristic = nil
        keyboardOutputReportCharacteristic = nil
        gamepadInputReportCharacteristic = nil
        bootKeyboardInputCharacteristic = nil
        bootMouseInputCharacteristic = nil
        switchInputReport21Characteristic = nil
        switchInputReport30Characteristic = nil
        switchInputReport31Characteristic = nil
        switchInputReport32Characteristic = nil
        switchInputReport33Characteristic = nil
        switchInputReport3FCharacteristic = nil
        switchOutputReport01Characteristic = nil
        switchOutputReport10Characteristic = nil
        switchOutputReport11Characteristic = nil
        switchOutputReport12Characteristic = nil

        let advertisedName: String = (activeProfile == .switchPro) ? "Pro Controller" : "ChromaCatch HID"
        let appearance: UInt16
        switch activeProfile {
        case .mouse:
            appearance = kAppearanceMouse
        case .keyboard, .combo:
            appearance = kAppearanceKeyboard
        case .gamepad, .switchPro:
            appearance = kAppearanceGamepad
        }

        // --- 1) Generic Access Service ---
        let deviceNameChar = CBMutableCharacteristic(
            type: kDeviceNameUUID,
            properties: [.read],
            value: Data(advertisedName.utf8),
            permissions: [.readable]
        )
        let appearanceChar = CBMutableCharacteristic(
            type: kAppearanceUUID,
            properties: [.read],
            value: Data([UInt8(appearance & 0xFF), UInt8((appearance >> 8) & 0xFF)]),
            permissions: [.readable]
        )
        let gapService = CBMutableService(type: kGenericAccessUUID, primary: true)
        gapService.characteristics = [deviceNameChar, appearanceChar]

        // --- 2) Battery Service ---
        let batteryChar = CBMutableCharacteristic(
            type: kBatteryLevelUUID,
            properties: [.read, .notify],
            value: nil,
            permissions: [.readable]
        )
        let batteryService = CBMutableService(type: kBatteryServiceUUID, primary: false)
        batteryService.characteristics = [batteryChar]

        // --- 3) Device Information Service ---
        let manufacturerChar = CBMutableCharacteristic(
            type: kManufacturerNameUUID,
            properties: [.read],
            value: Data((activeProfile == .switchPro ? "Nintendo Co., Ltd." : "ChromaCatch").utf8),
            permissions: [.readable]
        )
        let modelNumberChar = CBMutableCharacteristic(
            type: kModelNumberUUID,
            properties: [.read],
            value: Data((activeProfile == .switchPro ? "Pro Controller" : "ChromaCatch HID").utf8),
            permissions: [.readable]
        )
        // PnP ID: [vendorSource(USB=0x02), vendorID, productID, productVersion]
        let pnpIDValue: Data = {
            if activeProfile == .switchPro {
                return Data([0x02, 0x7E, 0x05, 0x09, 0x20, 0x10, 0x01]) // Nintendo 0x057E, Pro Controller 0x2009
            }
            return Data([0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01])
        }()
        let pnpIDChar = CBMutableCharacteristic(
            type: kPnPIDUUID,
            properties: [.read],
            value: pnpIDValue,
            permissions: [.readable]
        )
        let deviceInfoService = CBMutableService(type: kDeviceInfoServiceUUID, primary: false)
        deviceInfoService.characteristics = [manufacturerChar, modelNumberChar, pnpIDChar]

        // --- 4) HID Service (primary) ---
        let reportDescriptor: [UInt8]
        switch self.activeProfile {
        case .mouse: reportDescriptor = mouseReportDescriptor
        case .keyboard: reportDescriptor = keyboardReportDescriptor
        case .gamepad: reportDescriptor = gamepadReportDescriptor
        case .combo: reportDescriptor = comboReportDescriptor
        case .switchPro: reportDescriptor = switchProReportDescriptor
        }

        // HID Information: bcdHID=1.11, bCountryCode=0, Flags=0x03
        let hidInfoChar = CBMutableCharacteristic(
            type: kHIDInfoUUID,
            properties: [.read],
            value: Data([0x11, 0x01, 0x08, 0x03]),
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
        // For minimal Switch profile we intentionally omit this extra link.
        if activeProfile != .switchPro {
            reportMapChar.descriptors = [CBMutableDescriptor(type: CBUUID(string: "00002907-0000-1000-8000-00805F9B34FB"), value: Data([0x19, 0x2A]))]
        }

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
        case .switchPro:
            switchInputReport21Characteristic = createReportCharacteristic(reportID: 0x21, reportType: 0x01, properties: [.read, .notify], permissions: [.readable])
            switchInputReport30Characteristic = createReportCharacteristic(reportID: 0x30, reportType: 0x01, properties: [.read, .notify], permissions: [.readable])
            switchInputReport31Characteristic = createReportCharacteristic(reportID: 0x31, reportType: 0x01, properties: [.read, .notify], permissions: [.readable])
            switchInputReport32Characteristic = createReportCharacteristic(reportID: 0x32, reportType: 0x01, properties: [.read, .notify], permissions: [.readable])
            switchInputReport33Characteristic = createReportCharacteristic(reportID: 0x33, reportType: 0x01, properties: [.read, .notify], permissions: [.readable])
            switchInputReport3FCharacteristic = createReportCharacteristic(reportID: 0x3F, reportType: 0x01, properties: [.read, .notify], permissions: [.readable])
            switchOutputReport01Characteristic = createReportCharacteristic(reportID: 0x01, reportType: 0x02, properties: [.read, .write, .writeWithoutResponse], permissions: [.readable, .writeable])
            switchOutputReport10Characteristic = createReportCharacteristic(reportID: 0x10, reportType: 0x02, properties: [.read, .write, .writeWithoutResponse], permissions: [.readable, .writeable])
            switchOutputReport11Characteristic = createReportCharacteristic(reportID: 0x11, reportType: 0x02, properties: [.read, .write, .writeWithoutResponse], permissions: [.readable, .writeable])
            switchOutputReport12Characteristic = createReportCharacteristic(reportID: 0x12, reportType: 0x02, properties: [.read, .write, .writeWithoutResponse], permissions: [.readable, .writeable])
            hidCharacteristics.append(contentsOf: [
                switchInputReport21Characteristic!,
                switchInputReport30Characteristic!,
                switchInputReport31Characteristic!,
                switchInputReport32Characteristic!,
                switchInputReport33Characteristic!,
                switchInputReport3FCharacteristic!,
                switchOutputReport01Characteristic!,
                switchOutputReport10Characteristic!,
                switchOutputReport11Characteristic!,
                switchOutputReport12Characteristic!,
            ])
        }

        let hidService = CBMutableService(type: kHIDServiceUUID, primary: true)
        hidService.characteristics = hidCharacteristics

        let servicesToAdd: [CBMutableService]
        if activeProfile == .switchPro {
            // Minimal BLE test profile: advertise only HID service to reduce GATT surface.
            servicesToAdd = [hidService]
        } else {
            servicesToAdd = [gapService, batteryService, deviceInfoService, hidService]
        }

        totalServices = servicesToAdd.count
        for service in servicesToAdd {
            pm.add(service)
        }
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
        let localName: String = (activeProfile == .switchPro) ? "Pro Controller" : "ChromaCatch HID"
        let serviceUUIDs: [CBUUID] = (activeProfile == .switchPro) ? [kHIDServiceShortUUID] : [kHIDServiceUUID]
        let payload: [String: Any] = [
            CBAdvertisementDataLocalNameKey: localName,
            CBAdvertisementDataServiceUUIDsKey: serviceUUIDs,
        ]
        hidLog.info("Starting advertising name=\(localName, privacy: .public), switchProfile=\(self.activeProfile == .switchPro), uuid=\(serviceUUIDs.first?.uuidString ?? "?")")
        pm.startAdvertising(payload)
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
            stopSwitchInputStreaming()
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

        if activeProfile == .switchPro {
            // Switch handshake reliability improves when we proactively stream initial neutral reports.
            sendSwitchHandshakeBurst()
            startSwitchInputStreamingIfNeeded()
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
            stopSwitchInputStreaming()
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
                // Output reports should be writable (keyboard LEDs and Switch-style output reports).
                if let (reportID, reportType) = reportReference(of: request.characteristic),
                   reportType == 0x02
                {
                    if reportID == 0x03 {
                        lastKeyboardOutputReport = normalized(request.value ?? Data(), length: 1)
                    } else if activeProfile == .switchPro, [UInt8(0x01), 0x10, 0x11, 0x12, 0x80, 0x82].contains(reportID) {
                        handleSwitchOutputReportWrite(reportID: reportID, data: request.value ?? Data())
                    } else {
                        result = .requestNotSupported
                    }
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
        guard let (reportID, reportType) = reportReference(of: characteristic) else {
            return nil
        }

        if reportType == 0x02 {
            if reportID == 0x03 { return lastKeyboardOutputReport }
            if reportID == 0x01 { return lastSwitchOutputReport01 }
            if reportID == 0x10 { return lastSwitchOutputReport10 }
            if reportID == 0x11 { return lastSwitchOutputReport11 }
            if reportID == 0x12 { return lastSwitchOutputReport12 }
            return nil
        }
        if reportType != 0x01 { return nil }

        switch reportID {
        case 0x01: return lastMouseInputReport
        case 0x02: return lastKeyboardInputReport
        case 0x03: return lastGamepadInputReport
        case 0x21: return lastSwitchInputReport21
        case 0x30: return lastSwitchInputReport30
        case 0x31: return lastSwitchInputReport31
        case 0x32: return lastSwitchInputReport32
        case 0x33: return lastSwitchInputReport33
        case 0x3F: return lastSwitchInputReport3F
        case 0x05: return lastSystemControlInputReport
        case 0x06: return lastConsumerInputReport
        default: return nil
        }
    }

    private func reportReference(of characteristic: CBCharacteristic) -> (UInt8, UInt8)? {
        guard let descriptors = characteristic.descriptors else { return nil }
        for descriptor in descriptors where descriptor.uuid == kReportReferenceUUID {
            if let data = descriptor.value as? Data, data.count >= 2 {
                return (data[0], data[1])
            }
            if let bytes = descriptor.value as? [UInt8], bytes.count >= 2 {
                return (bytes[0], bytes[1])
            }
            if let nsData = descriptor.value as? NSData, nsData.length >= 2 {
                var bytes = [UInt8](repeating: 0, count: 2)
                nsData.getBytes(&bytes, length: 2)
                return (bytes[0], bytes[1])
            }
        }
        return nil
    }
}

// MARK: - CBCentralManagerDelegate (optional host selection flow)

extension BLEHIDCommander: CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        guard isScanningHosts else { return }
        if central.state == .poweredOn {
            central.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
        }
    }

    func centralManager(_ central: CBCentralManager,
                        didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any],
                        rssi RSSI: NSNumber)
    {
        let localName = advertisementData[CBAdvertisementDataLocalNameKey] as? String
        let name = peripheral.name ?? localName ?? "Unknown Device"

        // Skip our own peripheral advertising and iTools dongle entries in this host picker.
        if name.contains("ChromaCatch") || name.hasPrefix("BT-01414") {
            return
        }

        let candidate = HIDHostCandidate(
            id: peripheral.identifier,
            name: name,
            peripheral: peripheral,
            rssi: RSSI.intValue
        )

        DispatchQueue.main.async {
            if let idx = self.discoveredHosts.firstIndex(where: { $0.id == candidate.id }) {
                self.discoveredHosts[idx] = candidate
            } else {
                self.discoveredHosts.append(candidate)
            }
            self.discoveredHosts.sort { $0.rssi > $1.rssi }
        }
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        hidLog.info("Host connected from app scan: \(peripheral.name ?? "unknown", privacy: .public)")
        DispatchQueue.main.async {
            self.connectedHostName = peripheral.name ?? "Unknown Host"
        }
    }

    func centralManager(_ central: CBCentralManager,
                        didDisconnectPeripheral peripheral: CBPeripheral,
                        error: Error?)
    {
        hidLog.info("Host disconnected: \(peripheral.name ?? "unknown", privacy: .public)")
        DispatchQueue.main.async {
            if self.hostPeripheral?.identifier == peripheral.identifier {
                self.hostPeripheral = nil
                self.connectedHostName = nil
            }
        }
    }

    func centralManager(_ central: CBCentralManager,
                        didFailToConnect peripheral: CBPeripheral,
                        error: Error?)
    {
        hidLog.error("Host connect failed: \(error?.localizedDescription ?? "unknown", privacy: .public)")
        DispatchQueue.main.async {
            if self.hostPeripheral?.identifier == peripheral.identifier {
                self.hostPeripheral = nil
                self.connectedHostName = nil
            }
        }
    }
}

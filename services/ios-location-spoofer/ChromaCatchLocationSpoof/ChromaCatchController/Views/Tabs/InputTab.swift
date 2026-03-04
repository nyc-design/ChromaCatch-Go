import SwiftUI

struct InputTab: View {
    @EnvironmentObject var coordinator: AppCoordinator
    @State private var manualText = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    // ESP32 Mode
                    CardView("ESP32 Mode", icon: "cpu") {
                        if coordinator.esp32Mode == ESP32Mode.unknown {
                            Text("Not connected — tap Refresh to query ESP32 mode.")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        } else {
                            InfoRow(label: "Input", value: coordinator.esp32Mode.inputMode)
                            InfoRow(label: "Output", value: coordinator.esp32Mode.outputDelivery)
                            InfoRow(label: "Mode", value: coordinator.esp32Mode.outputMode)
                        }

                        ConnectionRow(
                            label: "Command WS",
                            icon: "arrow.left.arrow.right.circle",
                            isConnected: coordinator.esp32Client.wsConnected,
                            detail: "\(coordinator.esp32Host):\(coordinator.esp32WSPort)",
                            activeColor: .green
                        )

                        HStack(spacing: 8) {
                            Button {
                                coordinator.toggleESP32CommandWebSocket()
                            } label: {
                                HStack {
                                    Image(systemName: coordinator.esp32Client.wsConnected ? "bolt.slash" : "bolt.horizontal.circle")
                                    Text(coordinator.esp32Client.wsConnected ? "Disconnect WS" : "Connect WS")
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.borderedProminent)

                            Button {
                                Task { await coordinator.queryESP32Mode() }
                            } label: {
                                HStack {
                                    Image(systemName: "arrow.clockwise")
                                    Text("Refresh Mode")
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.bordered)
                        }
                    }

                    // BLE HID Commander
                    CardView("BLE HID Commander", icon: "gamecontroller.fill") {
                        Text("Route commands directly via Bluetooth HID instead of ESP32. Works with Switch, 3DS, PC.")
                            .font(.caption)
                            .foregroundColor(.secondary)

                        Toggle("Use BLE HID", isOn: Binding(
                            get: { coordinator.useBLEHID },
                            set: { newValue in coordinator.setBLEHIDEnabled(newValue) }
                        ))

                        if coordinator.useBLEHID {
                            Picker("HID Mode", selection: Binding(
                                get: { coordinator.bleHIDPreferredProfile },
                                set: { coordinator.setBLEHIDProfile($0) }
                            )) {
                                Text("Mouse + Keyboard").tag(HIDProfile.combo)
                                Text("Gamepad").tag(HIDProfile.gamepad)
                                Text("Switch Pro (Exp)").tag(HIDProfile.switchPro)
                            }
                            .pickerStyle(.segmented)

                            ConnectionRow(
                                label: "Advertising", icon: "antenna.radiowaves.left.and.right",
                                isConnected: coordinator.bleHIDCommander.isAdvertising,
                                activeColor: .mint
                            )
                            ConnectionRow(
                                label: "Connected", icon: "link",
                                isConnected: coordinator.bleHIDCommander.isConnected,
                                detail: coordinator.bleHIDCommander.connectedDeviceName,
                                activeColor: .mint
                            )

                            HStack(spacing: 8) {
                                Button {
                                    coordinator.disconnectBLEHIDAndMakeDiscoverable()
                                } label: {
                                    Label("Disconnect + Discoverable", systemImage: "dot.radiowaves.left.and.right")
                                        .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.bordered)

                                Button {
                                    coordinator.bleHIDCommander.isScanningHosts ? coordinator.stopBLEHostScan() : coordinator.startBLEHostScan()
                                } label: {
                                    Label(coordinator.bleHIDCommander.isScanningHosts ? "Stop Scan" : "Scan Hosts", systemImage: "magnifyingglass")
                                        .frame(maxWidth: .infinity)
                                }
                                .buttonStyle(.bordered)
                            }

                            if let host = coordinator.bleHIDCommander.connectedHostName {
                                HStack {
                                    Text("Host: \(host)")
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                    Spacer()
                                    Button("Disconnect Host") {
                                        coordinator.disconnectBLEHost()
                                    }
                                    .buttonStyle(.bordered)
                                    .controlSize(.small)
                                }
                            }

                            if !coordinator.bleHIDCommander.discoveredHosts.isEmpty {
                                VStack(spacing: 8) {
                                    ForEach(coordinator.bleHIDCommander.discoveredHosts.prefix(8)) { host in
                                        HStack {
                                            VStack(alignment: .leading, spacing: 2) {
                                                Text(host.name)
                                                    .font(.subheadline)
                                                Text("RSSI \(host.rssi)")
                                                    .font(.caption2)
                                                    .foregroundColor(.secondary)
                                            }
                                            Spacer()
                                            Button("Connect") {
                                                coordinator.connectBLEHost(host)
                                            }
                                            .buttonStyle(.bordered)
                                            .controlSize(.small)
                                        }
                                    }
                                }
                            }
                        }
                    }

                    if coordinator.useBLEHID {
                        CardView("Direct Controls", icon: "hand.tap.fill") {
                            Text("Local controls are best-effort and yield to backend traffic when control WS is active.")
                                .font(.caption)
                                .foregroundColor(.secondary)

                            if coordinator.bleHIDPreferredProfile == .gamepad || coordinator.bleHIDPreferredProfile == .switchPro {
                                GamepadControlPane()
                            } else {
                                MouseKeyboardPane(manualText: $manualText)
                            }
                        }
                    }

                    // Command Routing
                    CardView("Command Routing", icon: "arrow.triangle.branch") {
                        InfoRow(label: "Target", value: coordinator.useBLEHID ? "BLE HID" : "ESP32")
                        InfoRow(label: "Commands Sent", value: "\(coordinator.commandsSent)")
                        InfoRow(label: "Commands Acked", value: "\(coordinator.commandsAcked)")
                        if coordinator.esp32Mode != ESP32Mode.unknown {
                            InfoRow(label: "ESP32 Mode", value: coordinator.esp32Mode.outputMode)
                        }
                    }
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Input")
        }
    }
}

private struct MouseKeyboardPane: View {
    @EnvironmentObject var coordinator: AppCoordinator
    @Binding var manualText: String
    @FocusState private var keyboardFocused: Bool
    @State private var lastPoint: CGPoint?

    var body: some View {
        VStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color(.tertiarySystemGroupedBackground))
                Text("Trackpad")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .padding(.top, 8)
                    .frame(maxHeight: .infinity, alignment: .top)
            }
            .frame(height: 180)
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        if let previous = lastPoint {
                            let dx = Int((value.location.x - previous.x) * 0.9)
                            let dy = Int((value.location.y - previous.y) * 0.9)
                            if dx != 0 || dy != 0 {
                                coordinator.sendManualMouseDelta(dx: dx, dy: dy)
                            }
                        }
                        lastPoint = value.location
                    }
                    .onEnded { value in
                        defer { lastPoint = nil }
                        if abs(value.translation.width) < 4 && abs(value.translation.height) < 4 {
                            coordinator.sendManualMouseClick()
                        }
                    }
            )

            HStack(spacing: 8) {
                Button {
                    coordinator.sendManualMouseClick()
                } label: {
                    Label("Left Click", systemImage: "cursorarrow.click")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)

                Button {
                    keyboardFocused = true
                } label: {
                    Label("Keyboard", systemImage: "keyboard")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }

            TextField("Type text and press send", text: $manualText)
                .textFieldStyle(.roundedBorder)
                .focused($keyboardFocused)
                .submitLabel(.send)
                .onSubmit {
                    guard !manualText.isEmpty else { return }
                    coordinator.sendManualKeyboardText(manualText)
                    manualText = ""
                }

            Button {
                guard !manualText.isEmpty else { return }
                coordinator.sendManualKeyboardText(manualText)
                manualText = ""
            } label: {
                Label("Send Text", systemImage: "paperplane.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
        }
    }
}

private struct GamepadControlPane: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        VStack(spacing: 12) {
            Text("Touch controls for quick local gamepad input.")
                .font(.caption)
                .foregroundColor(.secondary)

            HStack(alignment: .top, spacing: 16) {
                VStack(spacing: 6) {
                    HoldPadButton("↑") {
                        coordinator.sendManualGamepadHat(direction: 0)
                    } onRelease: {
                        coordinator.clearManualGamepadHat()
                    }
                    HStack(spacing: 6) {
                        HoldPadButton("←") {
                            coordinator.sendManualGamepadHat(direction: 6)
                        } onRelease: {
                            coordinator.clearManualGamepadHat()
                        }
                        HoldPadButton("↓") {
                            coordinator.sendManualGamepadHat(direction: 4)
                        } onRelease: {
                            coordinator.clearManualGamepadHat()
                        }
                        HoldPadButton("→") {
                            coordinator.sendManualGamepadHat(direction: 2)
                        } onRelease: {
                            coordinator.clearManualGamepadHat()
                        }
                    }
                }

                Spacer(minLength: 0)

                VStack(spacing: 8) {
                    HStack(spacing: 8) {
                        HoldPadButton("Y", color: .yellow) {
                            coordinator.sendManualGamepadButton(index: 3, pressed: true)
                        } onRelease: {
                            coordinator.sendManualGamepadButton(index: 3, pressed: false)
                        }
                        HoldPadButton("X", color: .blue) {
                            coordinator.sendManualGamepadButton(index: 2, pressed: true)
                        } onRelease: {
                            coordinator.sendManualGamepadButton(index: 2, pressed: false)
                        }
                    }
                    HStack(spacing: 8) {
                        HoldPadButton("B", color: .red) {
                            coordinator.sendManualGamepadButton(index: 1, pressed: true)
                        } onRelease: {
                            coordinator.sendManualGamepadButton(index: 1, pressed: false)
                        }
                        HoldPadButton("A", color: .green) {
                            coordinator.sendManualGamepadButton(index: 0, pressed: true)
                        } onRelease: {
                            coordinator.sendManualGamepadButton(index: 0, pressed: false)
                        }
                    }
                }
            }

            HStack(spacing: 8) {
                HoldPadButton("L", color: .orange) {
                    coordinator.sendManualGamepadButton(index: 4, pressed: true)
                } onRelease: {
                    coordinator.sendManualGamepadButton(index: 4, pressed: false)
                }

                HoldPadButton("R", color: .orange) {
                    coordinator.sendManualGamepadButton(index: 5, pressed: true)
                } onRelease: {
                    coordinator.sendManualGamepadButton(index: 5, pressed: false)
                }

                Button {
                    coordinator.sendManualSwitchSyncPress()
                } label: {
                    Label("L + R", systemImage: "dot.radiowaves.left.and.right")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
            }
        }
    }
}

private struct HoldPadButton: View {
    let title: String
    var color: Color = .gray
    let onPress: () -> Void
    let onRelease: () -> Void

    @State private var pressed = false

    init(_ title: String,
         color: Color = .gray,
         onPress: @escaping () -> Void,
         onRelease: @escaping () -> Void)
    {
        self.title = title
        self.color = color
        self.onPress = onPress
        self.onRelease = onRelease
    }

    var body: some View {
        Text(title)
            .font(.headline)
            .frame(width: 54, height: 54)
            .background((pressed ? color.opacity(0.85) : color.opacity(0.35)).clipShape(Circle()))
            .overlay(Circle().stroke(Color.white.opacity(0.2), lineWidth: 1))
            .contentShape(Circle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in
                        if !pressed {
                            pressed = true
                            onPress()
                        }
                    }
                    .onEnded { _ in
                        if pressed {
                            pressed = false
                            onRelease()
                        }
                    }
            )
    }
}

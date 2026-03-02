import ReplayKit
import SwiftUI

// MARK: - Content View

struct ContentView: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                StatusBar()

                ScrollView {
                    VStack(spacing: 16) {
                        SettingsSection()
                        DongleSection()
                        BroadcastSection()
                        CoordinateSection()
                        DNSFilterSection()
                        DongleInfoSection()
                        LogSection()
                    }
                    .padding()
                }
                .scrollDismissesKeyboard(.interactively)
            }
            .navigationTitle("ChromaCatch")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

// MARK: - Status Bar

struct StatusBar: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        HStack(spacing: 14) {
            StatusBadge(label: "BLE", connected: coordinator.bleManager.isConnected)
            StatusBadge(label: "CTL", connected: coordinator.wsManager.isConnected)
            StatusBadge(label: "LOC", connected: coordinator.locationWSManager.isConnected, activeColor: .blue)
            StatusBadge(label: "ESP", connected: coordinator.esp32Client.isReachable, activeColor: .purple)
            StatusBadge(
                label: "FWD",
                connected: coordinator.dongleController.isForwarding,
                activeColor: .orange
            )
            StatusBadge(
                label: "GPS",
                connected: coordinator.gpsAccurate,
                activeColor: .cyan
            )
            StatusBadge(
                label: "DNS",
                connected: coordinator.dnsFilterManager.isConnected,
                activeColor: .yellow
            )
        }
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity)
        .background(Color(.systemGroupedBackground))
    }
}

struct StatusBadge: View {
    let label: String
    let connected: Bool
    var activeColor: Color = .green

    var body: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(connected ? activeColor : .red)
                .frame(width: 10, height: 10)
            Text(label)
                .font(.caption)
                .fontWeight(.semibold)
        }
    }
}

// MARK: - Settings Section

struct SettingsSection: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Settings")
                .font(.headline)

            Group {
                TextField("Client ID", text: $coordinator.clientId)
                TextField("Backend Control WS URL", text: $coordinator.backendURL)
                TextField("Location Service WS URL", text: $coordinator.locationServiceURL)
                TextField("API Key", text: $coordinator.apiKey)
            }
            .textFieldStyle(.roundedBorder)
            .font(.system(.caption, design: .monospaced))
            .autocapitalization(.none)
            .disableAutocorrection(true)
            .submitLabel(.done)

            HStack(spacing: 8) {
                TextField("ESP32 Host", text: $coordinator.esp32Host)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(.caption, design: .monospaced))
                    .autocapitalization(.none)
                    .submitLabel(.done)
                TextField("Port", text: $coordinator.esp32Port)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(.caption, design: .monospaced))
                    .frame(width: 60)
                    .keyboardType(.numberPad)
            }

            HStack {
                Button(coordinator.isRunning ? "Stop" : "Start") {
                    if coordinator.isRunning {
                        coordinator.stop()
                    } else {
                        coordinator.start()
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(coordinator.isRunning ? .red : .green)
            }
        }
        .padding()
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(12)
    }
}

// MARK: - Dongle Section (BLE Pairing)

struct DongleSection: View {
    @EnvironmentObject var coordinator: AppCoordinator
    @State private var showScanner = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Dongle")
                .font(.headline)

            if coordinator.bleManager.isConnected {
                HStack {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.green)
                    Text(coordinator.bleManager.connectedDeviceName ?? "Connected")
                        .font(.system(.body, design: .monospaced))
                    Spacer()
                    Button("Disconnect") {
                        coordinator.disconnectDongle()
                    }
                    .buttonStyle(.bordered)
                    .tint(.red)
                }
            } else {
                Text("Scan to find and connect to your iTools BT dongle.")
                    .font(.caption)
                    .foregroundColor(.secondary)

                Button("Scan for Dongle") {
                    coordinator.startDongleScan()
                    showScanner = true
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding()
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(12)
        .sheet(isPresented: $showScanner) {
            coordinator.stopDongleScan()
        } content: {
            DongleScannerSheet(showScanner: $showScanner)
                .environmentObject(coordinator)
        }
    }
}

struct DongleScannerSheet: View {
    @EnvironmentObject var coordinator: AppCoordinator
    @Binding var showScanner: Bool

    var body: some View {
        NavigationStack {
            List {
                if coordinator.bleManager.discoveredDevices.isEmpty && coordinator.bleManager.isScanning {
                    HStack {
                        ProgressView()
                            .padding(.trailing, 8)
                        Text("Searching for BT-01414 devices...")
                            .foregroundColor(.secondary)
                    }
                }

                ForEach(coordinator.bleManager.discoveredDevices) { device in
                    Button {
                        coordinator.connectToDongle(device)
                        showScanner = false
                    } label: {
                        HStack {
                            VStack(alignment: .leading) {
                                Text(device.name)
                                    .font(.system(.body, design: .monospaced))
                                Text(device.id.uuidString.prefix(8) + "...")
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                            Spacer()
                            SignalStrength(rssi: device.rssi)
                        }
                    }
                    .foregroundColor(.primary)
                }
            }
            .navigationTitle("Select Dongle")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { showScanner = false }
                }
            }
        }
    }
}

struct SignalStrength: View {
    let rssi: Int

    var bars: Int {
        if rssi > -50 { return 4 }
        if rssi > -65 { return 3 }
        if rssi > -80 { return 2 }
        return 1
    }

    var body: some View {
        HStack(spacing: 1) {
            ForEach(1...4, id: \.self) { bar in
                RoundedRectangle(cornerRadius: 1)
                    .fill(bar <= bars ? Color.green : Color.gray.opacity(0.3))
                    .frame(width: 4, height: CGFloat(bar * 4 + 2))
            }
        }
    }
}

// MARK: - Broadcast Section (ReplayKit)

struct BroadcastSection: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Screen Broadcast")
                .font(.headline)

            Text("Start broadcast from Control Center to stream your screen to the backend.")
                .font(.caption)
                .foregroundColor(.secondary)

            BroadcastPickerRepresentable()
                .frame(width: 44, height: 44)
        }
        .padding()
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(12)
    }
}

/// Wraps RPSystemBroadcastPickerView for SwiftUI.
struct BroadcastPickerRepresentable: UIViewRepresentable {
    func makeUIView(context: Context) -> RPSystemBroadcastPickerView {
        let picker = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 44, height: 44))
        picker.preferredExtension = "com.chromacatch.controller.broadcast"
        picker.showsMicrophoneButton = false
        return picker
    }

    func updateUIView(_ uiView: RPSystemBroadcastPickerView, context: Context) {}
}

// MARK: - Coordinate Section

struct CoordinateSection: View {
    @EnvironmentObject var coordinator: AppCoordinator
    @State private var coordText = "33.448, -96.789"

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Location")
                .font(.headline)

            if coordinator.dongleController.currentLat != 0 {
                HStack {
                    Text("Target:")
                        .foregroundColor(.secondary)
                    Text(String(format: "%.6f, %.6f",
                                coordinator.dongleController.currentLat,
                                coordinator.dongleController.currentLon))
                    .font(.system(.body, design: .monospaced))
                }
            }

            if coordinator.iosReportedLat != 0 {
                HStack {
                    Text("iOS GPS:")
                        .foregroundColor(.secondary)
                    Text(String(format: "%.6f, %.6f",
                                coordinator.iosReportedLat,
                                coordinator.iosReportedLon))
                    .font(.system(.body, design: .monospaced))
                }

                HStack {
                    Text("Drift:")
                        .foregroundColor(.secondary)
                    Text(String(format: "%.0fm", coordinator.gpsDriftMeters))
                        .font(.system(.body, design: .monospaced))
                        .foregroundColor(coordinator.gpsAccurate ? .green : .red)
                    if coordinator.gpsAccurate {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundColor(.green)
                            .font(.caption)
                    } else {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundColor(.red)
                            .font(.caption)
                    }
                }
            }

            Text("Manual Override")
                .font(.subheadline)
                .foregroundColor(.secondary)

            TextField("lat, lon  e.g. (1.2978, 103.813)", text: $coordText)
                .textFieldStyle(.roundedBorder)
                .keyboardType(.numbersAndPunctuation)
                .submitLabel(.send)
                .onSubmit { sendParsedCoords() }

            Button("Send Location") { sendParsedCoords() }
                .buttonStyle(.borderedProminent)
                .disabled(!coordinator.bleManager.isConnected || parseCoords() == nil)
        }
        .padding()
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(12)
    }

    private func parseCoords() -> (Double, Double)? {
        let cleaned = coordText
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "(", with: "")
            .replacingOccurrences(of: ")", with: "")
        let parts = cleaned.split(separator: ",").map {
            $0.trimmingCharacters(in: .whitespaces)
        }
        guard parts.count == 2,
              let lat = Double(parts[0]),
              let lon = Double(parts[1]) else { return nil }
        return (lat, lon)
    }

    private func sendParsedCoords() {
        guard let (lat, lon) = parseCoords() else { return }
        coordinator.sendManualLocation(lat: lat, lon: lon)
    }
}

// MARK: - DNS Filter Section

struct DNSFilterSection: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Location Guard")
                .font(.headline)

            Text("Block Apple Wi-Fi/cell positioning to prevent location rebound while spoofing.")
                .font(.caption)
                .foregroundColor(.secondary)

            Toggle("Block Apple Location Services", isOn: Binding(
                get: { coordinator.dnsFilterEnabled },
                set: { _ in coordinator.toggleDNSFilter() }
            ))

            HStack {
                Text("Status:")
                    .foregroundColor(.secondary)
                Text(coordinator.dnsFilterManager.isConnected ? "Active" : "Inactive")
                    .font(.system(.body, design: .monospaced))
                    .foregroundColor(coordinator.dnsFilterManager.isConnected ? .green : .secondary)
            }
        }
        .padding()
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(12)
    }
}

// MARK: - Dongle Info Section

struct DongleInfoSection: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Status")
                .font(.headline)

            InfoRow(label: "Dongle Init", value: coordinator.dongleController.isInitialized ? "Yes" : "No")
            InfoRow(label: "RP Status", value: coordinator.bleManager.rpStatus)
            InfoRow(label: "NMEA Sent", value: "\(coordinator.dongleController.nmeaSentCount)")
            InfoRow(label: "Forwarding", value: coordinator.dongleController.isForwarding ? "Active" : "Inactive")
            InfoRow(label: "Cmds Sent", value: "\(coordinator.commandsSent)")
            InfoRow(label: "Cmds Acked", value: "\(coordinator.commandsAcked)")
        }
        .padding()
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(12)
    }
}

struct InfoRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack {
            Text(label)
                .foregroundColor(.secondary)
            Spacer()
            Text(value)
                .font(.system(.body, design: .monospaced))
        }
    }
}

// MARK: - Log Section

struct LogSection: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Log")
                    .font(.headline)
                Spacer()
                Button("Clear") {
                    coordinator.logs.removeAll()
                }
                .font(.caption)
            }

            ForEach(coordinator.logs.prefix(100)) { entry in
                HStack(alignment: .top, spacing: 8) {
                    Text(entry.timestamp, format: .dateTime.hour().minute().second())
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundColor(.secondary)
                        .frame(width: 70, alignment: .leading)
                    Text(entry.message)
                        .font(.system(.caption, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .padding()
        .background(Color(.secondarySystemGroupedBackground))
        .cornerRadius(12)
    }
}

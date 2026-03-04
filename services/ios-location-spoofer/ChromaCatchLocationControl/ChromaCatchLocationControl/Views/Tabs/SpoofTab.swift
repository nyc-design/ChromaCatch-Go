import SwiftUI

struct SpoofTab: View {
    @EnvironmentObject var coordinator: AppCoordinator
    @State private var showScanner = false
    @State private var coordText = "33.448, -96.789"

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    // Location WebSocket
                    CardView("Location Service", icon: "location.fill") {
                        ConnectionRow(
                            label: "Location WebSocket", icon: "bolt.fill",
                            isConnected: coordinator.locationWSManager.isConnected,
                            activeColor: .blue
                        )

                        Button {
                            if coordinator.isLocationRunning {
                                coordinator.stopLocation()
                            } else {
                                coordinator.startLocation()
                            }
                        } label: {
                            HStack {
                                Image(systemName: coordinator.isLocationRunning ? "stop.fill" : "play.fill")
                                Text(coordinator.isLocationRunning ? "Disconnect" : "Connect")
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(coordinator.isLocationRunning ? .red : .blue)
                    }

                    // Dongle
                    CardView("GPS Dongle", icon: "antenna.radiowaves.left.and.right") {
                        if coordinator.bleManager.isConnected {
                            ConnectionRow(
                                label: coordinator.bleManager.connectedDeviceName ?? "Connected",
                                icon: "checkmark.circle.fill",
                                isConnected: true
                            )

                            Button("Disconnect") {
                                coordinator.disconnectDongle()
                            }
                            .buttonStyle(.bordered)
                            .tint(.red)
                        } else {
                            Text("Scan to find and connect to your iTools BT dongle.")
                                .font(.caption)
                                .foregroundColor(.secondary)

                            Button {
                                coordinator.startDongleScan()
                                showScanner = true
                            } label: {
                                HStack {
                                    Image(systemName: "magnifyingglass")
                                    Text("Scan for Dongle")
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.borderedProminent)
                        }
                    }

                    // Location
                    CardView("Location", icon: "map.fill") {
                        if coordinator.dongleController.currentLat != 0 {
                            InfoRow(
                                label: "Target",
                                value: String(
                                    format: "%.6f, %.6f",
                                    coordinator.dongleController.currentLat,
                                    coordinator.dongleController.currentLon
                                )
                            )
                        }

                        if coordinator.iosReportedLat != 0 {
                            InfoRow(
                                label: "iOS GPS",
                                value: String(
                                    format: "%.6f, %.6f",
                                    coordinator.iosReportedLat,
                                    coordinator.iosReportedLon
                                )
                            )

                            HStack {
                                Text("Drift")
                                    .foregroundColor(.secondary)
                                Spacer()
                                Text(String(format: "%.0fm", coordinator.gpsDriftMeters))
                                    .font(.system(.body, design: .monospaced))
                                    .foregroundColor(coordinator.gpsAccurate ? .green : .red)
                                Image(systemName: coordinator.gpsAccurate ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                                    .foregroundColor(coordinator.gpsAccurate ? .green : .red)
                                    .font(.caption)
                            }
                        }

                        Divider()

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

                    // Location Guard (DNS Filter)
                    CardView("Location Guard", icon: "shield.fill") {
                        Text("Block Apple Wi-Fi/cell positioning to prevent location rebound while spoofing.")
                            .font(.caption)
                            .foregroundColor(.secondary)

                        Toggle("Block Apple Location Services", isOn: Binding(
                            get: { coordinator.dnsFilterEnabled },
                            set: { _ in coordinator.toggleDNSFilter() }
                        ))

                        ConnectionRow(
                            label: "DNS Filter", icon: "network",
                            isConnected: coordinator.dnsFilterManager.isConnected,
                            activeColor: .yellow
                        )
                    }

                    // Dongle Status
                    CardView("Dongle Status", icon: "info.circle.fill") {
                        InfoRow(label: "Initialized", value: coordinator.dongleController.isInitialized ? "Yes" : "No")
                        InfoRow(label: "RP Status", value: coordinator.bleManager.rpStatus)
                        InfoRow(label: "NMEA Sent", value: "\(coordinator.dongleController.nmeaSentCount)")
                        InfoRow(label: "Forwarding", value: coordinator.dongleController.isForwarding ? "Active" : "Inactive")
                    }
                }
                .padding()
            }
            .scrollDismissesKeyboard(.interactively)
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Spoof")
            .sheet(isPresented: $showScanner) {
                coordinator.stopDongleScan()
            } content: {
                DongleScannerSheet(showScanner: $showScanner)
                    .environmentObject(coordinator)
            }
        }
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
              let lon = Double(parts[1])
        else { return nil }
        return (lat, lon)
    }

    private func sendParsedCoords() {
        guard let (lat, lon) = parseCoords() else { return }
        coordinator.sendManualLocation(lat: lat, lon: lon)
    }
}

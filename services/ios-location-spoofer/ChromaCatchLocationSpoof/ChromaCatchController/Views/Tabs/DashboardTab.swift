import SwiftUI

struct DashboardTab: View {
    @EnvironmentObject var coordinator: AppCoordinator

    private let columns = [
        GridItem(.flexible()),
        GridItem(.flexible()),
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    // Connection Status Grid
                    CardView("Connections", icon: "network") {
                        LazyVGrid(columns: columns, spacing: 8) {
                            StatusBadge(label: "BLE Dongle", isConnected: coordinator.bleManager.isConnected)
                            StatusBadge(label: "Backend WS", isConnected: coordinator.wsManager.isConnected)
                            StatusBadge(label: "Location WS", isConnected: coordinator.locationWSManager.isConnected, activeColor: .blue)
                            StatusBadge(label: "ESP32", isConnected: coordinator.esp32Client.isReachable, activeColor: .purple)
                            StatusBadge(label: "BLE HID", isConnected: coordinator.bleHIDCommander.isConnected, activeColor: .mint)
                            StatusBadge(label: "Forwarding", isConnected: coordinator.dongleController.isForwarding, activeColor: .orange)
                            StatusBadge(label: "GPS Lock", isConnected: coordinator.gpsAccurate, activeColor: .cyan)
                            StatusBadge(label: "DNS Filter", isConnected: coordinator.dnsFilterManager.isConnected, activeColor: .yellow)
                        }
                    }

                    // Metrics
                    CardView("Metrics", icon: "chart.bar.fill") {
                        InfoRow(label: "Commands Sent", value: "\(coordinator.commandsSent)")
                        InfoRow(label: "Commands Acked", value: "\(coordinator.commandsAcked)")
                        InfoRow(label: "NMEA Sent", value: "\(coordinator.dongleController.nmeaSentCount)")
                        InfoRow(label: "Command Target", value: coordinator.useBLEHID ? "BLE HID" : "ESP32")
                        if coordinator.gpsDriftMeters > 0 {
                            InfoRow(label: "GPS Drift", value: String(format: "%.0fm", coordinator.gpsDriftMeters))
                        }
                        InfoRow(label: "Uptime", value: formatUptime(Date().timeIntervalSince(coordinator.startTime)))
                    }

                    // Connection Details
                    CardView("Details", icon: "info.circle.fill") {
                        ConnectionRow(
                            label: "BLE Dongle", icon: "antenna.radiowaves.left.and.right",
                            isConnected: coordinator.bleManager.isConnected,
                            detail: coordinator.bleManager.connectedDeviceName
                        )
                        ConnectionRow(
                            label: "Backend WS", icon: "server.rack",
                            isConnected: coordinator.wsManager.isConnected
                        )
                        ConnectionRow(
                            label: "Location WS", icon: "location.fill",
                            isConnected: coordinator.locationWSManager.isConnected,
                            activeColor: .blue
                        )
                        ConnectionRow(
                            label: "ESP32", icon: "cpu",
                            isConnected: coordinator.esp32Client.isReachable,
                            detail: coordinator.esp32Mode != ESP32Mode.unknown ? coordinator.esp32Mode.outputMode : nil,
                            activeColor: .purple
                        )
                        ConnectionRow(
                            label: "BLE HID", icon: "gamecontroller",
                            isConnected: coordinator.bleHIDCommander.isConnected,
                            detail: coordinator.bleHIDCommander.connectedDeviceName,
                            activeColor: .mint
                        )
                    }
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Dashboard")
        }
    }

    private func formatUptime(_ seconds: TimeInterval) -> String {
        let h = Int(seconds) / 3600
        let m = (Int(seconds) % 3600) / 60
        let s = Int(seconds) % 60
        return String(format: "%02d:%02d:%02d", h, m, s)
    }
}

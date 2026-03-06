import SwiftUI

/// Sheet for scanning and connecting to iTools BT dongle devices.
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

import ReplayKit
import SwiftUI

struct VideoTab: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    // Backend Connection
                    CardView("Backend Connection", icon: "server.rack") {
                        ConnectionRow(
                            label: "Control WebSocket", icon: "bolt.fill",
                            isConnected: coordinator.wsManager.isConnected
                        )

                        Button {
                            if coordinator.isBackendRunning {
                                coordinator.stopBackend()
                            } else {
                                coordinator.startBackend()
                            }
                        } label: {
                            HStack {
                                Image(systemName: coordinator.isBackendRunning ? "stop.fill" : "play.fill")
                                Text(coordinator.isBackendRunning ? "Disconnect" : "Connect")
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(coordinator.isBackendRunning ? .red : .green)
                    }

                    // Video Source
                    CardView("Video Source", icon: "video.fill") {
                        // ReplayKit broadcast
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Screen Broadcast")
                                .font(.subheadline)
                                .fontWeight(.semibold)
                            Text("Start broadcast from Control Center to stream your screen to the backend.")
                                .font(.caption)
                                .foregroundColor(.secondary)
                            BroadcastPickerRepresentable()
                                .frame(width: 44, height: 44)
                        }

                        Divider()

                        // UVC placeholder
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text("UVC Capture")
                                    .font(.subheadline)
                                    .fontWeight(.semibold)
                                Spacer()
                                Text("Coming Soon")
                                    .font(.caption2)
                                    .fontWeight(.bold)
                                    .padding(.horizontal, 8)
                                    .padding(.vertical, 4)
                                    .background(Color.purple.opacity(0.2))
                                    .foregroundColor(.purple)
                                    .cornerRadius(6)
                            }
                            Text("USB video capture card input for direct feed.")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        .opacity(0.5)
                    }
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("Video")
        }
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

    func updateUIView(_: RPSystemBroadcastPickerView, context: Context) {}
}

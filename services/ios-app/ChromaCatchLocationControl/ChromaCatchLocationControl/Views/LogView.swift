import SwiftUI

/// Full-screen log viewer, navigated to from Settings tab.
struct LogView: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 4) {
                ForEach(coordinator.logs.prefix(500)) { entry in
                    HStack(alignment: .top, spacing: 8) {
                        Text(entry.timestamp, format: .dateTime.hour().minute().second())
                            .font(.system(.caption2, design: .monospaced))
                            .foregroundColor(.secondary)
                            .frame(width: 70, alignment: .leading)
                        Text(entry.message)
                            .font(.system(.caption, design: .monospaced))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(.horizontal)
                }
            }
            .padding(.vertical, 8)
        }
        .navigationTitle("Logs")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Clear") {
                    coordinator.logs.removeAll()
                }
            }
        }
        .background(Color(.systemGroupedBackground))
    }
}

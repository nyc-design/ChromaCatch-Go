import SwiftUI

struct SettingsTab: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        NavigationStack {
            Form {
                Section("Location Service") {
                    TextField("Location Service WS URL", text: $coordinator.locationServiceURL)
                        .font(.system(.caption, design: .monospaced))
                        .autocapitalization(.none)
                        .disableAutocorrection(true)

                    TextField("API Key", text: $coordinator.apiKey)
                        .font(.system(.caption, design: .monospaced))
                        .autocapitalization(.none)
                        .disableAutocorrection(true)

                    TextField("Client ID", text: $coordinator.clientId)
                        .font(.system(.caption, design: .monospaced))
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                }

                Section {
                    NavigationLink {
                        LogView()
                    } label: {
                        HStack {
                            Image(systemName: "doc.text")
                            Text("Logs")
                            Spacer()
                            Text("\(coordinator.logs.count)")
                                .font(.system(.caption, design: .monospaced))
                                .foregroundColor(.secondary)
                        }
                    }
                }
            }
            .navigationTitle("Settings")
        }
    }
}

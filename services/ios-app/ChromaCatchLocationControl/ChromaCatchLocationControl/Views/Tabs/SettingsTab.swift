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

                Section("Sniper Service") {
                    TextField("Sniper API URL", text: $coordinator.sniperServiceURL)
                        .font(.system(.caption, design: .monospaced))
                        .autocapitalization(.none)
                        .disableAutocorrection(true)

                    Text("Example: https://8010--main--chromacatch-go-agents--nyc-design.apps.coder.tapiavala.com")
                        .font(.caption2)
                        .foregroundColor(.secondary)
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

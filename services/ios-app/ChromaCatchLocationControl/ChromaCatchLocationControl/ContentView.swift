import SwiftUI

struct ContentView: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        TabView {
            SpoofTab()
                .tabItem {
                    Label("Spoof", systemImage: "location.fill")
                }

            SniperTab()
                .tabItem {
                    Label("Sniper", systemImage: "scope")
                }

            SettingsTab()
                .tabItem {
                    Label("Settings", systemImage: "gearshape.fill")
                }
        }
        .tint(.blue)
    }
}

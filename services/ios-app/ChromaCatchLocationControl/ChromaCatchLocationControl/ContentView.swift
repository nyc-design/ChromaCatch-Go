import SwiftUI

struct ContentView: View {
    @EnvironmentObject var coordinator: AppCoordinator

    var body: some View {
        TabView {
            SpoofTab()
                .tabItem {
                    Label("Spoof", systemImage: "location.fill")
                }

            SettingsTab()
                .tabItem {
                    Label("Settings", systemImage: "gearshape.fill")
                }
        }
        .tint(.blue)
    }
}

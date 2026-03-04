import Combine
import Foundation
import NetworkExtension

/// Manages the DNS filter VPN tunnel that blocks Apple location service domains.
///
/// When enabled, creates a lightweight NEPacketTunnelProvider configuration that sinkoles
/// DNS queries for Wi-Fi/cell positioning domains, forcing iOS to rely on the GPS dongle.
@MainActor
class DNSFilterManager: ObservableObject {
    @Published var isConnected: Bool = false

    private var manager: NETunnelProviderManager?
    private var statusObserver: NSObjectProtocol?
    private let log: (String) -> Void

    private static let bundleId = "com.chromacatch.spoof.dns"

    init(log: @escaping (String) -> Void = { NSLog("[DNSFilter] %@", $0) }) {
        self.log = log
        observeVPNStatus()
    }

    deinit {
        if let obs = statusObserver {
            NotificationCenter.default.removeObserver(obs)
        }
    }

    /// Load existing VPN config or create a fresh one.
    func loadOrCreate() async {
        do {
            let managers = try await NETunnelProviderManager.loadAllFromPreferences()
            if let existing = managers.first(where: {
                ($0.protocolConfiguration as? NETunnelProviderProtocol)?
                    .providerBundleIdentifier == Self.bundleId
            }) {
                manager = existing
                log("Loaded existing DNS filter config")
            } else {
                let m = NETunnelProviderManager()
                let proto = NETunnelProviderProtocol()
                proto.providerBundleIdentifier = Self.bundleId
                proto.serverAddress = "ChromaCatch DNS Filter"
                m.protocolConfiguration = proto
                m.localizedDescription = "ChromaCatch Location Guard"
                m.isEnabled = true
                manager = m
                log("Created new DNS filter config")
            }
            updateStatus()
        } catch {
            log("Failed to load VPN configs: \(error.localizedDescription)")
        }
    }

    /// Enable the DNS filter tunnel.
    func enable() async {
        if manager == nil {
            await loadOrCreate()
        }
        guard let manager = manager else { return }

        manager.isEnabled = true
        do {
            try await manager.saveToPreferences()
            // Re-load after save (required by iOS)
            try await manager.loadFromPreferences()
            try manager.connection.startVPNTunnel()
            log("DNS filter enabled")
        } catch {
            log("Failed to start DNS filter: \(error.localizedDescription)")
        }
    }

    /// Disable the DNS filter tunnel.
    func disable() {
        guard let manager = manager else { return }
        manager.connection.stopVPNTunnel()
        log("DNS filter disabled")
    }

    private func observeVPNStatus() {
        statusObserver = NotificationCenter.default.addObserver(
            forName: .NEVPNStatusDidChange,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.updateStatus()
            }
        }
    }

    private func updateStatus() {
        guard let manager = manager else {
            isConnected = false
            return
        }
        let status = manager.connection.status
        isConnected = (status == .connected)
    }
}

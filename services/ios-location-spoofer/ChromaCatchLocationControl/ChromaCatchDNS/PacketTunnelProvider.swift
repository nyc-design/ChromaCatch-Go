import NetworkExtension

/// Lightweight packet tunnel that sinkoles DNS queries for Apple location service domains.
///
/// This prevents iOS from using Wi-Fi/cell positioning to override the GPS dongle's spoofed
/// coordinates. Only location-related Apple domains are affected — all other traffic is untouched.
class PacketTunnelProvider: NEPacketTunnelProvider {

    /// Apple domains responsible for Wi-Fi and cell-tower positioning.
    /// Blocking these forces iOS to rely solely on the GPS dongle for location.
    private static let blockedDomains = [
        "gs-loc.apple.com",       // Wi-Fi Location API (WLOC binary protocol)
        "ls.apple.com",           // Tile-based Wi-Fi/cell database (covers *.ls.apple.com)
        "gs-loc-cn.apple.com",    // China variant of WLOC
        "gsp10-ssl.apple.com",    // Cell/Wi-Fi crowd collection
    ]

    override func startTunnel(options: [String: NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        let settings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: "127.0.0.1")

        // Minimal IPv4 config — we don't actually route traffic, just set DNS policy
        let ipv4 = NEIPv4Settings(addresses: ["10.0.0.1"], subnetMasks: ["255.255.255.0"])
        settings.ipv4Settings = ipv4

        // Point DNS at localhost (nothing listening) — queries for matched domains get NXDOMAIN/timeout
        let dns = NEDNSSettings(servers: ["127.0.0.1"])
        dns.matchDomains = Self.blockedDomains
        settings.dnsSettings = dns

        setTunnelNetworkSettings(settings) { error in
            if let error = error {
                NSLog("[ChromaCatchDNS] Failed to set tunnel settings: %@", error.localizedDescription)
            } else {
                NSLog("[ChromaCatchDNS] Tunnel started — blocking %d Apple location domains",
                      Self.blockedDomains.count)
            }
            completionHandler(error)
        }
    }

    override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        NSLog("[ChromaCatchDNS] Tunnel stopped (reason: %d)", reason.rawValue)
        completionHandler()
    }

    override func handleAppMessage(_ messageData: Data, completionHandler: ((Data?) -> Void)?) {
        // Future: could accept updated domain lists from main app via App Group IPC
        completionHandler?(nil)
    }
}

import CoreLocation
import Foundation

/// Lightweight CLLocationManager wrapper whose sole purpose is keeping the app
/// alive in the background.
///
/// iOS suspends apps ~10 seconds after entering background. By requesting
/// continuous location updates at the lowest accuracy (`kCLLocationAccuracyThreeKilometers`),
/// we keep the process running so that critical timers continue to fire:
/// - DongleController's 1 Hz NMEA loop (BLE writes to GPS dongle)
/// - LocationMonitor's 5 s GPS verification poll
/// - WebSocket heartbeats and status reporting
///
/// This manager does NOT use the location data for anything — it exists purely
/// as a background execution keepalive. The actual GPS verification is handled
/// by `LocationMonitor`.
class LocationKeepAlive: NSObject, CLLocationManagerDelegate {
    private var manager: CLLocationManager?
    private let log: (String) -> Void

    init(log: @escaping (String) -> Void = { NSLog("[LocationKeepAlive] %@", $0) }) {
        self.log = log
        super.init()
    }

    /// Request "Always" authorization and start background location updates.
    func start() {
        guard manager == nil else { return }

        let mgr = CLLocationManager()
        mgr.delegate = self
        mgr.desiredAccuracy = kCLLocationAccuracyThreeKilometers
        mgr.allowsBackgroundLocationUpdates = true
        mgr.pausesLocationUpdatesAutomatically = false
        mgr.showsBackgroundLocationIndicator = true

        let status = mgr.authorizationStatus
        if status == .notDetermined {
            mgr.requestAlwaysAuthorization()
        } else if status == .authorizedWhenInUse {
            // Prompt upgrade to Always (iOS shows this automatically on second ask)
            mgr.requestAlwaysAuthorization()
        }

        mgr.startUpdatingLocation()
        manager = mgr
        log("Background keepalive started (3km accuracy)")
    }

    /// Stop background location updates.
    func stop() {
        manager?.stopUpdatingLocation()
        manager?.delegate = nil
        manager = nil
        log("Background keepalive stopped")
    }

    // MARK: - CLLocationManagerDelegate

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        // Intentionally empty — we don't use this data.
        // The updates exist solely to keep the app alive in background.
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        if let clError = error as? CLError, clError.code == .locationUnknown {
            return
        }
        log("Keepalive location error: \(error.localizedDescription)")
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        log("Keepalive auth changed: \(status.rawValue)")

        switch status {
        case .authorizedAlways:
            log("Always authorization granted — background keepalive fully active")
        case .authorizedWhenInUse:
            log("Warning: Only WhenInUse — background keepalive will not persist. Grant 'Always' in Settings.")
        case .denied, .restricted:
            log("Warning: Location denied — background keepalive inactive")
        default:
            break
        }
    }
}

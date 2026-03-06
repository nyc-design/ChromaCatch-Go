import Foundation

/// Generates NMEA 0183 sentences (RMC + GGA) matching the iTools dongle protocol.
///
/// The dongle expects:
/// - RMC with `+++` prefix: `+++$GPRMC,...*XX\r\n`
/// - GGA with ` ---\r\n` suffix: `$GPGGA,...*XX ---\r\n`
struct NMEAGenerator {
    /// Build an RMC sentence with the iTools +++ prefix.
    func makeRMC(lat: Double, lon: Double, speed: Double = 4.7,
                 heading: Double = 0.0, date: Date = Date()) -> String {
        let timeStr = formatTime(date)
        let dateStr = formatDate(date)
        let (latStr, latHemi) = decimalToNMEA(lat, isLon: false)
        let (lonStr, lonHemi) = decimalToNMEA(lon, isLon: true)

        let body = "GPRMC,\(timeStr),A,\(latStr),\(latHemi),\(lonStr),\(lonHemi)," +
            "\(String(format: "%.1f", speed)),\(String(format: "%.0f", heading)),\(dateStr),,,A"
        let checksum = nmeaChecksum(body)
        return "+++$\(body)*\(checksum)\r\n"
    }

    /// Build a GGA sentence with the iTools ` ---\r\n` suffix.
    func makeGGA(lat: Double, lon: Double, altitude: Double = 2067.3,
                 date: Date = Date()) -> String {
        let timeStr = formatTime(date)
        let (latStr, latHemi) = decimalToNMEA(lat, isLon: false)
        let (lonStr, lonHemi) = decimalToNMEA(lon, isLon: true)

        let body = "GPGGA,\(timeStr),\(latStr),\(latHemi),\(lonStr),\(lonHemi)," +
            "1,06,2.100000,\(String(format: "%.1f", altitude)),M,23.5,M,,"
        let checksum = nmeaChecksum(body)
        return "$\(body)*\(checksum) ---\r\n"
    }

    /// Build both RMC + GGA as a pair, ready for BLE transmission.
    func makePair(lat: Double, lon: Double, altitude: Double = 2067.3,
                  speed: Double = 4.7, heading: Double = 0.0) -> (rmc: String, gga: String) {
        let now = Date()
        let rmc = makeRMC(lat: lat, lon: lon, speed: speed, heading: heading, date: now)
        let gga = makeGGA(lat: lat, lon: lon, altitude: altitude, date: now)
        return (rmc, gga)
    }

    // MARK: - Private Helpers

    /// Convert decimal degrees to NMEA format: "DDMM.MMMMMM" + hemisphere.
    func decimalToNMEA(_ decimal: Double, isLon: Bool) -> (String, String) {
        let hemisphere: String
        if isLon {
            hemisphere = decimal >= 0 ? "E" : "W"
        } else {
            hemisphere = decimal >= 0 ? "N" : "S"
        }
        let abs = Swift.abs(decimal)
        let degrees = Int(abs)
        let minutes = (abs - Double(degrees)) * 60.0

        if isLon {
            return (String(format: "%03d%09.6f", degrees, minutes), hemisphere)
        } else {
            return (String(format: "%02d%09.6f", degrees, minutes), hemisphere)
        }
    }

    /// XOR checksum of characters between $ and * (exclusive).
    func nmeaChecksum(_ sentence: String) -> String {
        var checksum: UInt8 = 0
        for byte in sentence.utf8 {
            checksum ^= byte
        }
        return String(format: "%02X", checksum)
    }

    private func formatTime(_ date: Date) -> String {
        let cal = Calendar(identifier: .gregorian)
        let comps = cal.dateComponents(in: TimeZone(identifier: "UTC")!, from: date)
        return String(format: "%02d%02d%02d.47", comps.hour!, comps.minute!, comps.second!)
    }

    private func formatDate(_ date: Date) -> String {
        let cal = Calendar(identifier: .gregorian)
        let comps = cal.dateComponents(in: TimeZone(identifier: "UTC")!, from: date)
        return String(format: "%02d%02d%02d", comps.day!, comps.month!, comps.year! % 100)
    }
}

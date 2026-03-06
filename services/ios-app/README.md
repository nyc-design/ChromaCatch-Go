# ChromaCatchLocationControl (iOS)

This directory contains a dedicated iOS app package for **location spoofing only**.

- Project: `ChromaCatchLocationControl/ChromaCatchLocationControl.xcodeproj`
- Main app source: `ChromaCatchLocationControl/ChromaCatchLocationControl/`
- DNS extension: `ChromaCatchLocationControl/ChromaCatchDNS/`

The app UI is intentionally scoped to:
- Spoof controls (dongle scan/connect, coordinate updates, location guard)
- Settings for location service URL / API key / client ID

This package is isolated so it can be moved to the `chromacatch-go` repo.

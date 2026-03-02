import ReplayKit
import UIKit

/// ReplayKit Broadcast Upload Extension sample handler.
/// Captures the iPhone screen and sends H.264 frames to the ChromaCatch backend
/// using the same h264-ws protocol as the CLI airplay-client tool.
///
/// Runs in a separate process with a 50MB memory limit.
/// Configuration is read from App Group UserDefaults (shared with main app).
class SampleHandler: RPBroadcastSampleHandler {
    private var encoder: H264Encoder?
    private var wsClient: BroadcastWSClient?

    override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
        // Read configuration from App Group shared defaults
        let defaults = UserDefaults(suiteName: "group.com.chromacatch")
        let backendURL = defaults?.string(forKey: "backendURL") ?? "wss://localhost:8000/ws/client"
        let apiKey = defaults?.string(forKey: "apiKey") ?? ""
        let clientId = defaults?.string(forKey: "clientId") ?? "ios-broadcast"

        // Detect actual screen dimensions (portrait-native on iPhone)
        let screenSize = UIScreen.main.nativeBounds.size
        let (encWidth, encHeight) = H264Encoder.scaledDimensions(
            screenWidth: Int(screenSize.width),
            screenHeight: Int(screenSize.height),
            maxDimension: 1280
        )
        NSLog("[SampleHandler] Screen native: %.0fx%.0f → Encode: %dx%d",
              screenSize.width, screenSize.height, encWidth, encHeight)

        // Initialize H.264 encoder at detected dimensions
        encoder = H264Encoder(bitrate: 2_000_000, keyframeInterval: 60)

        // Initialize WebSocket client for /ws/client (frame channel)
        guard let url = URL(string: backendURL.replacingOccurrences(of: "/ws/control", with: "/ws/client")) else {
            finishBroadcastWithError(NSError(domain: "ChromaCatch", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Invalid backend URL"]))
            return
        }

        wsClient = BroadcastWSClient(url: url, apiKey: apiKey, clientId: clientId)
        wsClient?.connect()

        // Wire encoder output to WebSocket
        encoder?.onEncodedAU = { [weak self] data, isKeyframe, captureTimestamp in
            self?.wsClient?.sendH264AU(data, isKeyframe: isKeyframe, captureTimestamp: captureTimestamp)
        }

        guard encoder?.start(width: encWidth, height: encHeight) == true else {
            finishBroadcastWithError(NSError(domain: "ChromaCatch", code: 2,
                userInfo: [NSLocalizedDescriptionKey: "Failed to start H.264 encoder at \(encWidth)x\(encHeight)"]))
            return
        }
    }

    override func broadcastPaused() {
        // No action needed — ReplayKit pauses sample delivery automatically
    }

    override func broadcastResumed() {
        // No action needed — samples resume automatically
    }

    override func broadcastFinished() {
        NSLog("[SampleHandler] broadcastFinished — stopping encoder and disconnecting WS")
        encoder?.stop()
        wsClient?.disconnect()
        encoder = nil
        wsClient = nil
    }

    override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with sampleBufferType: RPSampleBufferType) {
        switch sampleBufferType {
        case .video:
            encoder?.encode(sampleBuffer)

        case .audioApp:
            // Future: extract PCM from sampleBuffer and send via wsClient.sendAudioChunk()
            break

        case .audioMic:
            // We don't capture microphone audio
            break

        @unknown default:
            break
        }
    }
}

import CoreMedia
import Foundation
import VideoToolbox

/// Hardware H.264 encoder using VideoToolbox.
/// Converts CMSampleBuffers from ReplayKit into Annex-B format H.264 AUs
/// matching the backend's h264-ws protocol expectations.
class H264Encoder {
    private var session: VTCompressionSession?
    private var frameCount: Int = 0

    /// Called when an encoded Access Unit is ready.
    /// Parameters: (annexBData, isKeyframe, captureTimestamp)
    var onEncodedAU: ((Data, Bool, Double) -> Void)?

    private let bitrate: Int32
    private let keyframeInterval: Int32

    init(bitrate: Int32 = 2_000_000, keyframeInterval: Int32 = 60) {
        self.bitrate = bitrate
        self.keyframeInterval = keyframeInterval
    }

    /// Create the compression session at the given dimensions.
    /// Call this once before sending frames to `encode()`.
    func start(width: Int32, height: Int32) -> Bool {
        NSLog("[H264Encoder] Creating session at %dx%d", width, height)

        var status = VTCompressionSessionCreate(
            allocator: nil,
            width: width,
            height: height,
            codecType: kCMVideoCodecType_H264,
            encoderSpecification: nil,
            imageBufferAttributes: nil,
            compressedDataAllocator: nil,
            outputCallback: compressionOutputCallback,
            refcon: Unmanaged.passUnretained(self).toOpaque(),
            compressionSessionOut: &session
        )

        guard status == noErr, let session = session else {
            NSLog("[H264Encoder] VTCompressionSessionCreate failed: %d", status)
            return false
        }

        // Configure for real-time, low-latency encoding
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_RealTime, value: kCFBooleanTrue)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_ProfileLevel, value: kVTProfileLevel_H264_Baseline_AutoLevel)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_AverageBitRate, value: bitrate as CFNumber)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_MaxKeyFrameInterval, value: keyframeInterval as CFNumber)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_AllowFrameReordering, value: kCFBooleanFalse)

        status = VTCompressionSessionPrepareToEncodeFrames(session)
        if status == noErr {
            NSLog("[H264Encoder] Session ready at %dx%d", width, height)
        } else {
            NSLog("[H264Encoder] PrepareToEncodeFrames failed: %d", status)
        }
        return status == noErr
    }

    func encode(_ sampleBuffer: CMSampleBuffer) {
        guard let session = session,
              let imageBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }

        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        let duration = CMSampleBufferGetDuration(sampleBuffer)

        VTCompressionSessionEncodeFrame(
            session,
            imageBuffer: imageBuffer,
            presentationTimeStamp: pts,
            duration: duration,
            frameProperties: nil,
            sourceFrameRefcon: nil,
            infoFlagsOut: nil
        )
    }

    func stop() {
        if let session = session {
            VTCompressionSessionCompleteFrames(session, untilPresentationTimeStamp: .invalid)
            VTCompressionSessionInvalidate(session)
        }
        session = nil
    }

    // MARK: - Private

    private func handleEncodedFrame(_ sampleBuffer: CMSampleBuffer) {
        guard let attachments = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[CFString: Any]] else { return }

        let isKeyframe = !(attachments.first?[kCMSampleAttachmentKey_NotSync] as? Bool ?? false)
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        let captureTimestamp = CMTimeGetSeconds(pts)

        guard let formatDesc = CMSampleBufferGetFormatDescription(sampleBuffer),
              let dataBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }

        var annexBData = Data()

        // On keyframes, prepend SPS + PPS (Sequence/Picture Parameter Sets)
        if isKeyframe {
            annexBData.append(contentsOf: extractParameterSets(from: formatDesc))
        }

        // Convert AVCC (length-prefixed) to Annex-B (start code prefixed)
        var totalLength = 0
        var dataPointer: UnsafeMutablePointer<Int8>?
        CMBlockBufferGetDataPointer(dataBuffer, atOffset: 0, lengthAtOffsetOut: nil,
                                     totalLengthOut: &totalLength, dataPointerOut: &dataPointer)

        guard let ptr = dataPointer else { return }
        var offset = 0
        let startCode: [UInt8] = [0x00, 0x00, 0x00, 0x01]

        while offset < totalLength - 4 {
            // Read 4-byte AVCC length prefix
            var naluLength: UInt32 = 0
            memcpy(&naluLength, ptr + offset, 4)
            naluLength = naluLength.bigEndian
            offset += 4

            guard offset + Int(naluLength) <= totalLength else { break }

            // Replace length prefix with Annex-B start code
            annexBData.append(contentsOf: startCode)
            annexBData.append(Data(bytes: ptr + offset, count: Int(naluLength)))
            offset += Int(naluLength)
        }

        frameCount += 1
        if frameCount <= 3 || frameCount % 300 == 0 {
            NSLog("[H264Encoder] encoded frame #%d, keyframe=%@, size=%d bytes",
                  frameCount, isKeyframe ? "YES" : "no", annexBData.count)
        }
        onEncodedAU?(annexBData, isKeyframe, captureTimestamp)
    }

    fileprivate func handleEncodedOutput(status: OSStatus, sampleBuffer: CMSampleBuffer?) {
        guard status == noErr, let sampleBuffer = sampleBuffer else { return }
        handleEncodedFrame(sampleBuffer)
    }

    private func extractParameterSets(from formatDesc: CMFormatDescription) -> Data {
        var data = Data()
        let startCode: [UInt8] = [0x00, 0x00, 0x00, 0x01]

        // SPS
        var spsSize: Int = 0
        var spsCount: Int = 0
        var spsPointer: UnsafePointer<UInt8>?
        if CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
            formatDesc, parameterSetIndex: 0,
            parameterSetPointerOut: &spsPointer, parameterSetSizeOut: &spsSize,
            parameterSetCountOut: &spsCount, nalUnitHeaderLengthOut: nil
        ) == noErr, let sps = spsPointer {
            data.append(contentsOf: startCode)
            data.append(sps, count: spsSize)
        }

        // PPS
        var ppsSize: Int = 0
        var ppsPointer: UnsafePointer<UInt8>?
        if CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
            formatDesc, parameterSetIndex: 1,
            parameterSetPointerOut: &ppsPointer, parameterSetSizeOut: &ppsSize,
            parameterSetCountOut: nil, nalUnitHeaderLengthOut: nil
        ) == noErr, let pps = ppsPointer {
            data.append(contentsOf: startCode)
            data.append(pps, count: ppsSize)
        }

        return data
    }

    /// Calculate encode dimensions that preserve aspect ratio within a max dimension.
    static func scaledDimensions(screenWidth: Int, screenHeight: Int, maxDimension: Int = 1280) -> (Int32, Int32) {
        let longEdge = max(screenWidth, screenHeight)
        if longEdge <= maxDimension {
            return (Int32(screenWidth & ~1), Int32(screenHeight & ~1))
        }
        let scale = Double(maxDimension) / Double(longEdge)
        let w = Int(Double(screenWidth) * scale) & ~1
        let h = Int(Double(screenHeight) * scale) & ~1
        return (Int32(w), Int32(h))
    }
}

/// C-function callback for VTCompressionSessionCreate (iOS 16 compatible).
private func compressionOutputCallback(
    outputCallbackRefCon: UnsafeMutableRawPointer?,
    sourceFrameRefCon: UnsafeMutableRawPointer?,
    status: OSStatus,
    infoFlags: VTEncodeInfoFlags,
    sampleBuffer: CMSampleBuffer?
) {
    guard let refCon = outputCallbackRefCon else { return }
    let encoder = Unmanaged<H264Encoder>.fromOpaque(refCon).takeUnretainedValue()
    encoder.handleEncodedOutput(status: status, sampleBuffer: sampleBuffer)
}

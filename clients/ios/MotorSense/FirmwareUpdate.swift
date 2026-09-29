import Foundation
import CryptoKit

nonisolated struct FirmwareImage: Sendable {
    let data: Data
    let filename: String
    let version: String
    let sha256: String

    init(data: Data, filename: String) throws {
        guard filename.lowercased().hasSuffix(".bin"), (288...0xf0000).contains(data.count),
              data[0] == 0xe9, data[12] == 9, data[13] == 0,
              Array(data[32..<36]) == [0x32, 0x54, 0xcd, 0xab] else {
            throw FirmwareError.invalid("Select a MotorSense ESP32-S3 application .bin (up to 960 KiB), not a bootloader or merged flash image.")
        }
        func string(at offset: Int) -> String? {
            let bytes = data[offset..<(offset + 32)]
            guard let end = bytes.firstIndex(of: 0) else { return nil }
            return String(data: data[offset..<end], encoding: .utf8)
        }
        guard string(at: 80) == "motorsense", let version = string(at: 48), !version.isEmpty else {
            throw FirmwareError.invalid("This file does not identify itself as MotorSense firmware.")
        }
        self.data = data
        self.filename = filename
        self.version = version
        sha256 = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

nonisolated enum FirmwareError: LocalizedError {
    case invalid(String)
    var errorDescription: String? {
        switch self { case .invalid(let message): return message }
    }
}

nonisolated struct FirmwareUpdateStatus: Sendable {
    var axis: AxisName?
    var message = "Choose a firmware file and controller."
    var acknowledged = 0
    var total = 0
    var active = false
    var canCancel = false
    var failed = false
    var progress: Double { total == 0 ? 0 : Double(acknowledged) / Double(total) }
}

/// Binary OTA data advances on cumulative application acknowledgements, not ATT writes.
nonisolated final class FirmwareTransfer {
    enum Phase { case info, abort, begin, data, end, reboot, verify, complete }
    let image: FirmwareImage
    let axis: AxisName
    private(set) var phase: Phase = .info
    private(set) var acknowledged = 0
    private var nextOffset = 0
    init(image: FirmwareImage, axis: AxisName) { self.image = image; self.axis = axis }

    var command: String? {
        switch phase {
        case .info, .verify: return "DEVICE INFO"
        case .abort: return "OTA ABORT"
        case .begin: return "OTA BIN BEGIN \(image.data.count) \(image.sha256)"
        case .data: return nil
        case .end: return "OTA END"
        case .reboot, .complete: return nil
        }
    }
    var isSendingData: Bool { phase == .data }
    /// Build up to eight MTU-sized frames; the last frame requests one cumulative ACK.
    func dataFrames(maxPayload: Int) -> [Data] {
        guard phase == .data, maxPayload > 0 else { return [] }
        let framePayload = min(500, maxPayload - 9)
        guard framePayload > 0 else { return [] }
        let windowEnd = min(acknowledged + framePayload * 8, image.data.count)
        var frames: [Data] = []
        var offset = acknowledged
        while offset < windowEnd {
            let end = min(offset + framePayload, windowEnd)
            let count = end - offset
            var frame = Data([0x4d, 0x53])
            frame.append(contentsOf: UInt32(offset).littleEndianBytes)
            frame.append(contentsOf: UInt16(count).littleEndianBytes)
            frame.append(end == windowEnd ? 1 : 0)
            frame.append(image.data[offset..<end])
            frames.append(frame)
            offset = end
        }
        nextOffset = windowEnd
        return frames
    }
    var timeout: TimeInterval { phase == .begin ? 120 : 30 }
    var message: String {
        switch phase {
        case .info: return "Checking controller identity…"
        case .abort: return "Preparing update…"
        case .begin: return "Preparing inactive firmware slot…"
        case .data: return "Uploading to \(axis.title)…"
        case .end: return "Verifying firmware on controller…"
        case .reboot: return "Uploaded. Waiting for reboot and boot checks…"
        case .verify: return "Reconnected. Checking firmware version…"
        case .complete: return "\(axis.title) reports firmware \(image.version). Re-zero the axis before moving."
        }
    }
    /// Returns true only for the expected complete response; unrelated telemetry is ignored.
    func receive(_ line: String) throws -> Bool {
        if line.hasPrefix("ERR") {
            throw FirmwareError.invalid("Controller: \(line). OTA requires the unified firmware and initial wired migration.")
        }
        switch phase {
        case .info, .verify:
            guard line.hasPrefix("DEVICE ") else { return false }
            let fields = Dictionary(line.split(separator: " ").dropFirst().compactMap { token -> (String, String)? in
                let parts = token.split(separator: "=", maxSplits: 1)
                return parts.count == 2 ? (String(parts[0]), String(parts[1])) : nil
            }, uniquingKeysWith: { _, last in last })
            guard fields["role"] == axis.rawValue.uppercased(), fields["project"] == "motorsense" else {
                throw FirmwareError.invalid("The connected controller does not report the selected MotorSense role.")
            }
            if phase == .verify {
                guard fields["firmware"] == image.version else {
                    throw FirmwareError.invalid("Controller reports \(fields["firmware"] ?? "unknown"), expected \(image.version). The update may have rolled back.")
                }
                phase = .complete
            } else { phase = .abort }
        case .abort:
            guard line == "OK OTA ABORT" else { return false }; phase = .begin
        case .begin:
            guard line == "OK OTA BIN BEGIN" else { return false }; phase = .data
        case .data:
            guard line.hasPrefix("OK OTA BIN ") else { return false }
            guard line == "OK OTA BIN \(nextOffset)", nextOffset > acknowledged else {
                throw FirmwareError.invalid("Unexpected firmware offset. Reconnect and restart the upload.")
            }
            acknowledged = nextOffset
            phase = acknowledged == image.data.count ? .end : .data
        case .end:
            guard line == "OK OTA REBOOT" else { return false }; phase = .reboot
        case .reboot, .complete: return false
        }
        return true
    }
    func reconnecting() { phase = .verify }
}

nonisolated private extension UInt16 {
    var littleEndianBytes: [UInt8] { [UInt8(truncatingIfNeeded: self), UInt8(truncatingIfNeeded: self >> 8)] }
}

nonisolated private extension UInt32 {
    var littleEndianBytes: [UInt8] {
        [UInt8(truncatingIfNeeded: self), UInt8(truncatingIfNeeded: self >> 8),
         UInt8(truncatingIfNeeded: self >> 16), UInt8(truncatingIfNeeded: self >> 24)]
    }
}

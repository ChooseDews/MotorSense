@preconcurrency import CoreBluetooth
import Foundation

nonisolated enum AxisName: String, CaseIterable, Sendable {
    case yaw
    case pitch

    var title: String {
        rawValue.capitalized
    }

    var deviceSuffix: String {
        switch self {
        case .yaw: return "-78"
        case .pitch: return "-158"
        }
    }
}

nonisolated enum NUS {
    static var service: CBUUID { CBUUID(string: "6E400001-B5A3-F393-E0A9-E50E24DCCA9E") }
    static var rx: CBUUID { CBUUID(string: "6E400002-B5A3-F393-E0A9-E50E24DCCA9E") }
    static var tx: CBUUID { CBUUID(string: "6E400003-B5A3-F393-E0A9-E50E24DCCA9E") }
}

nonisolated func wrapDegrees(_ value: Double) -> Double {
    modulo(value, 360)
}

nonisolated func shortestDelta(current: Double, target: Double) -> Double {
    modulo(target - current + 180, 360) - 180
}

nonisolated func modulo(_ value: Double, _ modulus: Double) -> Double {
    var result = value.truncatingRemainder(dividingBy: modulus)
    if result < 0 { result += modulus }
    return result
}

nonisolated struct AxisCalibration: Equatable, Sendable {
    var degreesPerTick: Double
    var referenceTicks: Int = 0
    var referenceDegrees: Double = 0
    var maxDuty: Int = 80

    static let yaw = AxisCalibration(degreesPerTick: 360.0 / 11840.0)
    static let pitch = AxisCalibration(degreesPerTick: 360.0 / 11840.0)

    func angle(fromTicks ticks: Int) -> Double {
        referenceDegrees + Double(ticks - referenceTicks) * degreesPerTick
    }
}

nonisolated enum MountError: LocalizedError, Sendable {
    case bluetoothOff
    case bluetoothUnauthorized
    case bluetoothUnavailable
    case missingController(AxisName)
    case notConnected(AxisName)
    case bothAxesRequired
    case timedOut(String)
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .bluetoothOff:
            return "Bluetooth is off"
        case .bluetoothUnauthorized:
            return "Bluetooth access is not allowed"
        case .bluetoothUnavailable:
            return "Bluetooth is unavailable"
        case .missingController(let axis):
            return "Could not find MotorSense \(axis.rawValue) controller (\(axis.deviceSuffix))"
        case .notConnected(let axis):
            return "\(axis.rawValue) BLE is not connected"
        case .bothAxesRequired:
            return "Both axes must be connected"
        case .timedOut(let message), .failed(let message):
            return message
        }
    }
}

nonisolated struct LogLine: Identifiable, Equatable, Sendable {
    let id: UUID
    let text: String

    init(_ text: String) {
        id = UUID()
        self.text = text
    }
}

/// One line of raw BLE NUS traffic with a controller.
nonisolated struct TrafficEntry: Identifiable, Equatable, Sendable {
    let id: UUID
    let date: Date
    let axis: AxisName?
    let sent: Bool
    let text: String

    init(axis: AxisName?, sent: Bool, text: String) {
        id = UUID()
        date = Date()
        self.axis = axis
        self.sent = sent
        self.text = text
    }

    /// Poll replies that repeat several times per second; hideable.
    var isAxisUpdate: Bool {
        if sent { return text == "AXIS STATUS" }
        return text.hasPrefix("ENC ") || text.hasPrefix("AXIS: ")
    }
}

nonisolated struct AxisSnapshot: Equatable, Sendable {
    var name: AxisName
    var connected = false
    var connecting = false
    var healthy = false
    var ticks = 0
    var angle = 0.0
    var motionState = "IDLE"
    var duty = 0
    var error: String?
    var deviceName: String?
}

nonisolated enum MountEvent: Sendable {
    case firmware(FirmwareUpdateStatus)
    case bluetooth(CBManagerState)
    case log(String)
    case connecting(AxisName)
    case connected(AxisName, String)
    case disconnected(AxisName)
    case encoder(AxisName, Int)
    case status(AxisName, state: String, targetTicks: Int, position: Int, duty: Int, corrections: Int)
    case axisError(AxisName, String)
    case command(String)
    case traffic(axis: AxisName?, sent: Bool, text: String)
}

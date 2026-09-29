import Foundation

@MainActor
final class AxisState {
    let name: AxisName
    var calibration: AxisCalibration
    var connected = false
    var connecting = false
    var ticks = 0
    var angle = 0.0
    var motionState = "IDLE"
    var targetTicks: Int?
    var duty = 0
    var corrections = 0
    var lastNotification = Date.distantPast
    var error: String?
    var deviceName: String?

    init(name: AxisName, calibration: AxisCalibration) {
        self.name = name
        self.calibration = calibration
    }

    func updateTicks(_ ticks: Int) {
        self.ticks = ticks
        angle = calibration.angle(fromTicks: ticks)
        lastNotification = Date()
    }

    func setReference(ticks: Int, degrees: Double) {
        calibration.referenceTicks = ticks
        calibration.referenceDegrees = degrees
        updateTicks(ticks)
    }

    var healthy: Bool {
        connected && error == nil && Date().timeIntervalSince(lastNotification) < 5
    }

    func snapshot() -> AxisSnapshot {
        AxisSnapshot(
            name: name,
            connected: connected,
            connecting: connecting,
            healthy: healthy,
            ticks: ticks,
            angle: angle,
            motionState: motionState,
            duty: duty,
            error: error,
            deviceName: deviceName
        )
    }
}

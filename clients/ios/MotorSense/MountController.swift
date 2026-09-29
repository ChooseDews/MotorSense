import Combine
import CoreBluetooth
import Foundation
import UIKit

@MainActor
final class MountController: ObservableObject {
    @Published private(set) var firmwareUpdate = FirmwareUpdateStatus()
    private var previousIdleTimerDisabled: Bool?
    @Published private(set) var yaw = AxisSnapshot(name: .yaw)
    @Published private(set) var pitch = AxisSnapshot(name: .pitch)
    @Published var targetYaw: Double?
    @Published var targetPitch: Double?
    @Published var lastCommand = "No telescope command sent"
    @Published var logLines: [LogLine] = []
    @Published private(set) var traffic: [TrafficEntry] = []
    @Published var bluetoothState: CBManagerState = .unknown
    @Published var scanning = false
    @Published var latitude = 37.3349
    @Published var longitude = -122.0090
    @Published var elevationM = 0.0
    @Published private(set) var locked = false
    @Published private(set) var tracking = false

    private let yawAxis = AxisState(name: .yaw, calibration: .yaw)
    private let pitchAxis = AxisState(name: .pitch, calibration: .pitch)
    private var ble: BLEMount!
    private var backgroundObserver: AnyCancellable?
    private var healthTimer: Timer?
    private var trackedRA: Double?
    private var trackedDec: Double?
    private var lastTrackCommand = Date.distantPast
    private let trackingDuty = 40

    init() {
        ble = BLEMount { [weak self] event in
            Task { @MainActor in
                self?.handle(event)
            }
        }
        backgroundObserver = NotificationCenter.default.publisher(for: UIApplication.didEnterBackgroundNotification)
            .sink { [weak self] _ in
                Task { @MainActor in
                    guard let self, self.firmwareUpdate.active, self.firmwareUpdate.canCancel else { return }
                    self.cancelFirmwareUpdate()
                }
            }
        healthTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.publish()
                self?.tickTracking()
            }
        }
    }

    func connectFirmwareController(_ axis: AxisName) {
        guard !firmwareUpdate.active else { return }
        scanning = true
        axisState(axis).connecting = true
        publish()
        ble.connectFirmwareController(axis)
    }

    func updateFirmware(_ axis: AxisName, image: FirmwareImage) {
        guard !firmwareUpdate.active, !scanning, axisState(axis).connected else { return }
        setTracking(false)
        setLocked(true)
        firmwareUpdate = FirmwareUpdateStatus(axis: axis, message: "Starting update…", total: image.data.count, active: true, canCancel: true)
        previousIdleTimerDisabled = UIApplication.shared.isIdleTimerDisabled
        UIApplication.shared.isIdleTimerDisabled = true
        ble.updateFirmware(axis, image: image)
    }

    func cancelFirmwareUpdate() { ble.cancelFirmwareUpdate() }

    func connectControllers() {
        guard !firmwareUpdate.active else { return }
        scanning = true
        yawAxis.connecting = true
        pitchAxis.connecting = true
        publish()
        ble.scanAndConnect()
    }

    func home() {
        gotoMount(yaw: 0, pitch: 0)
    }

    func stop() {
        ble.stop()
        if tracking {
            setTracking(false)
        }
    }

    /// Sends AXIS ZERO to one axis, or both when `axis` is nil. Allowed while
    /// locked: the post-update flow zeroes the axis before unlocking. Stops
    /// motion first because the firmware refuses zeroing while the motor runs.
    func zeroAxis(_ axis: AxisName? = nil) {
        guard !firmwareUpdate.active else { return }
        let targets = axis.map { [$0] } ?? AxisName.allCases
        let connected = targets.filter { axisState($0).connected }
        guard !connected.isEmpty else {
            let label = axis?.title ?? "axes"
            log("Zero failed: no \(label) controller connected")
            lastCommand = "Zero failed"
            publish()
            return
        }
        setTracking(false)
        let names = connected.map { $0.title }.joined(separator: " + ")
        for name in connected {
            ble.command(name, "AXIS STOP")
            ble.command(name, "AXIS ZERO")
        }
        targetYaw = nil
        targetPitch = nil
        lastCommand = "Zeroing \(names)"
        log("Zeroing \(names): current position becomes the 0° reference")
        publish()
    }

    /// Establishes the current physical pose as an absolute horizontal
    /// coordinate measured by a phone fixed to the telescope tube.
    func alignToPhone(azimuth: Double, altitude: Double) {
        guard !firmwareUpdate.active, !locked else {
            log("Phone alignment blocked: Telescope Control is locked")
            return
        }
        guard yawAxis.connected, pitchAxis.connected else {
            log("Phone alignment failed: \(MountError.bothAxesRequired.localizedDescription)")
            return
        }
        setTracking(false)
        ble.stop()
        ble.command(.yaw, "AXIS ZERO")
        ble.command(.pitch, "AXIS ZERO")

        let alignedYaw = wrapDegrees(azimuth)
        let alignedPitch = max(-90, min(90, altitude))
        // AXIS ZERO assigns the configured zero index. Current MotorSense
        // controllers use zero_deg=0, represented by tick zero in this client.
        yawAxis.setReference(ticks: 0, degrees: alignedYaw)
        pitchAxis.setReference(ticks: 0, degrees: alignedPitch)
        targetYaw = nil
        targetPitch = nil
        lastCommand = String(format: "Aligned to phone: %.1f° / %.1f°", alignedYaw, alignedPitch)
        log(lastCommand)
        publish()
    }

    func setLocked(_ on: Bool) {
        guard !firmwareUpdate.active else { return }
        guard locked != on else { return }
        if on {
            setTracking(false)
            ble.stop()
            locked = true
            lastCommand = "Telescope Control locked"
            log("Telescope Control locked")
        } else {
            locked = false
            log("Telescope Control unlocked")
        }
    }

    func toggleLocked() {
        setLocked(!locked)
    }

    func setTracking(_ on: Bool) {
        guard !on || !firmwareUpdate.active else { return }
        guard tracking != on else { return }
        if on {
            guard !locked else {
                log("Tracking blocked: Telescope Control is locked")
                return
            }
            guard yawAxis.connected, pitchAxis.connected else {
                log("Tracking failed: \(MountError.bothAxesRequired.localizedDescription)")
                return
            }
            captureTrackedEquatorial(azimuth: yawAxis.angle, altitude: pitchAxis.angle)
            tracking = true
            lastTrackCommand = .distantPast
            lastCommand = "Sidereal tracking on"
            log("Sidereal tracking on")
        } else {
            tracking = false
            trackedRA = nil
            trackedDec = nil
            log("Sidereal tracking off")
        }
    }

    func toggleTracking() {
        setTracking(!tracking)
    }

    func gotoMount(yaw: Double, pitch: Double, duty: Int? = nil, retargetTracking: Bool = true) {
        if locked || firmwareUpdate.active {
            log("Telescope GOTO blocked: Telescope Control is locked")
            return
        }
        guard yawAxis.connected, pitchAxis.connected else {
            log("Telescope GOTO failed: \(MountError.bothAxesRequired.localizedDescription)")
            return
        }
        if tracking, retargetTracking {
            captureTrackedEquatorial(azimuth: yaw, altitude: pitch)
        }
        let yawDelta = shortestDelta(current: yawAxis.angle, target: yaw)
        let pitchDelta = pitch - pitchAxis.angle
        targetYaw = wrapDegrees(yaw)
        targetPitch = pitch
        publish()
        if abs(yawDelta) >= yawAxis.calibration.degreesPerTick / 2 {
            ble.command(.yaw, "AXIS MOVE \(format(yawDelta)) \(duty ?? yawAxis.calibration.maxDuty)")
        }
        if abs(pitchDelta) >= pitchAxis.calibration.degreesPerTick / 2 {
            ble.command(.pitch, "AXIS MOVE \(format(pitchDelta)) \(duty ?? pitchAxis.calibration.maxDuty)")
        }
    }

    func updateLocation(_ fix: GPSFix) {
        latitude = fix.latitude
        longitude = fix.longitude
        elevationM = fix.elevation
    }

    func moveRelative(yawDelta: Double, pitchDelta: Double, duty: Int) {
        gotoMount(
            yaw: wrapDegrees(yawAxis.angle + yawDelta),
            pitch: pitchAxis.angle + pitchDelta,
            duty: duty
        )
    }

    private func handle(_ event: MountEvent) {
        switch event {
        case .firmware(let status):
            firmwareUpdate = status
            if !status.active {
                if let previousIdleTimerDisabled { UIApplication.shared.isIdleTimerDisabled = previousIdleTimerDisabled }
                previousIdleTimerDisabled = nil
                scanning = false
                log(status.message)
            }
        case .bluetooth(let state):
            bluetoothState = state
        case .log(let text):
            if text.hasPrefix("Scanning") || text.hasPrefix("Waiting") {
                scanning = true
            }
            if text.hasPrefix("Controllers connected") || text.hasPrefix("Connect failed") {
                scanning = false
                if text.hasPrefix("Connect failed") {
                    yawAxis.connecting = false
                    pitchAxis.connecting = false
                }
            }
            log(text)
        case .connecting(let axis):
            scanning = false
            axisState(axis).connecting = true
        case .connected(let axis, let name):
            let state = axisState(axis)
            state.connected = true
            state.connecting = false
            state.error = nil
            state.deviceName = name
            log("\(axis.title) connected: \(name)")
        case .disconnected(let axis):
            let state = axisState(axis)
            state.connected = false
            state.connecting = false
            state.error = "Disconnected"
        case .encoder(let axis, let ticks):
            axisState(axis).updateTicks(ticks)
        case .status(let axis, let motion, let targetTicks, let position, let duty, let corrections):
            let state = axisState(axis)
            state.motionState = motion
            state.targetTicks = targetTicks
            state.duty = duty
            state.corrections = corrections
            state.updateTicks(position)
        case .axisError(let axis, let message):
            axisState(axis).error = message
        case .command(let text):
            lastCommand = text
        case .traffic(let axis, let sent, let text):
            appendTraffic(axis: axis, sent: sent, text: text)
        }
        publish()
    }

    /// Raw BLE transcript ring buffer, newest last.
    private func appendTraffic(axis: AxisName?, sent: Bool, text: String) {
        let line = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !line.isEmpty else { return }
        traffic.append(TrafficEntry(axis: axis, sent: sent, text: line))
        if traffic.count > 1000 {
            traffic.removeFirst(traffic.count - 1000)
        }
    }

    private func axisState(_ name: AxisName) -> AxisState {
        name == .yaw ? yawAxis : pitchAxis
    }

    private func log(_ text: String) {
        let line = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !line.isEmpty else { return }
        logLines.append(LogLine(line))
        if logLines.count > 200 {
            logLines.removeFirst(logLines.count - 200)
        }
    }

    private func publish() {
        yaw = yawAxis.snapshot()
        pitch = pitchAxis.snapshot()
    }

    private func format(_ value: Double) -> String {
        String(format: "%.5f", value)
    }

    private func captureTrackedEquatorial(azimuth: Double, altitude: Double) {
        let lst = SkyMath.lstDegrees(date: Date(), longitude: longitude)
        let eq = SkyMath.equatorial(azimuth: azimuth, altitude: altitude, latitude: latitude, lst: lst)
        trackedRA = eq.ra
        trackedDec = eq.dec
    }

    private func tickTracking() {
        guard tracking, !locked else { return }
        guard yawAxis.connected, pitchAxis.connected else {
            setTracking(false)
            return
        }
        guard let ra = trackedRA, let dec = trackedDec else { return }
        let idle = yawAxis.motionState == "IDLE" || yawAxis.motionState == "DONE" || yawAxis.motionState == "CANCELLED"
        let pitchIdle = pitchAxis.motionState == "IDLE" || pitchAxis.motionState == "DONE" || pitchAxis.motionState == "CANCELLED"
        guard idle, pitchIdle else { return }
        guard Date().timeIntervalSince(lastTrackCommand) >= 1 else { return }
        let lst = SkyMath.lstDegrees(date: Date(), longitude: longitude)
        let horiz = SkyMath.horizontal(ra: ra, dec: dec, latitude: latitude, lst: lst)
        if horiz.altitude < -1 {
            setTracking(false)
            log("Sidereal tracking stopped: target below horizon")
            return
        }
        let yawDelta = shortestDelta(current: yawAxis.angle, target: horiz.azimuth)
        let pitchTarget = max(0, min(90, horiz.altitude))
        let pitchDelta = pitchTarget - pitchAxis.angle
        let minMove = min(yawAxis.calibration.degreesPerTick, pitchAxis.calibration.degreesPerTick) * 0.6
        guard abs(yawDelta) >= minMove || abs(pitchDelta) >= minMove else { return }
        lastTrackCommand = Date()
        gotoMount(yaw: horiz.azimuth, pitch: pitchTarget, duty: trackingDuty, retargetTracking: false)
    }
}

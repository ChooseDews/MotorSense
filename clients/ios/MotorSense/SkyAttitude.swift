import CoreMotion
import Combine
import Foundation
import simd

struct SkyLook: Equatable, Sendable {
    var azimuth: Double
    var altitude: Double
    var roll: Double
}

/// Converts Core Motion's true/magnetic-north reference frame into the
/// direction of the top edge of a portrait iPhone. When the phone is mounted
/// flat against the telescope, this is the optical-axis direction.
nonisolated func telescopeLook(from rotation: simd_quatd, gravityY: Double? = nil) -> SkyLook {
    // CMAttitude describes the reference frame in device coordinates. Invert
    // it to express the device's portrait top edge in north/west/up space.
    let top = simd_act(rotation.inverse, SIMD3<Double>(0, 1, 0))
    let length = max(simd_length(top), 1e-12)
    let direction = top / length

    // In xTrueNorthZVertical/xMagneticNorthZVertical, +X is north, +Y is west,
    // and +Z is up. Azimuth increases clockwise from north.
    let azimuth = wrapDegrees(atan2(-direction.y, direction.x) / SkyMath.deg)
    // Gravity is measured in device coordinates and is the most direct,
    // drift-free measure of the top edge's elevation: +Y points toward the
    // top of a portrait phone, while gravity points down.
    let vertical = gravityY.map { -$0 } ?? direction.z
    let altitude = asin(max(-1, min(1, vertical))) / SkyMath.deg
    return SkyLook(azimuth: azimuth, altitude: altitude, roll: 0)
}

@MainActor
final class TelescopeAttitude: ObservableObject {
    @Published private(set) var available = CMMotionManager().isDeviceMotionAvailable
    @Published private(set) var running = false
    @Published private(set) var look: SkyLook?
    @Published private(set) var usesTrueNorth = false
    @Published private(set) var compassAccuracy: CMMagneticFieldCalibrationAccuracy = .uncalibrated
    @Published private(set) var errorMessage: String?

    private let manager = CMMotionManager()
    private let queue: OperationQueue = {
        let queue = OperationQueue()
        queue.name = "dews-solutions.MotorSense.telescope-attitude"
        queue.maxConcurrentOperationCount = 1
        queue.qualityOfService = .userInteractive
        return queue
    }()

    func start() {
        guard manager.isDeviceMotionAvailable else {
            available = false
            errorMessage = "Device motion is unavailable on this iPhone."
            return
        }
        guard !manager.isDeviceMotionActive else { return }
        available = true
        errorMessage = nil
        manager.deviceMotionUpdateInterval = 1.0 / 30.0

        let frames = CMMotionManager.availableAttitudeReferenceFrames()
        let frame: CMAttitudeReferenceFrame
        if frames.contains(.xTrueNorthZVertical) {
            frame = .xTrueNorthZVertical
            usesTrueNorth = true
        } else if frames.contains(.xMagneticNorthZVertical) {
            frame = .xMagneticNorthZVertical
            usesTrueNorth = false
        } else {
            errorMessage = "An absolute compass heading is unavailable."
            return
        }

        manager.startDeviceMotionUpdates(using: frame, to: queue) { [weak self] motion, error in
            Task { @MainActor [weak self] in
                guard let self else { return }
                if let error {
                    self.errorMessage = error.localizedDescription
                    return
                }
                guard let motion else { return }
                let q = motion.attitude.quaternion
                self.compassAccuracy = motion.magneticField.accuracy
                self.look = telescopeLook(
                    from: simd_quatd(ix: q.x, iy: q.y, iz: q.z, r: q.w),
                    gravityY: motion.gravity.y
                )
            }
        }
        running = true
    }

    func stop() {
        manager.stopDeviceMotionUpdates()
        running = false
        look = nil
    }
}

@MainActor
final class SkyAttitude: ObservableObject {
    @Published private(set) var available = CMMotionManager().isDeviceMotionAvailable
    @Published private(set) var running = false
    @Published private(set) var look: SkyLook?
    @Published private(set) var hasAbsoluteHeading = false
    @Published var calibrated = false
    @Published var headingOffset = 0.0

    private let manager = CMMotionManager()
    private let queue = OperationQueue()
    private var pendingHeading: Double?

    init() {
        queue.name = "dews-solutions.MotorSense.sky-attitude"
        queue.maxConcurrentOperationCount = 1
        queue.qualityOfService = .userInteractive
    }

    func start() {
        guard manager.isDeviceMotionAvailable else {
            available = false
            return
        }
        available = true
        guard !manager.isDeviceMotionActive else { return }
        manager.deviceMotionUpdateInterval = 1.0 / 60.0
        let frames = CMMotionManager.availableAttitudeReferenceFrames()
        let referenceFrame: CMAttitudeReferenceFrame = frames.contains(.xTrueNorthZVertical)
            ? .xTrueNorthZVertical
            : (frames.contains(.xMagneticNorthZVertical) ? .xMagneticNorthZVertical : .xArbitraryZVertical)
        hasAbsoluteHeading = referenceFrame != .xArbitraryZVertical
        if hasAbsoluteHeading {
            calibrated = true
        }
        manager.startDeviceMotionUpdates(using: referenceFrame, to: queue) { [weak self] motion, _ in
            guard let motion else { return }
            let attitude = motion.attitude.quaternion
            let rotation = simd_quatd(ix: attitude.x, iy: attitude.y, iz: attitude.z, r: attitude.w)
            Task { @MainActor [weak self] in
                guard let self else { return }
                let rawLook = SkyAttitude.look(from: rotation, offset: 0)
                if let pendingHeading = self.pendingHeading {
                    self.headingOffset = wrapDegrees(pendingHeading - rawLook.azimuth)
                    self.pendingHeading = nil
                    self.calibrated = true
                }
                self.look = SkyLook(
                    azimuth: wrapDegrees(rawLook.azimuth + self.headingOffset),
                    altitude: rawLook.altitude,
                    roll: rawLook.roll
                )
            }
        }
        running = true
    }

    func stop() {
        manager.stopDeviceMotionUpdates()
        running = false
        look = nil
    }

    func alignHeading(to azimuth: Double) {
        guard let sample = latestRotation() else {
            pendingHeading = azimuth
            calibrated = false
            return
        }
        let current = SkyAttitude.look(from: sample, offset: 0)
        headingOffset = wrapDegrees(azimuth - current.azimuth)
        pendingHeading = nil
        calibrated = true
        look = SkyLook(
            azimuth: wrapDegrees(current.azimuth + headingOffset),
            altitude: current.altitude,
            roll: current.roll
        )
    }

    func clearHeading() {
        headingOffset = 0
        calibrated = false
    }

    private func latestRotation() -> simd_quatd? {
        guard let attitude = manager.deviceMotion?.attitude.quaternion else { return nil }
        return simd_quatd(ix: attitude.x, iy: attitude.y, iz: attitude.z, r: attitude.w)
    }

    nonisolated static func look(from rotation: simd_quatd, offset: Double) -> SkyLook {
        let deviceForward = simd_act(rotation, SIMD3<Double>(0, 0, -1))
        let deviceUp = simd_act(rotation, SIMD3<Double>(0, 1, 0))
        let forward = SIMD3(deviceForward.x, deviceForward.z, -deviceForward.y)
        let screenUp = SIMD3(deviceUp.x, deviceUp.z, -deviceUp.y)
        let worldUp = SIMD3<Double>(0, 1, 0)
        let rightVector = simd_cross(forward, worldUp)
        let upVector = worldUp - forward * simd_dot(worldUp, forward)
        let altitude = asin(max(-1, min(1, forward.y))) / SkyMath.deg
        let azimuth = wrapDegrees(atan2(forward.x, -forward.z) / SkyMath.deg + offset)
        let roll: Double
        if simd_length_squared(rightVector) > 1e-8, simd_length_squared(upVector) > 1e-8 {
            let right = simd_normalize(rightVector)
            let horizonUp = simd_normalize(upVector)
            roll = atan2(simd_dot(screenUp, right), simd_dot(screenUp, horizonUp)) / SkyMath.deg
        } else {
            roll = 0
        }
        // Core Motion's portrait-device roll axis is quarter-turned from the
        // SceneKit camera's screen-up convention. Compensate in this path only;
        // ARKit camera transforms already use the scene's camera axes.
        return SkyLook(azimuth: azimuth, altitude: altitude, roll: roll + 90)
    }

    nonisolated static func skyOrientation(from camera: simd_float4x4) -> (forward: SIMD3<Float>, roll: Double) {
        let cameraForward = SIMD3<Float>(-camera.columns.2.x, -camera.columns.2.y, -camera.columns.2.z)
        let cameraUp = SIMD3<Float>(camera.columns.1.x, camera.columns.1.y, camera.columns.1.z)
        let forward = simd_normalize(cameraForward)
        let screenUp = simd_normalize(cameraUp)
        let worldUp = SIMD3<Float>(0, 1, 0)
        let rightVector = simd_cross(forward, worldUp)
        let upVector = worldUp - forward * simd_dot(worldUp, forward)
        let roll: Double
        if simd_length_squared(rightVector) > 1e-8, simd_length_squared(upVector) > 1e-8 {
            let right = simd_normalize(rightVector)
            let horizonUp = simd_normalize(upVector)
            roll = Double(atan2(simd_dot(screenUp, right), simd_dot(screenUp, horizonUp))) / SkyMath.deg
        } else {
            roll = 0
        }
        return (forward, roll)
    }
}

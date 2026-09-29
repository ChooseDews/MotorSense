import Combine
@preconcurrency import SceneKit
import SwiftUI
import UIKit

@MainActor
final class PlanetariumController: ObservableObject {
    let scene = PlanetariumScene()
    @Published var selected: SkyTarget?
    @Published var lookAzimuth = 180.0
    @Published var lookAltitude = 38.0
    @Published var fieldOfView = 72.0
    @Published var visibleOnly = true
    @Published var liveClock = true
    @Published var simulatedDate = Date()
    @Published var showTelescope = true
    @Published var arEnabled = false
    @Published var showCamera = false
    @Published var cameraError: String?
    @Published var visibleCount = 0
    @Published var namedVisibleCount = 0
    private var lookRoll = 0.0

    func apply(latitude: Double, longitude: Double, date: Date, yaw: Double, pitch: Double, settings: AppSettings? = nil) {
        scene.update(
            latitude: latitude,
            longitude: longitude,
            date: date,
            lookAzimuth: lookAzimuth,
            lookAltitude: lookAltitude,
            fieldOfView: fieldOfView,
            lookRoll: lookRoll,
            telescopeYaw: yaw,
            telescopePitch: pitch,
            showTelescope: showTelescope
        )
        if let settings {
            applyDisplay(settings)
        }
        visibleCount = scene.visibleStarCount
        namedVisibleCount = scene.visibleNamedCount
        if let selected {
            self.selected = scene.placed(selected)
        }
    }

    func applyDisplay(_ settings: AppSettings) {
        scene.applyDisplay(
            showStars: settings.showStars,
            showPlanets: settings.showPlanets,
            showSun: settings.showSun,
            showMoon: settings.showMoon,
            showGalaxies: settings.showGalaxies,
            showClusters: settings.showClusters,
            showNebulae: settings.showNebulae,
            showConstellations: settings.showConstellations,
            starSize: settings.starSize,
            planetSize: settings.planetSize,
            showMountains: settings.showMountains,
            showTelescopeLaser: settings.showTelescopeLaser
        )
        visibleCount = scene.visibleStarCount
        namedVisibleCount = scene.visibleNamedCount
    }

    func setLiveClock(_ live: Bool) {
        guard liveClock != live else { return }
        liveClock = live
        if live {
            simulatedDate = Date()
        }
    }

    func select(_ target: SkyTarget?, centerView: Bool = false) {
        let placed = target.map(scene.placed)
        selected = placed
        scene.highlight(placed)
        guard centerView, !arEnabled, let placed else { return }
        lookAzimuth = placed.azimuth
        lookAltitude = max(-4, min(88, placed.altitude))
        scene.look(azimuth: lookAzimuth, altitude: lookAltitude, fieldOfView: fieldOfView)
    }

    func placeTelescopeInFront() {
        scene.placeTelescope(azimuth: lookAzimuth)
    }

    func syncTelescope(yaw: Double, pitch: Double) {
        scene.setTelescopePose(yaw: yaw, pitch: pitch, visible: showTelescope)
    }

    func pan(azimuthDelta: Double, altitudeDelta: Double) {
        lookAzimuth = wrapDegrees(lookAzimuth + azimuthDelta)
        lookAltitude = min(88, max(-8, lookAltitude + altitudeDelta))
        scene.look(azimuth: lookAzimuth, altitude: lookAltitude, fieldOfView: fieldOfView)
    }

    func zoom(_ factor: Double) {
        fieldOfView = min(110, max(18, fieldOfView / factor))
        scene.look(azimuth: lookAzimuth, altitude: lookAltitude, fieldOfView: fieldOfView)
    }

    func lookNorth() {
        lookAzimuth = 0
        lookAltitude = 32
        scene.look(azimuth: lookAzimuth, altitude: lookAltitude, fieldOfView: fieldOfView)
    }

    func lookZenith() {
        lookAltitude = 88
        scene.look(azimuth: lookAzimuth, altitude: lookAltitude, fieldOfView: fieldOfView)
    }

    func applyAttitude(_ look: SkyLook) {
        lookAzimuth = look.azimuth
        lookAltitude = min(89, max(-89, look.altitude))
        lookRoll = look.roll
        scene.look(
            azimuth: lookAzimuth,
            altitude: lookAltitude,
            roll: look.roll,
            fieldOfView: fieldOfView
        )
    }

    func setARBackground(_ enabled: Bool) {
        scene.setARBackground(enabled)
    }

    func clearAROrientation() {
        lookRoll = 0
        scene.look(azimuth: lookAzimuth, altitude: lookAltitude, fieldOfView: fieldOfView)
    }
}

nonisolated final class PlanetariumScene: SCNScene {
    private let catalog = SkyCatalog.shared
    private let skyNode = SCNNode()
    private let starCloudNode = SCNNode()
    private let constellationNode = SCNNode()
    private let planetsNode = SCNNode()
    private let labelsNode = SCNNode()
    private let highlightNode = SCNNode()
    private let pickNode = SCNNode()
    private let horizonNode = SCNNode()
    private let groundNode = SCNNode()
    private let lowerSkyOccluderNode = SCNNode()
    private let mountainNode = SCNNode()
    private let telescopeRoot = SCNNode()
    private let telescopeYawNode = SCNNode()
    private let telescopePitchNode = SCNNode()
    private let telescopeLaserNode = SCNNode()
    private let cameraNode = SCNNode()
    private var starPointElement: SCNGeometryElement?
    private var lastBodies: [SolarBody] = []
    private(set) var visibleStarCount = 0
    private(set) var visibleNamedCount = 0
    private var latitude = 37.3349
    private var longitude = -122.0090
    private var date = Date()
    private var lst = 0.0
    private var showStars = true
    private var showPlanets = true
    private var showSun = true
    private var showMoon = true
    private var showGalaxies = true
    private var showClusters = true
    private var showNebulae = true
    private var starSize = 1.0
    private var planetSize = 1.0
    private var showTelescope = true
    private var showMountains = true
    private var showTelescopeLaser = false
    private var arBackgroundEnabled = false
    private var telescopePlacementAzimuth = 180.0
    private var telescopeYaw = 0.0
    private var telescopePitch = 0.0

    nonisolated override init() {
        super.init()
        background.contents = UIColor(red: 0.012, green: 0.016, blue: 0.03, alpha: 1)
        rootNode.addChildNode(skyNode)
        starCloudNode.addChildNode(buildStarCloud())
        skyNode.addChildNode(starCloudNode)
        constellationNode.addChildNode(buildLines())
        skyNode.addChildNode(constellationNode)
        skyNode.addChildNode(planetsNode)
        skyNode.addChildNode(labelsNode)
        skyNode.addChildNode(highlightNode)
        skyNode.addChildNode(pickNode)
        rootNode.addChildNode(horizonNode)
        horizonNode.addChildNode(telescopeRoot)
        cameraNode.camera = SCNCamera()
        cameraNode.camera?.zNear = 0.01
        cameraNode.camera?.zFar = 40
        cameraNode.camera?.fieldOfView = 72
        cameraNode.camera?.wantsHDR = true
        cameraNode.position = SCNVector3(0, 0.18, 0)
        rootNode.addChildNode(cameraNode)
        let ambient = SCNNode()
        ambient.light = SCNLight()
        ambient.light?.type = .ambient
        ambient.light?.intensity = 180
        ambient.light?.color = UIColor(white: 0.55, alpha: 1)
        rootNode.addChildNode(ambient)
        let key = SCNNode()
        key.light = SCNLight()
        key.light?.type = .omni
        key.light?.intensity = 420
        key.light?.color = UIColor(red: 0.75, green: 0.85, blue: 1.0, alpha: 1)
        key.position = SCNVector3(0.4, 0.8, 0.3)
        rootNode.addChildNode(key)
        buildLabels()
        buildPickProxies()
        buildHorizon()
        buildTelescope()
        horizonNode.addChildNode(telescopeLaserNode)
        placeTelescope(azimuth: 180)
        look(azimuth: 180, altitude: 38, fieldOfView: 72)
    }

    @available(*, unavailable)
    nonisolated required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    var pointOfView: SCNNode { cameraNode }

    func update(
        latitude: Double,
        longitude: Double,
        date: Date,
        lookAzimuth: Double,
        lookAltitude: Double,
        fieldOfView: Double,
        lookRoll: Double,
        telescopeYaw: Double,
        telescopePitch: Double,
        showTelescope: Bool
    ) {
        self.latitude = latitude
        self.longitude = longitude
        self.date = date
        self.showTelescope = showTelescope
        lst = SkyMath.lstDegrees(date: date, longitude: longitude)
        let latR = Float((latitude - 90) * SkyMath.deg)
        let lstR = Float(-lst * SkyMath.deg)
        skyNode.simdOrientation = simd_quatf(angle: latR, axis: SIMD3(1, 0, 0)) * simd_quatf(angle: lstR, axis: SIMD3(0, 1, 0))
        recountVisible()
        updateSolarSystem()
        aimTelescope(yaw: telescopeYaw, pitch: telescopePitch)
        telescopeRoot.isHidden = !showTelescope || arBackgroundEnabled
        updateTelescopeLaser()
        look(azimuth: lookAzimuth, altitude: lookAltitude, roll: lookRoll, fieldOfView: fieldOfView)
    }

    func look(azimuth: Double, altitude: Double, fieldOfView: Double) {
        look(azimuth: azimuth, altitude: altitude, roll: 0, fieldOfView: fieldOfView)
    }

    func look(azimuth: Double, altitude: Double, roll: Double, fieldOfView: Double) {
        cameraNode.camera?.fieldOfView = CGFloat(fieldOfView)
        let eye = SIMD3<Float>(0, 0.18, 0)
        let dir = SkyMath.lookDirection(azimuth: azimuth, altitude: altitude)
        let worldUp = SIMD3<Float>(0, 1, 0)
        let projectedUp = worldUp - dir * simd_dot(worldUp, dir)
        let baseUp = simd_length_squared(projectedUp) > 0.0001
            ? simd_normalize(projectedUp)
            : SIMD3<Float>(0, 0, dir.y > 0 ? 1 : -1)
        let right = simd_normalize(simd_cross(dir, baseUp))
        let rollRadians = Float(roll * SkyMath.deg)
        let up = baseUp * cos(rollRadians) + right * sin(rollRadians)
        cameraNode.simdPosition = eye
        cameraNode.look(at: SCNVector3(eye + dir), up: SCNVector3(up), localFront: SCNVector3(0, 0, -1))
    }

    func setARBackground(_ enabled: Bool) {
        arBackgroundEnabled = enabled
        if enabled {
            background.contents = UIColor.clear
        } else {
            background.contents = UIColor(red: 0.012, green: 0.016, blue: 0.03, alpha: 1)
        }
        groundNode.geometry?.firstMaterial?.colorBufferWriteMask = enabled ? [] : .all
        for node in horizonNode.childNodes where node !== groundNode {
            node.isHidden = enabled
                || (node === telescopeRoot && !showTelescope)
                || (node === mountainNode && !showMountains)
                || (node === telescopeLaserNode && (!showTelescopeLaser || !showTelescope))
        }
    }

    func placeTelescope(azimuth: Double) {
        telescopePlacementAzimuth = azimuth
        let az = azimuth * SkyMath.deg
        let dist: Float = 0.72
        telescopeRoot.position = SCNVector3(Float(sin(az)) * dist, 0, Float(-cos(az)) * dist)
        telescopeRoot.eulerAngles = SCNVector3(0, Float(-az), 0)
        applyTelescopeOrientation()
        updateTelescopeLaser()
    }

    func setTelescopePose(yaw: Double, pitch: Double, visible: Bool) {
        aimTelescope(yaw: yaw, pitch: pitch)
        showTelescope = visible
        telescopeRoot.isHidden = !visible || arBackgroundEnabled
        updateTelescopeLaser()
    }

    func setConstellationsVisible(_ visible: Bool) {
        constellationNode.isHidden = !visible
        for node in labelsNode.childNodes where node.name?.hasPrefix("const-") == true {
            node.isHidden = !visible
        }
    }

    func applyDisplay(
        showStars: Bool,
        showPlanets: Bool,
        showSun: Bool,
        showMoon: Bool,
        showGalaxies: Bool,
        showClusters: Bool,
        showNebulae: Bool,
        showConstellations: Bool,
        starSize: Double,
        planetSize: Double,
        showMountains: Bool,
        showTelescopeLaser: Bool
    ) {
        self.showStars = showStars
        self.showPlanets = showPlanets
        self.showSun = showSun
        self.showMoon = showMoon
        self.showGalaxies = showGalaxies
        self.showClusters = showClusters
        self.showNebulae = showNebulae
        self.starSize = min(2.5, max(0.35, starSize))
        self.planetSize = min(2.5, max(0.35, planetSize))
        self.showMountains = showMountains
        self.showTelescopeLaser = showTelescopeLaser
        mountainNode.isHidden = !showMountains || arBackgroundEnabled
        updateTelescopeLaser()
        starCloudNode.isHidden = !showStars
        applyStarSize()
        setConstellationsVisible(showConstellations)
        applyCatalogVisibility()
        updateSolarSystem()
        recountVisible()
    }

    func highlight(_ target: SkyTarget?) {
        highlightNode.childNodes.forEach { $0.removeFromParentNode() }
        guard let target else { return }
        let position = SCNVector3(SkyMath.equatorialPosition(ra: target.ra, dec: target.dec))
        let haloRadius: CGFloat = target.kind == .sun ? 0.06 : (target.kind == .sky ? 0.02 : 0.032)
        let halo = SCNNode(geometry: SCNSphere(radius: haloRadius))
        halo.geometry?.firstMaterial?.diffuse.contents = UIColor(red: 0.45, green: 0.82, blue: 0.98, alpha: 0.18)
        halo.geometry?.firstMaterial?.emission.contents = UIColor(red: 0.45, green: 0.82, blue: 0.98, alpha: 0.55)
        halo.geometry?.firstMaterial?.lightingModel = .constant
        halo.geometry?.firstMaterial?.writesToDepthBuffer = false
        halo.position = position
        highlightNode.addChildNode(halo)
        let ring = SCNNode(geometry: SCNTorus(ringRadius: target.kind == .sky ? 0.028 : 0.045, pipeRadius: 0.0024))
        ring.geometry?.firstMaterial?.diffuse.contents = UIColor(red: 0.45, green: 0.82, blue: 0.98, alpha: 1)
        ring.geometry?.firstMaterial?.emission.contents = UIColor(red: 0.45, green: 0.82, blue: 0.98, alpha: 1)
        ring.geometry?.firstMaterial?.lightingModel = .constant
        ring.position = position
        ring.constraints = [SCNBillboardConstraint()]
        highlightNode.addChildNode(ring)
    }

    func placed(_ target: SkyTarget) -> SkyTarget {
        target.placed(latitude: latitude, lst: lst)
    }

    @MainActor
    func pick(at point: CGPoint, in view: SCNView, allowSkyPoint: Bool) -> SkyTarget? {
        if let hit = pickFromHits(at: point, in: view) {
            return hit
        }
        let eq = equatorial(at: point, in: view)
        let maxDist = allowSkyPoint ? 1.6 : 4.2
        if let nearest = nearest(to: eq, maxDistance: maxDist) {
            return nearest
        }
        return allowSkyPoint ? makeSkyPoint(eq) : nil
    }

    @MainActor
    private func pickFromHits(at point: CGPoint, in view: SCNView) -> SkyTarget? {
        let hits = view.hitTest(point, options: [
            .searchMode: SCNHitTestSearchMode.all.rawValue,
            .boundingBoxOnly: true,
            .categoryBitMask: NSNumber(value: 2),
        ])
        var best: (target: SkyTarget, score: Float)?
        for hit in hits {
            guard let name = hit.node.name else { continue }
            let score = hit.worldCoordinates.distance(to: cameraNode.worldPosition) + hit.screenDistance(from: point, in: view)
            if let body = lastBodies.first(where: { $0.id == name }), shows(body) {
                if best == nil || score < best!.score {
                    best = (makeBodyTarget(body), score)
                }
            } else if name.hasPrefix("hip-"), showStars, let hip = Int(name.dropFirst(4)), let star = catalog.stars.first(where: { $0.hip == hip }) {
                if best == nil || score < best!.score {
                    best = (makeStarTarget(star), score)
                }
            } else if let dso = catalog.dsos.first(where: { $0.id == name }), shows(layer: dso.layer) {
                if best == nil || score < best!.score {
                    best = (makeDSOTarget(dso), score)
                }
            }
        }
        return best?.target
    }

    @MainActor
    private func equatorial(at point: CGPoint, in view: SCNView) -> Equatorial {
        let near = view.unprojectPoint(SCNVector3(Float(point.x), Float(point.y), 0))
        let far = view.unprojectPoint(SCNVector3(Float(point.x), Float(point.y), 1))
        let dir = SIMD3(far.x - near.x, far.y - near.y, far.z - near.z)
        let local = skyNode.simdConvertVector(simd_normalize(dir), from: nil)
        return SkyMath.equatorial(from: local)
    }

    private func nearest(to eq: Equatorial, maxDistance: Double) -> SkyTarget? {
        var best: SkyTarget?
        var bestDist = maxDistance
        for body in lastBodies where shows(body) {
            let dist = SkyMath.angularDistance(ra1: eq.ra, dec1: eq.dec, ra2: body.ra, dec2: body.dec)
            if dist < bestDist {
                bestDist = dist
                best = makeBodyTarget(body)
            }
        }
        if showStars {
            for star in catalog.stars where star.mag <= 5.8 {
                let dist = SkyMath.angularDistance(ra1: eq.ra, dec1: eq.dec, ra2: star.ra, dec2: star.dec)
                let weighted = dist + max(0, star.mag) * 0.12
                if weighted < bestDist {
                    bestDist = weighted
                    best = makeStarTarget(star)
                }
            }
        }
        for dso in catalog.dsos where shows(layer: dso.layer) {
            let dist = SkyMath.angularDistance(ra1: eq.ra, dec1: eq.dec, ra2: dso.ra, dec2: dso.dec)
            if dist < bestDist {
                bestDist = dist
                best = makeDSOTarget(dso)
            }
        }
        return best
    }

    private func buildStarCloud() -> SCNNode {
        var vertices: [SCNVector3] = []
        var colors: [SIMD4<Float>] = []
        vertices.reserveCapacity(catalog.stars.count)
        colors.reserveCapacity(catalog.stars.count)
        for star in catalog.stars {
            vertices.append(SCNVector3(SkyMath.equatorialPosition(ra: star.ra, dec: star.dec)))
            let rgb = SkyMath.starColor(bv: star.bv)
            let alpha = Float(min(1, 0.18 + max(0, 6.3 - star.mag) * 0.16))
            colors.append(SIMD4(rgb.x, rgb.y, rgb.z, alpha))
        }
        let vertexSource = SCNGeometrySource(vertices: vertices)
        let colorData = colors.withUnsafeBufferPointer { Data(buffer: $0) }
        let colorSource = SCNGeometrySource(
            data: colorData,
            semantic: .color,
            vectorCount: colors.count,
            usesFloatComponents: true,
            componentsPerVector: 4,
            bytesPerComponent: MemoryLayout<Float>.size,
            dataOffset: 0,
            dataStride: MemoryLayout<SIMD4<Float>>.stride
        )
        let indices = (0..<vertices.count).map { Int32($0) }
        let element = SCNGeometryElement(indices: indices, primitiveType: .point)
        starPointElement = element
        applyStarSize()
        let geometry = SCNGeometry(sources: [vertexSource, colorSource], elements: [element])
        geometry.firstMaterial?.lightingModel = .constant
        geometry.firstMaterial?.readsFromDepthBuffer = true
        geometry.firstMaterial?.writesToDepthBuffer = true
        return SCNNode(geometry: geometry)
    }

    private func buildLines() -> SCNNode {
        let parent = SCNNode()
        for (index, line) in catalog.lines.enumerated() {
            let hue = CGFloat((index * 47) % 360) / 360
            let color = UIColor(hue: hue, saturation: 0.72, brightness: 0.95, alpha: 0.85)
            let emit = UIColor(hue: hue, saturation: 0.55, brightness: 0.8, alpha: 0.7)
            for segment in line.segments where segment.count >= 2 {
                let points = segment.map { pair in
                    SCNVector3(SkyMath.equatorialPosition(ra: wrapRA(pair[0]), dec: pair[1]) * 0.997)
                }
                let source = SCNGeometrySource(vertices: points)
                var indices: [Int32] = []
                for i in 0..<(points.count - 1) {
                    indices.append(Int32(i))
                    indices.append(Int32(i + 1))
                }
                let element = SCNGeometryElement(indices: indices, primitiveType: .line)
                let geometry = SCNGeometry(sources: [source], elements: [element])
                geometry.firstMaterial?.diffuse.contents = color
                geometry.firstMaterial?.emission.contents = emit
                geometry.firstMaterial?.lightingModel = .constant
                parent.addChildNode(SCNNode(geometry: geometry))
            }
        }
        return parent
    }

    private func buildLabels() {
        for star in catalog.namedStars where star.mag <= 1.7 {
            let pos = SCNVector3(SkyMath.equatorialPosition(ra: star.ra, dec: star.dec) * 1.03)
            let label = makeLabel(star.displayName, at: pos, size: 0.026, color: UIColor(white: 0.92, alpha: 0.9))
            label.name = "star-\(star.hip)"
            labelsNode.addChildNode(label)
        }
        for (index, item) in catalog.constellations.enumerated() {
            let hue = CGFloat((index * 47) % 360) / 360
            let color = UIColor(hue: hue, saturation: 0.55, brightness: 0.95, alpha: 0.82)
            let pos = SCNVector3(SkyMath.equatorialPosition(ra: item.ra, dec: item.dec) * 1.05)
            let label = makeLabel(item.id, at: pos, size: 0.036, color: color)
            label.name = "const-\(item.id)"
            labelsNode.addChildNode(label)
        }
        for dso in catalog.dsos where (dso.mag ?? 99) < 4.2 || ["M31", "M42", "M45", "M13", "M51"].contains(dso.id) {
            let pos = SCNVector3(SkyMath.equatorialPosition(ra: dso.ra, dec: dso.dec) * 1.02)
            let label = makeLabel(dso.id, at: pos, size: 0.022, color: UIColor(red: 0.78, green: 0.86, blue: 1.0, alpha: 0.8))
            label.name = "dso-\(dso.id)"
            labelsNode.addChildNode(label)
        }
    }

    private func buildPickProxies() {
        pickNode.categoryBitMask = 2
        for star in catalog.stars where star.mag <= 5.6 {
            let radius = CGFloat(max(0.012, (6.2 - star.mag) * 0.0065))
            let node = SCNNode(geometry: SCNSphere(radius: radius))
            node.geometry?.firstMaterial?.diffuse.contents = UIColor.clear
            node.geometry?.firstMaterial?.transparency = 0.0
            node.geometry?.firstMaterial?.writesToDepthBuffer = false
            node.position = SCNVector3(SkyMath.equatorialPosition(ra: star.ra, dec: star.dec))
            node.name = "hip-\(star.hip)"
            node.categoryBitMask = 2
            pickNode.addChildNode(node)
        }
        for dso in catalog.dsos {
            let node = SCNNode(geometry: SCNSphere(radius: 0.018))
            node.geometry?.firstMaterial?.diffuse.contents = UIColor.clear
            node.geometry?.firstMaterial?.transparency = 0.0
            node.geometry?.firstMaterial?.writesToDepthBuffer = false
            node.position = SCNVector3(SkyMath.equatorialPosition(ra: dso.ra, dec: dso.dec))
            node.name = dso.id
            node.categoryBitMask = 2
            pickNode.addChildNode(node)
        }
    }

    private func buildHorizon() {
        // Extend far beyond the unit-radius celestial sphere. The lower-sky dome
        // supplies a second, camera-centered depth barrier at the exact horizon.
        groundNode.geometry = SCNCylinder(radius: 40, height: 0.008)
        let black = UIColor(white: 0.0, alpha: 1)
        groundNode.geometry?.firstMaterial?.diffuse.contents = black
        groundNode.geometry?.firstMaterial?.emission.contents = black
        groundNode.geometry?.firstMaterial?.lightingModel = .constant
        groundNode.position = SCNVector3(0, -0.03, 0)
        horizonNode.addChildNode(groundNode)
        lowerSkyOccluderNode.geometry = buildLowerSkyOccluder()
        lowerSkyOccluderNode.position = SCNVector3(0, 0.18, 0)
        horizonNode.addChildNode(lowerSkyOccluderNode)
        mountainNode.addChildNode(buildMountainRing(radius: 0.94, phase: 0.4, color: UIColor(red: 0.09, green: 0.12, blue: 0.16, alpha: 1)))
        mountainNode.addChildNode(buildMountainRing(radius: 0.87, phase: 2.1, color: UIColor(red: 0.035, green: 0.05, blue: 0.075, alpha: 1)))
        horizonNode.addChildNode(mountainNode)
        let ring = SCNNode(geometry: SCNTorus(ringRadius: 1.18, pipeRadius: 0.0035))
        ring.geometry?.firstMaterial?.diffuse.contents = UIColor(white: 0.55, alpha: 0.22)
        ring.geometry?.firstMaterial?.emission.contents = UIColor(white: 0.4, alpha: 0.12)
        ring.geometry?.firstMaterial?.lightingModel = .constant
        horizonNode.addChildNode(ring)
        for (label, az) in [("N", 0.0), ("E", 90.0), ("S", 180.0), ("W", 270.0)] {
            let rad = az * SkyMath.deg
            let pos = SCNVector3(Float(sin(rad) * 1.22), 0.04, Float(-cos(rad) * 1.22))
            horizonNode.addChildNode(makeLabel(label, at: pos, size: 0.046, color: UIColor(red: 0.45, green: 0.82, blue: 0.98, alpha: 0.92), billboard: false))
        }
    }

    private func buildLowerSkyOccluder() -> SCNGeometry {
        let segments = 128
        let bands = 12
        let radius: Float = 0.96
        var vertices: [SCNVector3] = []
        var indices: [Int32] = []

        for band in 0...bands {
            let altitude = -90.0 * Double(band) / Double(bands)
            for segment in 0...segments {
                let azimuth = 360.0 * Double(segment) / Double(segments)
                vertices.append(SCNVector3(SkyMath.lookDirection(azimuth: azimuth, altitude: altitude) * radius))
            }
        }
        let stride = segments + 1
        for band in 0..<bands {
            for segment in 0..<segments {
                let a = Int32(band * stride + segment)
                let b = Int32((band + 1) * stride + segment)
                indices.append(contentsOf: [a, b, a + 1, a + 1, b, b + 1])
            }
        }
        let geometry = SCNGeometry(
            sources: [SCNGeometrySource(vertices: vertices)],
            elements: [SCNGeometryElement(indices: indices, primitiveType: .triangles)]
        )
        let material = SCNMaterial()
        material.diffuse.contents = UIColor.black
        material.emission.contents = UIColor.black
        material.lightingModel = .constant
        material.isDoubleSided = true
        material.writesToDepthBuffer = true
        geometry.firstMaterial = material
        return geometry
    }

    private func buildMountainRing(radius: Float, phase: Double, color: UIColor) -> SCNNode {
        let segments = 160
        var vertices: [SCNVector3] = []
        var indices: [Int32] = []
        for segment in 0...segments {
            let azimuth = 2 * Double.pi * Double(segment) / Double(segments)
            let ridge = 0.25
                + 0.055 * sin(azimuth * 3 + phase)
                + 0.035 * sin(azimuth * 7 - phase * 1.7)
                + 0.018 * sin(azimuth * 17 + phase * 2.3)
            let x = Float(sin(azimuth)) * radius
            let z = Float(-cos(azimuth)) * radius
            vertices.append(SCNVector3(x, -0.45, z))
            vertices.append(SCNVector3(x, Float(ridge), z))
        }
        for segment in 0..<segments {
            let base = Int32(segment * 2)
            indices.append(contentsOf: [base, base + 1, base + 3, base, base + 3, base + 2])
        }
        let geometry = SCNGeometry(
            sources: [SCNGeometrySource(vertices: vertices)],
            elements: [SCNGeometryElement(indices: indices, primitiveType: .triangles)]
        )
        let material = SCNMaterial()
        material.diffuse.contents = color
        material.emission.contents = color.withAlphaComponent(0.42)
        material.lightingModel = .constant
        material.isDoubleSided = true
        material.writesToDepthBuffer = true
        geometry.firstMaterial = material
        return SCNNode(geometry: geometry)
    }

    private func buildTelescope() {
        let metal = metalMaterial(UIColor(white: 0.22, alpha: 1), emit: UIColor(white: 0.08, alpha: 1))
        let accent = metalMaterial(UIColor(red: 0.18, green: 0.22, blue: 0.28, alpha: 1), emit: UIColor(red: 0.08, green: 0.12, blue: 0.18, alpha: 1))
        let tubeColor = metalMaterial(UIColor(white: 0.62, alpha: 1), emit: UIColor(white: 0.18, alpha: 1))
        let glow = metalMaterial(UIColor(red: 0.45, green: 0.82, blue: 0.98, alpha: 1), emit: UIColor(red: 0.25, green: 0.55, blue: 0.75, alpha: 1))

        let pad = SCNNode(geometry: SCNCylinder(radius: 0.09, height: 0.012))
        pad.geometry?.firstMaterial = accent
        pad.position = SCNVector3(0, 0.006, 0)
        telescopeRoot.addChildNode(pad)

        let pier = SCNNode(geometry: SCNCylinder(radius: 0.028, height: 0.13))
        pier.geometry?.firstMaterial = metal
        pier.position = SCNVector3(0, 0.075, 0)
        telescopeRoot.addChildNode(pier)

        let fork = SCNNode(geometry: SCNBox(width: 0.11, height: 0.018, length: 0.04, chamferRadius: 0.004))
        fork.geometry?.firstMaterial = metal
        fork.position = SCNVector3(0, 0.145, 0)
        telescopeRoot.addChildNode(fork)

        telescopeYawNode.position = SCNVector3(0, 0.155, 0)
        telescopeRoot.addChildNode(telescopeYawNode)

        let leftArm = SCNNode(geometry: SCNBox(width: 0.012, height: 0.11, length: 0.022, chamferRadius: 0.003))
        leftArm.geometry?.firstMaterial = accent
        leftArm.position = SCNVector3(-0.055, 0.05, 0)
        telescopeYawNode.addChildNode(leftArm)
        let rightArm = leftArm.clone()
        rightArm.position = SCNVector3(0.055, 0.05, 0)
        telescopeYawNode.addChildNode(rightArm)

        telescopePitchNode.position = SCNVector3(0, 0.09, 0)
        telescopeYawNode.addChildNode(telescopePitchNode)

        let hub = SCNNode(geometry: SCNSphere(radius: 0.018))
        hub.geometry?.firstMaterial = glow
        telescopePitchNode.addChildNode(hub)

        let tube = SCNNode(geometry: SCNCylinder(radius: 0.024, height: 0.22))
        tube.geometry?.firstMaterial = tubeColor
        tube.eulerAngles = SCNVector3(Float.pi / 2, 0, 0)
        tube.position = SCNVector3(0, 0, -0.02)
        telescopePitchNode.addChildNode(tube)

        let dew = SCNNode(geometry: SCNCylinder(radius: 0.03, height: 0.04))
        dew.geometry?.firstMaterial = metal
        dew.eulerAngles = SCNVector3(Float.pi / 2, 0, 0)
        dew.position = SCNVector3(0, 0, -0.14)
        telescopePitchNode.addChildNode(dew)

        let lens = SCNNode(geometry: SCNCylinder(radius: 0.018, height: 0.006))
        lens.geometry?.firstMaterial = glow
        lens.eulerAngles = SCNVector3(Float.pi / 2, 0, 0)
        lens.position = SCNVector3(0, 0, -0.16)
        telescopePitchNode.addChildNode(lens)

        let eyepiece = SCNNode(geometry: SCNCylinder(radius: 0.01, height: 0.04))
        eyepiece.geometry?.firstMaterial = metal
        eyepiece.position = SCNVector3(0, 0.03, 0.09)
        telescopePitchNode.addChildNode(eyepiece)
    }

    private func aimTelescope(yaw: Double, pitch: Double) {
        telescopeYaw = yaw
        telescopePitch = pitch
        applyTelescopeOrientation()
        updateTelescopeLaser()
    }

    private func applyTelescopeOrientation() {
        // The root is rotated to keep the model facing away from the viewer at its
        // placement azimuth. Compensate for that rotation so yaw remains an
        // absolute compass bearing in the same coordinate system as the sky.
        let localYaw = telescopePlacementAzimuth - telescopeYaw
        telescopeYawNode.eulerAngles = SCNVector3(0, Float(localYaw * SkyMath.deg), 0)

        // The optical axis points down local -Z, so positive X rotation raises it.
        telescopePitchNode.eulerAngles = SCNVector3(Float(telescopePitch * SkyMath.deg), 0, 0)
    }

    private func updateTelescopeLaser() {
        let visible = showTelescopeLaser && showTelescope && !arBackgroundEnabled
        telescopeLaserNode.isHidden = !visible
        guard visible else { return }

        let direction = simd_normalize(SkyMath.lookDirection(azimuth: telescopeYaw, altitude: telescopePitch))
        let placement = telescopePlacementAzimuth * SkyMath.deg
        let pivot = SIMD3<Float>(
            Float(sin(placement)) * 0.72,
            0.245,
            Float(-cos(placement)) * 0.72
        )
        let start = pivot + direction * 0.165
        let dot = simd_dot(start, direction)
        let discriminant = max(0, dot * dot - (simd_length_squared(start) - 0.985 * 0.985))
        let distance = max(0.02, -dot + sqrt(discriminant))
        let end = start + direction * distance
        let midpoint = (start + end) * 0.5

        let beam = SCNCylinder(radius: 0.0022, height: CGFloat(distance))
        let material = SCNMaterial()
        let green = UIColor(red: 0.3, green: 1.0, blue: 0.42, alpha: 0.88)
        material.diffuse.contents = green
        material.emission.contents = green
        material.lightingModel = .constant
        material.writesToDepthBuffer = false
        material.readsFromDepthBuffer = true
        beam.firstMaterial = material
        telescopeLaserNode.geometry = beam
        telescopeLaserNode.simdPosition = midpoint
        telescopeLaserNode.simdOrientation = simd_quatf(from: SIMD3<Float>(0, 1, 0), to: direction)
        telescopeLaserNode.renderingOrder = 20
    }

    private func metalMaterial(_ color: UIColor, emit: UIColor) -> SCNMaterial {
        let material = SCNMaterial()
        material.diffuse.contents = color
        material.emission.contents = emit
        material.metalness.contents = 0.75
        material.roughness.contents = 0.35
        material.lightingModel = .physicallyBased
        return material
    }

    private func recountVisible() {
        var visible = 0
        var named = 0
        if showStars {
            for star in catalog.stars {
                let horiz = SkyMath.horizontal(ra: star.ra, dec: star.dec, latitude: latitude, lst: lst)
                if horiz.altitude > 0 {
                    visible += 1
                    if star.name != nil { named += 1 }
                }
            }
        }
        visibleStarCount = visible
        visibleNamedCount = named
    }

    private func applyStarSize() {
        guard let element = starPointElement else { return }
        let scale = CGFloat(starSize)
        element.pointSize = 3.2 * scale
        element.minimumPointScreenSpaceRadius = 0.6 * scale
        element.maximumPointScreenSpaceRadius = 7 * scale
    }

    private func applyCatalogVisibility() {
        pickNode.isHidden = false
        for node in pickNode.childNodes {
            guard let name = node.name else { continue }
            if name.hasPrefix("hip-") {
                node.isHidden = !showStars
            } else if let dso = catalog.dsos.first(where: { $0.id == name }) {
                node.isHidden = !shows(layer: dso.layer)
            }
        }
        for node in labelsNode.childNodes {
            guard let name = node.name else { continue }
            if name.hasPrefix("star-") {
                node.isHidden = !showStars
            } else if name.hasPrefix("dso-") {
                let id = String(name.dropFirst(4))
                if let dso = catalog.dsos.first(where: { $0.id == id }) {
                    node.isHidden = !shows(layer: dso.layer)
                }
            }
        }
    }

    private func shows(layer: SkyLayer) -> Bool {
        switch layer {
        case .stars: return showStars
        case .planets: return showPlanets
        case .sun: return showSun
        case .moon: return showMoon
        case .galaxies: return showGalaxies
        case .clusters: return showClusters
        case .nebulae: return showNebulae
        case .constellations: return !constellationNode.isHidden
        }
    }

    private func shows(_ body: SolarBody) -> Bool {
        switch body.kind {
        case .sun: return showSun
        case .moon: return showMoon
        case .planet: return showPlanets
        default: return true
        }
    }

    private func updateSolarSystem() {
        lastBodies = SkyMath.solarSystem(date: date).filter(shows)
        planetsNode.childNodes.forEach { $0.removeFromParentNode() }
        let size = CGFloat(planetSize)
        for body in lastBodies {
            let radius: CGFloat
            switch body.kind {
            case .sun: radius = 0.048 * size
            case .moon: radius = 0.03 * size
            default: radius = CGFloat(max(0.01, min(0.028, (5.2 - body.mag) * 0.005))) * size
            }
            let node = SCNNode(geometry: SCNSphere(radius: radius))
            let ui = UIColor(red: CGFloat(body.color.x), green: CGFloat(body.color.y), blue: CGFloat(body.color.z), alpha: 1)
            node.geometry?.firstMaterial?.diffuse.contents = ui
            node.geometry?.firstMaterial?.emission.contents = ui
            node.geometry?.firstMaterial?.lightingModel = .constant
            node.position = SCNVector3(SkyMath.equatorialPosition(ra: body.ra, dec: body.dec) * 0.93)
            node.name = body.id
            node.categoryBitMask = 2
            planetsNode.addChildNode(node)
            let glow = SCNNode(geometry: SCNSphere(radius: radius * 1.7))
            glow.geometry?.firstMaterial?.diffuse.contents = ui.withAlphaComponent(0.18)
            glow.geometry?.firstMaterial?.emission.contents = ui.withAlphaComponent(0.45)
            glow.geometry?.firstMaterial?.lightingModel = .constant
            glow.geometry?.firstMaterial?.writesToDepthBuffer = false
            glow.position = node.position
            planetsNode.addChildNode(glow)
            planetsNode.addChildNode(makeLabel(body.name, at: node.position + SCNVector3(0, Float(radius) + 0.03, 0), size: 0.028, color: ui))
        }
    }

    private func makeLabel(_ text: String, at position: SCNVector3, size: CGFloat, color: UIColor, billboard: Bool = true) -> SCNNode {
        let geometry = SCNText(string: text, extrusionDepth: 0)
        geometry.font = UIFont.systemFont(ofSize: 10, weight: .semibold)
        geometry.flatness = 0.2
        geometry.firstMaterial?.diffuse.contents = color
        geometry.firstMaterial?.emission.contents = color
        geometry.firstMaterial?.lightingModel = .constant
        geometry.firstMaterial?.writesToDepthBuffer = false
        let node = SCNNode(geometry: geometry)
        let box = geometry.boundingBox
        let width = max(0.001, box.max.x - box.min.x)
        let scale = Float(size / CGFloat(width))
        node.scale = SCNVector3(scale, scale, scale)
        node.position = position
        node.pivot = SCNMatrix4MakeTranslation((box.max.x + box.min.x) / 2, 0, 0)
        if billboard {
            node.constraints = [SCNBillboardConstraint()]
        }
        return node
    }

    private func makeStarTarget(_ star: CatalogStar) -> SkyTarget {
        placed(SkyTarget(
            id: "hip-\(star.hip)",
            name: star.displayName,
            subtitle: String(format: "HIP %d  ·  mag %.1f", star.hip, star.mag),
            kind: .star,
            ra: star.ra,
            dec: star.dec,
            mag: star.mag,
            color: SkyMath.starColor(bv: star.bv)
        ))
    }

    private func makeBodyTarget(_ body: SolarBody) -> SkyTarget {
        placed(SkyTarget(
            id: body.id,
            name: body.name,
            subtitle: body.kind.rawValue.capitalized,
            kind: body.kind,
            ra: body.ra,
            dec: body.dec,
            mag: body.mag,
            color: body.color
        ))
    }

    private func makeDSOTarget(_ dso: CatalogDSO) -> SkyTarget {
        placed(SkyTarget(
            id: dso.id,
            name: dso.name,
            subtitle: [dso.id, dso.kind].joined(separator: "  ·  "),
            kind: .dso,
            ra: dso.ra,
            dec: dso.dec,
            mag: dso.mag,
            color: SIMD3(0.72, 0.86, 1.0)
        ))
    }

    private func makeSkyPoint(_ eq: Equatorial) -> SkyTarget {
        placed(SkyTarget(
            id: "sky-\(Int(eq.ra * 10))-\(Int(eq.dec * 10))",
            name: "Sky point",
            subtitle: "Double-tap coordinate",
            kind: .sky,
            ra: eq.ra,
            dec: eq.dec,
            mag: nil,
            color: SIMD3(0.45, 0.82, 0.98)
        ))
    }

    private func wrapRA(_ lon: Double) -> Double {
        lon < 0 ? lon + 360 : lon
    }
}

nonisolated extension SCNVector3 {
    init(_ v: SIMD3<Float>) {
        self.init(v.x, v.y, v.z)
    }

    static func + (lhs: SCNVector3, rhs: SCNVector3) -> SCNVector3 {
        SCNVector3(lhs.x + rhs.x, lhs.y + rhs.y, lhs.z + rhs.z)
    }

    static func * (lhs: SCNVector3, rhs: Float) -> SCNVector3 {
        SCNVector3(lhs.x * rhs, lhs.y * rhs, lhs.z * rhs)
    }

    func distance(to other: SCNVector3) -> Float {
        let dx = x - other.x
        let dy = y - other.y
        let dz = z - other.z
        return sqrt(dx * dx + dy * dy + dz * dz)
    }
}

private extension SCNHitTestResult {
    @MainActor
    func screenDistance(from point: CGPoint, in view: SCNView) -> Float {
        let projected = view.projectPoint(worldCoordinates)
        let dx = CGFloat(projected.x) - point.x
        let dy = CGFloat(projected.y) - point.y
        return Float(sqrt(dx * dx + dy * dy)) * 0.002
    }
}

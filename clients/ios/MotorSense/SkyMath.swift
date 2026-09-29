import Foundation
import simd

nonisolated struct Equatorial: Equatable, Sendable {
    var ra: Double
    var dec: Double
}

nonisolated struct Horizontal: Equatable, Sendable {
    var azimuth: Double
    var altitude: Double
}

nonisolated enum SkyMath {
    static let deg = Double.pi / 180
    static let obliquity = 23.43929111

    static func julianDate(_ date: Date) -> Double {
        date.timeIntervalSince1970 / 86_400 + 2_440_587.5
    }

    static func centuriesJ2000(_ date: Date) -> Double {
        (julianDate(date) - 2_451_545.0) / 36_525
    }

    static func gmstDegrees(_ date: Date) -> Double {
        let jd = julianDate(date)
        let t = (jd - 2_451_545.0) / 36_525
        let st = 280.46061837
            + 360.98564736629 * (jd - 2_451_545.0)
            + 0.000387933 * t * t
            - t * t * t / 38_710_000
        return wrapDegrees(st)
    }

    static func lstDegrees(date: Date, longitude: Double) -> Double {
        wrapDegrees(gmstDegrees(date) + longitude)
    }

    static func horizontal(ra: Double, dec: Double, latitude: Double, lst: Double) -> Horizontal {
        let ha = (lst - ra) * deg
        let lat = latitude * deg
        let decR = dec * deg
        let sinAlt = sin(decR) * sin(lat) + cos(decR) * cos(lat) * cos(ha)
        let altitude = asin(max(-1, min(1, sinAlt)))
        let cosAlt = max(1e-9, cos(altitude))
        let sinAz = -sin(ha) * cos(decR) / cosAlt
        let cosAz = (sin(decR) - sin(altitude) * sin(lat)) / (cosAlt * cos(lat))
        return Horizontal(azimuth: wrapDegrees(atan2(sinAz, cosAz) / deg), altitude: altitude / deg)
    }

    static func equatorialPosition(ra: Double, dec: Double) -> SIMD3<Float> {
        let raR = ra * deg
        let decR = dec * deg
        let cosDec = cos(decR)
        return SIMD3(
            Float(cosDec * sin(raR)),
            Float(sin(decR)),
            Float(cosDec * cos(raR))
        )
    }

    static func equatorial(from local: SIMD3<Float>) -> Equatorial {
        let x = Double(local.x)
        let y = Double(local.y)
        let z = Double(local.z)
        return Equatorial(
            ra: wrapDegrees(atan2(x, z) / deg),
            dec: atan2(y, sqrt(x * x + z * z)) / deg
        )
    }

    static func equatorial(azimuth: Double, altitude: Double, latitude: Double, lst: Double) -> Equatorial {
        let az = azimuth * deg
        let alt = altitude * deg
        let lat = latitude * deg
        let sinDec = sin(alt) * sin(lat) + cos(alt) * cos(lat) * cos(az)
        let dec = asin(max(-1, min(1, sinDec)))
        let cosDec = max(1e-9, cos(dec))
        let sinHA = -sin(az) * cos(alt) / cosDec
        let cosHA = (sin(alt) - sin(dec) * sin(lat)) / (cosDec * cos(lat))
        let ha = atan2(sinHA, cosHA) / deg
        return Equatorial(ra: wrapDegrees(lst - ha), dec: dec / deg)
    }

    static func lookDirection(azimuth: Double, altitude: Double) -> SIMD3<Float> {
        let az = azimuth * deg
        let alt = altitude * deg
        return SIMD3(
            Float(sin(az) * cos(alt)),
            Float(sin(alt)),
            Float(-cos(az) * cos(alt))
        )
    }

    static func lookAngles(from direction: SIMD3<Float>) -> Horizontal {
        let dir = simd_normalize(direction)
        let altitude = asin(Double(max(-1, min(1, dir.y)))) / deg
        let azimuth = wrapDegrees(atan2(Double(dir.x), Double(-dir.z)) / deg)
        return Horizontal(azimuth: azimuth, altitude: altitude)
    }

    static func angularDistance(ra1: Double, dec1: Double, ra2: Double, dec2: Double) -> Double {
        let p1 = equatorialPosition(ra: ra1, dec: dec1)
        let p2 = equatorialPosition(ra: ra2, dec: dec2)
        let dot = max(-1, min(1, Double(simd_dot(simd_normalize(p1), simd_normalize(p2)))))
        return acos(dot) / deg
    }

    static func starColor(bv: Double) -> SIMD3<Float> {
        let t = max(-0.4, min(2.0, bv))
        if t < 0 {
            let u = Float((t + 0.4) / 0.4)
            return mix(SIMD3(0.62, 0.72, 1.0), SIMD3(0.78, 0.86, 1.0), u)
        }
        if t < 0.45 {
            let u = Float(t / 0.45)
            return mix(SIMD3(0.82, 0.88, 1.0), SIMD3(1.0, 0.98, 0.94), u)
        }
        if t < 1.1 {
            let u = Float((t - 0.45) / 0.65)
            return mix(SIMD3(1.0, 0.98, 0.94), SIMD3(1.0, 0.82, 0.55), u)
        }
        let u = Float((t - 1.1) / 0.9)
        return mix(SIMD3(1.0, 0.82, 0.55), SIMD3(1.0, 0.58, 0.38), u)
    }

    static func mix(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ t: Float) -> SIMD3<Float> {
        a + (b - a) * max(0, min(1, t))
    }

    static func solarSystem(date: Date) -> [SolarBody] {
        let t = centuriesJ2000(date)
        let earth = heliocentric(elements: .earth, t: t)
        var bodies: [SolarBody] = []

        let sunVec = -earth.ecliptic
        bodies.append(makeBody(id: "sun", name: "Sun", kind: .sun, ecliptic: sunVec, t: t, mag: -26.7, color: SIMD3(1, 0.84, 0.28)))

        for planet in PlanetElements.planets {
            let helio = heliocentric(elements: planet, t: t)
            let geo = helio.ecliptic - earth.ecliptic
            let r = simd_length(helio.ecliptic)
            let dist = simd_length(geo)
            let mag = planetMagnitude(planet.id, r: r, delta: dist)
            bodies.append(makeBody(id: planet.id, name: planet.name, kind: .planet, ecliptic: geo, t: t, mag: mag, color: planet.color))
        }

        bodies.append(moon(date: date, t: t))
        return bodies
    }

    private static func makeBody(id: String, name: String, kind: SkyObjectKind, ecliptic: SIMD3<Double>, t: Double, mag: Double, color: SIMD3<Float>) -> SolarBody {
        let eq = equatorialFromEcliptic(ecliptic, t: t)
        return SolarBody(id: id, name: name, kind: kind, ra: eq.ra, dec: eq.dec, mag: mag, color: color)
    }

    private static func moon(date: Date, t: Double) -> SolarBody {
        let d = julianDate(date) - 2_451_545.0
        let l = (218.316 + 13.176396 * d) * deg
        let m = (134.963 + 13.064993 * d) * deg
        let f = (93.272 + 13.229350 * d) * deg
        let lon = l + 6.289 * deg * sin(m)
        let lat = 5.128 * deg * sin(f)
        let x = cos(lat) * cos(lon)
        let y = cos(lat) * sin(lon)
        let z = sin(lat)
        let eq = equatorialFromEcliptic(SIMD3(x, y, z), t: t)
        let phase = 0.5 * (1 - cos(m))
        let mag = -12.7 + 1.5 * phase
        return SolarBody(id: "moon", name: "Moon", kind: .moon, ra: eq.ra, dec: eq.dec, mag: mag, color: SIMD3(0.92, 0.93, 0.88))
    }

    private static func equatorialFromEcliptic(_ v: SIMD3<Double>, t: Double) -> Equatorial {
        let eps = (obliquity - 0.0130042 * t) * deg
        let y = v.y * cos(eps) - v.z * sin(eps)
        let z = v.y * sin(eps) + v.z * cos(eps)
        let ra = wrapDegrees(atan2(y, v.x) / deg)
        let dec = atan2(z, sqrt(v.x * v.x + y * y)) / deg
        return Equatorial(ra: ra, dec: dec)
    }

    private static func heliocentric(elements: PlanetElements, t: Double) -> (ecliptic: SIMD3<Double>, a: Double) {
        let a = elements.a.0 + elements.a.1 * t
        let e = elements.e.0 + elements.e.1 * t
        let i = (elements.i.0 + elements.i.1 * t) * deg
        let l = wrapDegrees(elements.l.0 + elements.l.1 * t) * deg
        let wbar = wrapDegrees(elements.wbar.0 + elements.wbar.1 * t) * deg
        let omega = wrapDegrees(elements.omega.0 + elements.omega.1 * t) * deg
        let w = wbar - omega
        let m = atan2(sin(l - wbar), cos(l - wbar))
        var eAnom = m
        for _ in 0..<10 {
            eAnom = m + e * sin(eAnom)
        }
        let xv = a * (cos(eAnom) - e)
        let yv = a * sqrt(max(0, 1 - e * e)) * sin(eAnom)
        let xh = xv * (cos(w) * cos(omega) - sin(w) * sin(omega) * cos(i))
            - yv * (sin(w) * cos(omega) + cos(w) * sin(omega) * cos(i))
        let yh = xv * (cos(w) * sin(omega) + sin(w) * cos(omega) * cos(i))
            + yv * (cos(w) * cos(omega) * cos(i) - sin(w) * sin(omega))
        let zh = xv * (sin(w) * sin(i)) + yv * (cos(w) * sin(i))
        return (SIMD3(xh, yh, zh), a)
    }

    private static func planetMagnitude(_ id: String, r: Double, delta: Double) -> Double {
        let h: Double
        switch id {
        case "mercury": h = -0.6
        case "venus": h = -4.4
        case "mars": h = -1.5
        case "jupiter": h = -9.4
        case "saturn": h = -8.9
        case "uranus": h = -7.2
        case "neptune": h = -6.9
        default: h = 0
        }
        return h + 5 * log10(max(0.01, r * delta))
    }
}

nonisolated struct SolarBody: Identifiable, Sendable {
    let id: String
    let name: String
    let kind: SkyObjectKind
    let ra: Double
    let dec: Double
    let mag: Double
    let color: SIMD3<Float>
}

nonisolated enum SkyObjectKind: String, Sendable {
    case star
    case planet
    case sun
    case moon
    case dso
    case constellation
    case sky
}

nonisolated private struct PlanetElements {
    let id: String
    let name: String
    let color: SIMD3<Float>
    let a: (Double, Double)
    let e: (Double, Double)
    let i: (Double, Double)
    let l: (Double, Double)
    let wbar: (Double, Double)
    let omega: (Double, Double)

    static let earth = PlanetElements(
        id: "earth",
        name: "Earth",
        color: SIMD3(0.2, 0.4, 1),
        a: (1.00000261, 0.00000562),
        e: (0.01671123, -0.00004392),
        i: (-0.00001531, -0.01294668),
        l: (100.46457166, 35999.37244981),
        wbar: (102.93768193, 0.32327364),
        omega: (0, 0)
    )

    static let planets: [PlanetElements] = [
        PlanetElements(id: "mercury", name: "Mercury", color: SIMD3(0.78, 0.74, 0.62), a: (0.38709927, 0.00000037), e: (0.20563593, 0.00001906), i: (7.00497902, -0.00594749), l: (252.25032350, 149472.67411175), wbar: (77.45779628, 0.16047689), omega: (48.33076593, -0.12534081)),
        PlanetElements(id: "venus", name: "Venus", color: SIMD3(1.0, 0.86, 0.42), a: (0.72333566, 0.00000390), e: (0.00677672, -0.00004107), i: (3.39467605, -0.00078890), l: (181.97909950, 58517.81538729), wbar: (131.60246718, 0.00268329), omega: (76.67984255, -0.27769418)),
        PlanetElements(id: "mars", name: "Mars", color: SIMD3(1.0, 0.38, 0.22), a: (1.52371034, 0.0001847), e: (0.09339410, 0.00007882), i: (1.84969142, -0.00813131), l: (-4.55343205, 19140.30268499), wbar: (-23.94362959, 0.44441088), omega: (49.55953891, -0.29257343)),
        PlanetElements(id: "jupiter", name: "Jupiter", color: SIMD3(1.0, 0.72, 0.38), a: (5.20288700, -0.00011607), e: (0.04838624, -0.00013253), i: (1.30439695, -0.00183714), l: (34.39644051, 3034.74612775), wbar: (14.72847983, 0.21252668), omega: (100.47390909, 0.20469106)),
        PlanetElements(id: "saturn", name: "Saturn", color: SIMD3(1.0, 0.90, 0.48), a: (9.53667594, -0.00125060), e: (0.05386179, -0.00050991), i: (2.48599187, 0.00193609), l: (49.95424423, 1222.49362201), wbar: (92.59887831, -0.41897216), omega: (113.66242448, -0.28867794)),
        PlanetElements(id: "uranus", name: "Uranus", color: SIMD3(0.42, 0.92, 0.95), a: (19.18916464, -0.00196176), e: (0.04725744, -0.00004397), i: (0.77263783, -0.00242939), l: (313.23810451, 428.48202785), wbar: (170.95427630, 0.40805281), omega: (74.01692503, 0.04240589)),
        PlanetElements(id: "neptune", name: "Neptune", color: SIMD3(0.28, 0.42, 1.0), a: (30.06992276, 0.00026291), e: (0.00859048, 0.00005105), i: (1.77004347, 0.00035372), l: (-55.12002969, 218.45945325), wbar: (44.96476227, -0.32241464), omega: (131.78422574, -0.00508664)),
    ]
}

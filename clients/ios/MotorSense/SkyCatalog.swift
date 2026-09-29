import Foundation
import simd

nonisolated struct CatalogStar: Identifiable, Sendable {
    var hip: Int
    var ra: Double
    var dec: Double
    var mag: Double
    var bv: Double
    var name: String?

    var id: Int { hip }

    var displayName: String {
        name ?? "HIP \(hip)"
    }
}

nonisolated enum SkyLayer: String, CaseIterable, Identifiable, Sendable {
    case stars
    case planets
    case sun
    case moon
    case galaxies
    case clusters
    case nebulae
    case constellations

    var id: String { rawValue }

    var title: String {
        switch self {
        case .stars: return "Stars"
        case .planets: return "Planets"
        case .sun: return "Sun"
        case .moon: return "Moon"
        case .galaxies: return "Galaxies"
        case .clusters: return "Clusters"
        case .nebulae: return "Nebulae"
        case .constellations: return "Constellations"
        }
    }

    var subtitle: String {
        switch self {
        case .stars: return "Named and catalog stars"
        case .planets: return "Mercury through Neptune"
        case .sun: return "Solar disk"
        case .moon: return "Earth's moon"
        case .galaxies: return "Messier galaxies"
        case .clusters: return "Open and globular clusters"
        case .nebulae: return "Nebulae and remnants"
        case .constellations: return "IAU stick figures"
        }
    }

    var icon: String {
        switch self {
        case .stars: return "sparkle"
        case .planets: return "globe"
        case .sun: return "sun.max.fill"
        case .moon: return "moon.fill"
        case .galaxies: return "hurricane"
        case .clusters: return "circle.hexagongrid.fill"
        case .nebulae: return "cloud.fill"
        case .constellations: return "star.circle"
        }
    }

    static func dso(_ kind: String) -> SkyLayer {
        let lowered = kind.lowercased()
        if lowered.contains("galaxy") { return .galaxies }
        if lowered.contains("cluster") || lowered.contains("asterism") { return .clusters }
        return .nebulae
    }
}

nonisolated struct CatalogDSO: Identifiable, Sendable {
    var id: String
    var name: String
    var kind: String
    var mag: Double?
    var ra: Double
    var dec: Double
    var catalog: String

    var layer: SkyLayer { SkyLayer.dso(kind) }
}

nonisolated struct CatalogConstellation: Identifiable, Sendable {
    var id: String
    var name: String
    var ra: Double
    var dec: Double
}

nonisolated struct ConstellationLine: Sendable {
    var id: String
    var segments: [[[Double]]]
}

nonisolated struct SkyTarget: Identifiable, Equatable, Sendable {
    var id: String
    var name: String
    var subtitle: String
    var kind: SkyObjectKind
    var ra: Double
    var dec: Double
    var mag: Double?
    var color: SIMD3<Float>
    var azimuth: Double = 0
    var altitude: Double = 0

    var searchText: String {
        "\(name) \(subtitle) \(id)".lowercased()
    }

    var isVisible: Bool { altitude > 0 }

    func placed(latitude: Double, lst: Double) -> SkyTarget {
        let horiz = SkyMath.horizontal(ra: ra, dec: dec, latitude: latitude, lst: lst)
        var next = self
        next.azimuth = horiz.azimuth
        next.altitude = horiz.altitude
        return next
    }
}

nonisolated struct SkyCatalog: Sendable {
    let stars: [CatalogStar]
    let lines: [ConstellationLine]
    let constellations: [CatalogConstellation]
    let dsos: [CatalogDSO]
    let namedStars: [CatalogStar]

    static let shared: SkyCatalog = load()

    private static func load() -> SkyCatalog {
        guard let url = Bundle.main.url(forResource: "sky_catalog", withExtension: "json", subdirectory: "SkyData")
                ?? Bundle.main.url(forResource: "sky_catalog", withExtension: "json") else {
            return SkyCatalog(stars: [], lines: [], constellations: [], dsos: [])
        }
        do {
            let data = try Data(contentsOf: url)
            return try JSONDecoder().decode(SkyCatalogFile.self, from: data).makeCatalog()
        } catch {
            return SkyCatalog(stars: [], lines: [], constellations: [], dsos: [])
        }
    }

    init(stars: [CatalogStar], lines: [ConstellationLine], constellations: [CatalogConstellation], dsos: [CatalogDSO]) {
        self.stars = stars
        self.lines = lines
        self.constellations = constellations
        self.dsos = dsos
        namedStars = stars.filter { $0.name != nil }
    }

    func targets(at date: Date) -> [SkyTarget] {
        var items: [SkyTarget] = []
        items.append(contentsOf: SkyMath.solarSystem(date: date).map { body in
            SkyTarget(
                id: body.id,
                name: body.name,
                subtitle: body.kind == .planet ? "Planet" : body.kind.rawValue.capitalized,
                kind: body.kind,
                ra: body.ra,
                dec: body.dec,
                mag: body.mag,
                color: body.color
            )
        })
        items.append(contentsOf: namedStars.map { star in
            SkyTarget(
                id: "hip-\(star.hip)",
                name: star.displayName,
                subtitle: String(format: "HIP %d  ·  mag %.1f", star.hip, star.mag),
                kind: .star,
                ra: star.ra,
                dec: star.dec,
                mag: star.mag,
                color: SkyMath.starColor(bv: star.bv)
            )
        })
        items.append(contentsOf: dsos.map { dso in
            SkyTarget(
                id: dso.id,
                name: dso.name,
                subtitle: [dso.id, dso.kind, dso.catalog].filter { !$0.isEmpty }.joined(separator: "  ·  "),
                kind: .dso,
                ra: dso.ra,
                dec: dso.dec,
                mag: dso.mag,
                color: SIMD3(0.72, 0.86, 1.0)
            )
        })
        items.append(contentsOf: constellations.map { item in
            SkyTarget(
                id: "const-\(item.id)",
                name: item.name,
                subtitle: "Constellation \(item.id)",
                kind: .constellation,
                ra: item.ra,
                dec: item.dec,
                mag: nil,
                color: SIMD3(0.55, 0.72, 1.0)
            )
        })
        return items
    }
}

nonisolated private struct SkyCatalogFile: Decodable {
    struct Star: Decodable {
        var h: Int
        var r: Double
        var d: Double
        var m: Double
        var b: Double
        var n: String?
    }

    struct Line: Decodable {
        var id: String
        var p: [[[Double]]]
    }

    struct Constellation: Decodable {
        var id: String
        var n: String
        var r: Double
        var d: Double
    }

    struct DSO: Decodable {
        var id: String
        var n: String
        var k: String
        var m: Double?
        var r: Double
        var d: Double
        var c: String
    }

    var stars: [Star]
    var lines: [Line]
    var constellations: [Constellation]
    var dsos: [DSO]

    func makeCatalog() -> SkyCatalog {
        SkyCatalog(
            stars: stars.map { CatalogStar(hip: $0.h, ra: $0.r, dec: $0.d, mag: $0.m, bv: $0.b, name: $0.n) },
            lines: lines.map { ConstellationLine(id: $0.id, segments: $0.p) },
            constellations: constellations.map { CatalogConstellation(id: $0.id, name: $0.n, ra: $0.r, dec: $0.d) },
            dsos: dsos.map { CatalogDSO(id: $0.id, name: $0.n, kind: $0.k, mag: $0.m, ra: $0.r, dec: $0.d, catalog: $0.c) }
        )
    }
}

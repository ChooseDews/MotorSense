import Combine
import SwiftUI
import UIKit

@MainActor
final class AppSettings: ObservableObject {
    @Published var redMode: Bool {
        didSet { UserDefaults.standard.set(redMode, forKey: Self.redModeKey) }
    }
    @Published var showCoordinates: Bool {
        didSet { UserDefaults.standard.set(showCoordinates, forKey: Self.coordsKey) }
    }
    @Published var hapticsEnabled: Bool {
        didSet { UserDefaults.standard.set(hapticsEnabled, forKey: Self.hapticsKey) }
    }
    @Published var keepScreenOn: Bool {
        didSet {
            UserDefaults.standard.set(keepScreenOn, forKey: Self.screenKey)
            UIApplication.shared.isIdleTimerDisabled = keepScreenOn
        }
    }
    @Published var showConstellations: Bool {
        didSet { UserDefaults.standard.set(showConstellations, forKey: Self.constKey) }
    }
    @Published var showStars: Bool {
        didSet { UserDefaults.standard.set(showStars, forKey: Self.starsKey) }
    }
    @Published var showPlanets: Bool {
        didSet { UserDefaults.standard.set(showPlanets, forKey: Self.planetsKey) }
    }
    @Published var showSun: Bool {
        didSet { UserDefaults.standard.set(showSun, forKey: Self.sunKey) }
    }
    @Published var showMoon: Bool {
        didSet { UserDefaults.standard.set(showMoon, forKey: Self.moonKey) }
    }
    @Published var showGalaxies: Bool {
        didSet { UserDefaults.standard.set(showGalaxies, forKey: Self.galaxiesKey) }
    }
    @Published var showClusters: Bool {
        didSet { UserDefaults.standard.set(showClusters, forKey: Self.clustersKey) }
    }
    @Published var showNebulae: Bool {
        didSet { UserDefaults.standard.set(showNebulae, forKey: Self.nebulaeKey) }
    }
    @Published var showMountains: Bool {
        didSet { UserDefaults.standard.set(showMountains, forKey: Self.mountainsKey) }
    }
    @Published var showTelescopeLaser: Bool {
        didSet { UserDefaults.standard.set(showTelescopeLaser, forKey: Self.telescopeLaserKey) }
    }
    @Published var starSize: Double {
        didSet { UserDefaults.standard.set(starSize, forKey: Self.starSizeKey) }
    }
    @Published var planetSize: Double {
        didSet { UserDefaults.standard.set(planetSize, forKey: Self.planetSizeKey) }
    }

    private static let redModeKey = "settings.redMode"
    private static let coordsKey = "settings.showCoordinates"
    private static let hapticsKey = "settings.hapticsEnabled"
    private static let screenKey = "settings.keepScreenOn"
    private static let constKey = "settings.showConstellations"
    private static let starsKey = "settings.showStars"
    private static let planetsKey = "settings.showPlanets"
    private static let sunKey = "settings.showSun"
    private static let moonKey = "settings.showMoon"
    private static let galaxiesKey = "settings.showGalaxies"
    private static let clustersKey = "settings.showClusters"
    private static let nebulaeKey = "settings.showNebulae"
    private static let mountainsKey = "settings.showMountains"
    private static let telescopeLaserKey = "settings.showTelescopeLaser"
    private static let starSizeKey = "settings.starSize"
    private static let planetSizeKey = "settings.planetSize"

    init() {
        let defaults = UserDefaults.standard
        for key in [Self.hapticsKey, Self.constKey, Self.starsKey, Self.planetsKey, Self.sunKey, Self.moonKey, Self.galaxiesKey, Self.clustersKey, Self.nebulaeKey, Self.mountainsKey] {
            if defaults.object(forKey: key) == nil {
                defaults.set(true, forKey: key)
            }
        }
        if defaults.object(forKey: Self.starSizeKey) == nil {
            defaults.set(1.0, forKey: Self.starSizeKey)
        }
        if defaults.object(forKey: Self.planetSizeKey) == nil {
            defaults.set(1.0, forKey: Self.planetSizeKey)
        }
        redMode = defaults.bool(forKey: Self.redModeKey)
        showCoordinates = defaults.bool(forKey: Self.coordsKey)
        hapticsEnabled = defaults.bool(forKey: Self.hapticsKey)
        keepScreenOn = defaults.bool(forKey: Self.screenKey)
        showConstellations = defaults.bool(forKey: Self.constKey)
        showStars = defaults.bool(forKey: Self.starsKey)
        showPlanets = defaults.bool(forKey: Self.planetsKey)
        showSun = defaults.bool(forKey: Self.sunKey)
        showMoon = defaults.bool(forKey: Self.moonKey)
        showGalaxies = defaults.bool(forKey: Self.galaxiesKey)
        showClusters = defaults.bool(forKey: Self.clustersKey)
        showNebulae = defaults.bool(forKey: Self.nebulaeKey)
        showMountains = defaults.bool(forKey: Self.mountainsKey)
        showTelescopeLaser = defaults.bool(forKey: Self.telescopeLaserKey)
        starSize = defaults.double(forKey: Self.starSizeKey)
        planetSize = defaults.double(forKey: Self.planetSizeKey)
        UIApplication.shared.isIdleTimerDisabled = keepScreenOn
    }

    func shows(_ target: SkyTarget) -> Bool {
        switch target.kind {
        case .star: return showStars
        case .planet: return showPlanets
        case .sun: return showSun
        case .moon: return showMoon
        case .constellation: return showConstellations
        case .sky: return true
        case .dso:
            guard let dso = SkyCatalog.shared.dsos.first(where: { $0.id == target.id }) else { return true }
            return shows(layer: dso.layer)
        }
    }

    func shows(layer: SkyLayer) -> Bool {
        switch layer {
        case .stars: return showStars
        case .planets: return showPlanets
        case .sun: return showSun
        case .moon: return showMoon
        case .galaxies: return showGalaxies
        case .clusters: return showClusters
        case .nebulae: return showNebulae
        case .constellations: return showConstellations
        }
    }
}

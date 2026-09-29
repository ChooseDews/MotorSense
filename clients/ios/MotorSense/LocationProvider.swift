import Combine
import CoreLocation
import Foundation
import MapKit

struct GPSFix: Equatable, Sendable {
    var latitude: Double
    var longitude: Double
    var elevation: Double
    var accuracy: Double
    var timestamp: Date
}

@MainActor
final class LocationProvider: NSObject, ObservableObject {
    @Published private(set) var fix: GPSFix?
    @Published private(set) var placeName: String?
    @Published private(set) var authorization = CLAuthorizationStatus.notDetermined
    @Published private(set) var statusText = "Location off"

    private let manager = CLLocationManager()
    private var lastGeocoded: GPSFix?
    private var geocodeTask: Task<Void, Never>?

    override init() {
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyNearestTenMeters
        manager.distanceFilter = 8
        authorization = manager.authorizationStatus
        restoreCachedLocation()
        updateStatus()
    }

    func start() {
        authorization = manager.authorizationStatus
        switch authorization {
        case .notDetermined:
            statusText = "Requesting location"
            manager.requestWhenInUseAuthorization()
        case .authorizedWhenInUse, .authorizedAlways:
            manager.startUpdatingLocation()
            statusText = fix == nil ? "Acquiring GPS" : formattedFix
        case .denied, .restricted:
            statusText = "Location denied"
        @unknown default:
            statusText = "Location unavailable"
        }
    }

    var coordinateText: String {
        guard let fix else { return statusText }
        return "\(Self.format(fix.latitude, positive: "N", negative: "S"))  \(Self.format(fix.longitude, positive: "E", negative: "W"))"
    }

    var placeText: String {
        placeName ?? statusText
    }

    func displayText(showCoordinates: Bool) -> String {
        if showCoordinates || placeName == nil {
            return coordinateText
        }
        return placeName ?? coordinateText
    }

    var elevationText: String {
        guard let fix else { return "—" }
        return String(format: "%.0f m", fix.elevation)
    }

    var formattedFix: String {
        guard fix != nil else { return statusText }
        return "\(coordinateText)  ·  \(elevationText)"
    }

    private static func format(_ value: Double, positive: String, negative: String) -> String {
        String(format: "%.4f°%@", abs(value), value >= 0 ? positive : negative)
    }

    private func apply(_ next: GPSFix) {
        fix = next
        persist(next)
        updateStatus()
        reverseGeocodeIfNeeded(next)
    }

    private func restoreCachedLocation() {
        let defaults = UserDefaults.standard
        guard defaults.object(forKey: "location.lat") != nil else { return }
        let cached = GPSFix(
            latitude: defaults.double(forKey: "location.lat"),
            longitude: defaults.double(forKey: "location.lon"),
            elevation: defaults.double(forKey: "location.elev"),
            accuracy: defaults.double(forKey: "location.acc"),
            timestamp: defaults.object(forKey: "location.time") as? Date ?? Date()
        )
        fix = cached
        lastGeocoded = cached
        placeName = defaults.string(forKey: "location.place")
    }

    private func persist(_ fix: GPSFix) {
        let defaults = UserDefaults.standard
        defaults.set(fix.latitude, forKey: "location.lat")
        defaults.set(fix.longitude, forKey: "location.lon")
        defaults.set(fix.elevation, forKey: "location.elev")
        defaults.set(fix.accuracy, forKey: "location.acc")
        defaults.set(fix.timestamp, forKey: "location.time")
    }

    private func persistPlaceName(_ name: String) {
        UserDefaults.standard.set(name, forKey: "location.place")
    }

    private func reverseGeocodeIfNeeded(_ fix: GPSFix) {
        if let last = lastGeocoded {
            let lastLocation = CLLocation(latitude: last.latitude, longitude: last.longitude)
            let nextLocation = CLLocation(latitude: fix.latitude, longitude: fix.longitude)
            if lastLocation.distance(from: nextLocation) < 80 { return }
        }
        lastGeocoded = fix
        geocodeTask?.cancel()
        geocodeTask = Task {
            let location = CLLocation(latitude: fix.latitude, longitude: fix.longitude)
            guard let name = await Self.placeName(for: location) else { return }
            placeName = name
            persistPlaceName(name)
            updateStatus()
        }
    }

    private static func placeName(for location: CLLocation) async -> String? {
        guard let request = MKReverseGeocodingRequest(location: location) else { return nil }
        do {
            let items = try await request.mapItems
            guard let item = items.first else { return nil }
            if let short = item.address?.shortAddress, !short.isEmpty {
                return short
            }
            if let full = item.address?.fullAddress, !full.isEmpty {
                return full.split(separator: "\n").prefix(2).joined(separator: ", ")
            }
            return item.name
        } catch {
            return nil
        }
    }

    private func updateStatus() {
        switch authorization {
        case .authorizedWhenInUse, .authorizedAlways:
            statusText = fix == nil ? "Acquiring GPS" : formattedFix
        case .denied, .restricted:
            statusText = "Location denied"
        case .notDetermined:
            statusText = "Location off"
        @unknown default:
            statusText = "Location unavailable"
        }
    }
}

extension LocationProvider: CLLocationManagerDelegate {
    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        Task { @MainActor in
            authorization = status
            updateStatus()
            if status == .authorizedWhenInUse || status == .authorizedAlways {
                self.manager.startUpdatingLocation()
            }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else { return }
        let next = GPSFix(
            latitude: location.coordinate.latitude,
            longitude: location.coordinate.longitude,
            elevation: location.altitude,
            accuracy: location.horizontalAccuracy,
            timestamp: location.timestamp
        )
        Task { @MainActor in
            apply(next)
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor in
            if fix == nil {
                statusText = error.localizedDescription
            }
        }
    }
}

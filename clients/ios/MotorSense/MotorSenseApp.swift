import SwiftUI

@main
struct MotorSenseApp: App {
    @StateObject private var mount = MountController()
    @StateObject private var location = LocationProvider()
    @StateObject private var sky = PlanetariumController()
    @StateObject private var settings = AppSettings()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(mount)
                .environmentObject(location)
                .environmentObject(sky)
                .environmentObject(settings)
                .preferredColorScheme(.dark)
                .background(Palette.backgroundTop.ignoresSafeArea())
                .redModeAware(settings.redMode)
                .onAppear { location.start() }
        }
    }
}

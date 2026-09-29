import SwiftUI

struct RootView: View {
    @State private var selectedTab: Tab = .sky

    private enum Tab: Hashable {
        case sky
        case align
        case telescopeControl
        case settings
    }

    var body: some View {
        TabView(selection: $selectedTab) {
            PlanetariumView()
                .tabItem {
                    Label("Sky", systemImage: "sparkles")
                }
                .tag(Tab.sky)
            TelescopeAlignmentView()
                .tabItem {
                    Label("Align", systemImage: "iphone.gen3.radiowaves.left.and.right")
                }
                .tag(Tab.align)
            ContentView()
                .tabItem {
                    Label("Control", systemImage: "gyroscope")
                }
                .tag(Tab.telescopeControl)
            SettingsView()
                .tabItem {
                    Label("Settings", systemImage: "gearshape")
                }
                .tag(Tab.settings)
        }
        .toolbarBackground(.ultraThinMaterial, for: .tabBar)
        .preferredColorScheme(.dark)
    }
}

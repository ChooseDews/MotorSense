import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var settings: AppSettings
    @EnvironmentObject private var location: LocationProvider
    @EnvironmentObject private var mount: MountController
    @State private var showZeroDialog = false
    @State private var zeroTarget: AxisName?

    var body: some View {
        NavigationStack {
            ZStack {
                Palette.background.ignoresSafeArea()
                List {
                    Section("Display") {
                        Toggle(isOn: $settings.redMode) {
                            settingLabel("Red mode", subtitle: "Night-vision friendly red tint", icon: "moon.fill")
                        }
                        .tint(Palette.bad)
                        Toggle(isOn: $settings.keepScreenOn) {
                            settingLabel("Keep screen on", subtitle: "Disable auto-lock while observing", icon: "sun.max.fill")
                        }
                        .tint(Palette.yaw)
                    }

                    Section("Location") {
                        Toggle(isOn: $settings.showCoordinates) {
                            settingLabel("Show lat / lng", subtitle: "Use coordinates instead of the place name", icon: "globe")
                        }
                        .tint(Palette.yaw)
                        HStack {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(location.placeText)
                                    .foregroundStyle(Palette.text)
                                Text(location.coordinateText)
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(Palette.muted)
                            }
                            Spacer()
                        }
                    }

                    Section("Sky layers") {
                        ForEach(SkyLayer.allCases) { layer in
                            Toggle(isOn: binding(for: layer)) {
                                settingLabel(layer.title, subtitle: layer.subtitle, icon: layer.icon)
                            }
                            .tint(Palette.yaw)
                        }
                    }

                    Section("Object size") {
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Text("Stars")
                                Spacer()
                                Text(String(format: "%.1f×", settings.starSize))
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(Palette.muted)
                            }
                            Slider(value: $settings.starSize, in: 0.35...2.5, step: 0.05)
                                .tint(Palette.yaw)
                            HStack {
                                Text("Planets")
                                Spacer()
                                Text(String(format: "%.1f×", settings.planetSize))
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(Palette.muted)
                            }
                            Slider(value: $settings.planetSize, in: 0.35...2.5, step: 0.05)
                                .tint(Palette.pitch)
                        }
                    }

                    Section("Controls") {
                        Toggle(isOn: $settings.hapticsEnabled) {
                            settingLabel("Haptics", subtitle: "Tactile feedback on telescope nudges", icon: "iphone.radiowaves.left.and.right")
                        }
                        .tint(Palette.yaw)
                    }

                    Section("Telescope Control") {
                        LabeledContent("Yaw") {
                            Text(mount.yaw.connected ? mount.yaw.deviceName ?? "Connected" : "Not connected")
                                .foregroundStyle(mount.yaw.connected ? Palette.good : Palette.muted)
                        }
                        LabeledContent("Pitch") {
                            Text(mount.pitch.connected ? mount.pitch.deviceName ?? "Connected" : "Not connected")
                                .foregroundStyle(mount.pitch.connected ? Palette.good : Palette.muted)
                        }
                        LabeledContent("Lock") {
                            Text(mount.locked ? "On" : "Off")
                                .foregroundStyle(mount.locked ? Palette.warn : Palette.muted)
                        }
                        LabeledContent("Sidereal tracking") {
                            Text(mount.tracking ? "On" : "Off")
                                .foregroundStyle(mount.tracking ? Palette.good : Palette.muted)
                        }
                        LabeledContent("Zero axis") {
                            HStack(spacing: 8) {
                                zeroButton("Yaw", target: .yaw)
                                    .disabled(!mount.yaw.connected || mount.firmwareUpdate.active)
                                zeroButton("Pitch", target: .pitch)
                                    .disabled(!mount.pitch.connected || mount.firmwareUpdate.active)
                                zeroButton("Both", target: nil)
                                    .disabled((!mount.yaw.connected && !mount.pitch.connected) || mount.firmwareUpdate.active)
                            }
                        }
                    }

                    Section("Diagnostics") {
                        NavigationLink {
                            LogView()
                        } label: {
                            settingLabel("Command log", subtitle: "BLE commands and controller replies", icon: "terminal")
                        }
                    }

                    Section("Firmware") {
                        NavigationLink {
                            FirmwareUpdateView()
                        } label: {
                            settingLabel("Firmware update", subtitle: "Install a MotorSense .bin on Yaw or Pitch", icon: "arrow.down.doc")
                        }
                    }

                    Section("About") {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("MotorSense")
                                .font(.headline)
                                .foregroundStyle(Palette.text)
                            Text("A companion app for controlling and observing with a MotorSense telescope.")
                                .font(.subheadline)
                                .foregroundStyle(Palette.muted)
                        }
                        .padding(.vertical, 2)

                        VStack(alignment: .leading, spacing: 6) {
                            Text("Telescope BLE protocol")
                                .font(.headline)
                                .foregroundStyle(Palette.text)
                            Text("MotorSense is designed to work with the telescope Bluetooth Low Energy protocol documented in the project repository.")
                                .font(.subheadline)
                                .foregroundStyle(Palette.muted)
                        }
                        .padding(.vertical, 2)

                        Link(destination: URL(string: "https://github.com/ChooseDews/MotorSense")!) {
                            settingLabel("MotorSense on GitHub", subtitle: "Protocol documentation, source code, and updates", icon: "chevron.left.forwardslash.chevron.right")
                        }

                        Link(destination: URL(string: "https://johndews.com")!) {
                            settingLabel("John Dews", subtitle: "johndews.com", icon: "person.crop.circle")
                        }

                        LabeledContent("Star catalog", value: "Bundled, offline")
                        LabeledContent("Planets / Moon", value: "Computed on device")
                        LabeledContent("Place names", value: "Cached after first lookup")
                    }
                }
                .scrollContentBackground(.hidden)
                .confirmationDialog(zeroDialogTitle, isPresented: $showZeroDialog, titleVisibility: .visible) {
                    Button("Set current position as 0°") { mount.zeroAxis(zeroTarget) }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text("Stops motion, then sets the current position as the 0° reference. Works while locked, e.g. after a firmware update.")
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
        }
        .preferredColorScheme(.dark)
        .tint(Palette.yaw)
    }

    private var zeroDialogTitle: String {
        switch zeroTarget {
        case .yaw: "Zero Yaw axis"
        case .pitch: "Zero Pitch axis"
        default: "Zero both axes"
        }
    }

    private func zeroButton(_ title: String, target: AxisName?) -> some View {
        Button {
            zeroTarget = target
            showZeroDialog = true
        } label: {
            Text(title)
                .font(.caption.weight(.semibold))
                .padding(.horizontal, 12)
                .padding(.vertical, 7)
                .background(Color.white.opacity(0.05), in: Capsule())
                .overlay(Capsule().stroke(Palette.cardStroke, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .foregroundStyle(Palette.text)
    }

    private func binding(for layer: SkyLayer) -> Binding<Bool> {
        switch layer {
        case .stars: return $settings.showStars
        case .planets: return $settings.showPlanets
        case .sun: return $settings.showSun
        case .moon: return $settings.showMoon
        case .galaxies: return $settings.showGalaxies
        case .clusters: return $settings.showClusters
        case .nebulae: return $settings.showNebulae
        case .constellations: return $settings.showConstellations
        }
    }

    private func settingLabel(_ title: String, subtitle: String, icon: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .frame(width: 28, height: 28)
                .foregroundStyle(Palette.yaw)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(Palette.muted)
            }
        }
    }
}

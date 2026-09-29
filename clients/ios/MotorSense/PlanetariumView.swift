import ARKit
import Combine
import SceneKit
import SwiftUI
import UIKit

struct PlanetariumView: View {
    @EnvironmentObject private var mount: MountController
    @EnvironmentObject private var location: LocationProvider
    @EnvironmentObject private var sky: PlanetariumController
    @EnvironmentObject private var settings: AppSettings
    @State private var query = ""
    @State private var showSearch = false
    @State private var showLayers = false
    @State private var catalogDate = Date()
    @StateObject private var attitude = SkyAttitude()

    private var observerLatitude: Double { location.fix?.latitude ?? mount.latitude }
    private var observerLongitude: Double { location.fix?.longitude ?? mount.longitude }
    private var clock: Date { sky.liveClock ? Date() : sky.simulatedDate }

    var body: some View {
        standardEvents
            .onChange(of: sky.arEnabled) { _, enabled in
                setAREnabled(enabled)
            }
            .onChange(of: sky.showCamera) { _, enabled in
                setCameraEnabled(enabled)
            }
            .onChange(of: attitude.look) { _, look in
                guard sky.arEnabled, !sky.showCamera, let look else { return }
                sky.applyAttitude(look)
            }
            .onDisappear(perform: stopAR)
    }

    private var standardEvents: some View {
        content
            .onAppear {
                refresh()
                if sky.arEnabled && !sky.showCamera {
                    attitude.start()
                }
            }
            .onChange(of: location.fix) { _, _ in refresh() }
            .onChange(of: mount.yaw.angle) { _, yaw in
                sky.syncTelescope(yaw: yaw, pitch: mount.pitch.angle)
            }
            .onChange(of: mount.pitch.angle) { _, pitch in
                sky.syncTelescope(yaw: mount.yaw.angle, pitch: pitch)
            }
            .onChange(of: sky.simulatedDate) { _, _ in
                if !sky.liveClock { refresh() }
            }
            .onChange(of: sky.showTelescope) { _, _ in refresh() }
            .onChange(of: settings.showStars) { _, _ in sky.applyDisplay(settings) }
            .onChange(of: settings.showPlanets) { _, _ in sky.applyDisplay(settings) }
            .onChange(of: settings.showSun) { _, _ in sky.applyDisplay(settings) }
            .onChange(of: settings.showMoon) { _, _ in sky.applyDisplay(settings) }
            .onChange(of: settings.showGalaxies) { _, _ in sky.applyDisplay(settings) }
            .onChange(of: settings.showClusters) { _, _ in sky.applyDisplay(settings) }
            .onChange(of: settings.showNebulae) { _, _ in sky.applyDisplay(settings) }
            .onChange(of: settings.showConstellations) { _, _ in sky.applyDisplay(settings) }
            .onChange(of: settings.starSize) { _, _ in sky.applyDisplay(settings) }
            .onChange(of: settings.planetSize) { _, _ in sky.applyDisplay(settings) }
            .onReceive(Timer.publish(every: 15, on: .main, in: .common).autoconnect()) { now in
                if sky.liveClock {
                    sky.simulatedDate = now
                    refresh()
                }
            }
    }

    private func setAREnabled(_ enabled: Bool) {
        guard enabled else {
            attitude.stop()
            sky.clearAROrientation()
            sky.setARBackground(false)
            sky.showCamera = false
            return
        }
        if sky.showCamera {
            attitude.stop()
        } else {
            attitude.start()
        }
    }

    private func setCameraEnabled(_ enabled: Bool) {
        sky.setARBackground(sky.arEnabled && enabled)
        guard sky.arEnabled else { return }
        if enabled {
            attitude.stop()
        } else {
            attitude.start()
        }
    }

    private func stopAR() {
        sky.arEnabled = false
        attitude.stop()
        sky.clearAROrientation()
        sky.showCamera = false
        sky.setARBackground(false)
    }

    private var content: some View {
        ZStack {
            Palette.background.ignoresSafeArea()
            PlanetariumSceneView(controller: sky, attitude: attitude) { target, center in
                sky.select(target, centerView: center && !sky.arEnabled)
                showSearch = false
            }
            .ignoresSafeArea()

            VStack(spacing: 10) {
                header
                Spacer(minLength: 0)
                    .allowsHitTesting(false)
                if let selected = sky.selected {
                    selectionCard(selected)
                }
                bottomBar
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 8)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showSearch) {
            searchSheet
        }
        .sheet(isPresented: $showLayers) {
            layersSheet
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("STAR CONTROL")
                        .font(.caption.weight(.semibold))
                        .tracking(2.2)
                        .foregroundStyle(Palette.muted)
                    Text(clock.formatted(date: .abbreviated, time: .shortened))
                        .font(.headline.weight(.semibold))
                }
                Spacer()
                Button {
                    showLayers = true
                } label: {
                    Image(systemName: "slider.horizontal.3")
                        .font(.body.weight(.semibold))
                        .frame(width: 38, height: 38)
                        .background(.ultraThinMaterial, in: Circle())
                }
                .foregroundStyle(Palette.text)
                Button {
                    showSearch = true
                } label: {
                    Image(systemName: "magnifyingglass")
                        .font(.body.weight(.semibold))
                        .frame(width: 38, height: 38)
                        .background(.ultraThinMaterial, in: Circle())
                }
                .foregroundStyle(Palette.text)
            }

            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) {
                    locationPill
                    pill("\(sky.visibleCount) up", icon: "sparkle")
                    pill(String(format: "FOV %.0f°", sky.fieldOfView), icon: "camera.viewfinder")
                    if sky.arEnabled {
                        pill(sky.showCamera ? "Camera AR" : (attitude.calibrated ? "Aligned" : "Point north"), icon: "location.north.line")
                    }
                }
                VStack(alignment: .leading, spacing: 6) {
                    locationPill
                    HStack(spacing: 8) {
                        pill("\(sky.visibleCount) up", icon: "sparkle")
                        pill(String(format: "FOV %.0f°", sky.fieldOfView), icon: "camera.viewfinder")
                    }
                }
            }
        }
        .padding(12)
        .glassCard(cornerRadius: 22)
    }

    private var bottomBar: some View {
        VStack(spacing: 10) {
            ViewThatFits(in: .horizontal) {
                bottomControls(compact: false)
                bottomControls(compact: true)
            }
            if !sky.liveClock {
                HStack(spacing: 10) {
                    DatePicker("", selection: $sky.simulatedDate)
                        .labelsHidden()
                        .colorScheme(.dark)
                    Button("Now") {
                        sky.simulatedDate = Date()
                        catalogDate = sky.simulatedDate
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Palette.yaw)
                }
                HStack {
                    Text("−12h")
                        .font(.caption2)
                        .foregroundStyle(Palette.muted)
                    Slider(value: timeOffset, in: -12...12, step: 0.25)
                        .tint(Palette.pitch)
                    Text("+12h")
                        .font(.caption2)
                        .foregroundStyle(Palette.muted)
                }
            }
            Text(skyStatusLine)
                .font(.caption2)
                .foregroundStyle(Palette.muted)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(12)
        .glassCard(cornerRadius: 22)
    }

    private func selectionCard(_ target: SkyTarget) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(target.name)
                        .font(.title3.weight(.semibold))
                    Text(target.subtitle)
                        .font(.caption)
                        .foregroundStyle(Palette.muted)
                }
                Spacer()
                Button {
                    sky.select(nil)
                } label: {
                    Image(systemName: "xmark")
                        .font(.caption.weight(.bold))
                        .frame(width: 28, height: 28)
                        .background(Color.white.opacity(0.08), in: Circle())
                }
                .foregroundStyle(Palette.muted)
            }
            HStack(spacing: 8) {
                metric("AZ", String(format: "%.1f°", target.azimuth))
                metric("ALT", String(format: "%.1f°", target.altitude))
                metric("RA", formatRA(target.ra))
                metric("DEC", formatDec(target.dec))
            }
            HStack {
                Text(target.isVisible ? "Visible now" : "Below horizon")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(target.isVisible ? Palette.good : Palette.warn)
                Spacer()
                if let mag = target.mag {
                    Text(String(format: "mag %.1f", mag))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(Palette.muted)
                }
            }
            Button {
                mount.gotoMount(yaw: target.azimuth, pitch: max(0, min(90, target.altitude)))
            } label: {
                HStack {
                    Image(systemName: mount.locked ? "lock.fill" : "scope")
                    Text(mount.locked ? "Telescope Control locked" : (target.isVisible ? "Slew telescope" : "Slew anyway"))
                }
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
                .background(target.isVisible ? Palette.yaw.opacity(0.28) : Palette.warn.opacity(0.2), in: Capsule())
            }
            .foregroundStyle(Palette.text)
            .disabled(mount.locked)
        }
        .padding(14)
        .glassCard(cornerRadius: 22, stroke: Palette.yaw.opacity(0.35))
        .padding(.bottom, 10)
    }

    private var searchSheet: some View {
        NavigationStack {
            List {
                Toggle("Above horizon only", isOn: $sky.visibleOnly)
                    .tint(Palette.yaw)
                ForEach(filteredTargets) { target in
                    Button {
                        sky.select(target, centerView: true)
                        showSearch = false
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(target.name)
                                    .foregroundStyle(Palette.text)
                                Text(target.subtitle)
                                    .font(.caption)
                                    .foregroundStyle(Palette.muted)
                            }
                            Spacer()
                            VStack(alignment: .trailing, spacing: 2) {
                                Text(target.isVisible ? "Up" : "Down")
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(target.isVisible ? Palette.good : Palette.warn)
                                Text(String(format: "alt %.0f°", target.altitude))
                                    .font(.caption2.monospacedDigit())
                                    .foregroundStyle(Palette.muted)
                            }
                        }
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(Palette.backgroundTop)
            .navigationTitle("Select target")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $query, prompt: "Stars, planets, Messier")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { showSearch = false }
                }
            }
        }
        .preferredColorScheme(.dark)
        .presentationDetents([.medium, .large])
    }

    private var layersSheet: some View {
        NavigationStack {
            List {
                Section("Show") {
                    ForEach(SkyLayer.allCases) { layer in
                        Toggle(isOn: layerBinding(layer)) {
                            Label {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(layer.title)
                                    Text(layer.subtitle)
                                        .font(.caption)
                                        .foregroundStyle(Palette.muted)
                                }
                            } icon: {
                                Image(systemName: layer.icon)
                                    .foregroundStyle(Palette.yaw)
                            }
                        }
                        .tint(Palette.yaw)
                    }
                }
                Section("Horizon") {
                    Toggle(isOn: mountainsBinding) {
                        Label {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Mountains")
                                Text("Simulated terrain around the horizon")
                                    .font(.caption)
                                    .foregroundStyle(Palette.muted)
                            }
                        } icon: {
                            Image(systemName: "mountain.2.fill")
                                .foregroundStyle(Palette.yaw)
                        }
                    }
                    .tint(Palette.yaw)
                    Toggle(isOn: telescopeLaserBinding) {
                        Label {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Telescope laser")
                                Text("Show where the telescope is pointing")
                                    .font(.caption)
                                    .foregroundStyle(Palette.muted)
                            }
                        } icon: {
                            Image(systemName: "scope")
                                .foregroundStyle(Palette.good)
                        }
                    }
                    .tint(Palette.good)
                }
                Section("Size") {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Text("Stars")
                            Spacer()
                            Text(String(format: "%.1f×", settings.starSize))
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(Palette.muted)
                        }
                        Slider(value: $settings.starSize, in: 0.35...2.5, step: 0.05)
                            .tint(Palette.yaw)
                    }
                    VStack(alignment: .leading, spacing: 8) {
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
            }
            .scrollContentBackground(.hidden)
            .background(Palette.backgroundTop)
            .navigationTitle("Sky layers")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { showLayers = false }
                }
            }
        }
        .preferredColorScheme(.dark)
        .presentationDetents([.medium, .large])
    }

    private func layerBinding(_ layer: SkyLayer) -> Binding<Bool> {
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

    private var mountainsBinding: Binding<Bool> {
        Binding(
            get: { settings.showMountains },
            set: { visible in
                settings.showMountains = visible
                sky.applyDisplay(settings)
            }
        )
    }

    private var telescopeLaserBinding: Binding<Bool> {
        Binding(
            get: { settings.showTelescopeLaser },
            set: { visible in
                settings.showTelescopeLaser = visible
                sky.applyDisplay(settings)
            }
        )
    }

    private var filteredTargets: [SkyTarget] {
        let lst = SkyMath.lstDegrees(date: clock, longitude: observerLongitude)
        let items = SkyCatalog.shared.targets(at: clock).map { $0.placed(latitude: observerLatitude, lst: lst) }
        let layered = items.filter(settings.shows)
        let visible = sky.visibleOnly ? layered.filter(\.isVisible) : layered
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let searched = q.isEmpty ? visible : visible.filter { $0.searchText.contains(q) }
        return searched.sorted { lhs, rhs in
            if lhs.kind.sortRank != rhs.kind.sortRank { return lhs.kind.sortRank < rhs.kind.sortRank }
            return (lhs.mag ?? 99) < (rhs.mag ?? 99)
        }
    }

    private var timeOffset: Binding<Double> {
        Binding(
            get: { sky.simulatedDate.timeIntervalSince(catalogDate) / 3600 },
            set: { sky.simulatedDate = catalogDate.addingTimeInterval($0 * 3600) }
        )
    }

    private func refresh() {
        if sky.liveClock {
            catalogDate = Date()
            sky.simulatedDate = catalogDate
        }
        sky.apply(
            latitude: observerLatitude,
            longitude: observerLongitude,
            date: clock,
            yaw: mount.yaw.angle,
            pitch: mount.pitch.angle,
            settings: settings
        )
    }

    private var skyStatusLine: String {
        var parts = [String(format: "Az %.0f°  ·  Alt %.0f°  ·  %d named stars above horizon", sky.lookAzimuth, sky.lookAltitude, sky.namedVisibleCount)]
        if sky.arEnabled && !sky.showCamera && !attitude.calibrated {
            parts.append("Point north and tap Set N to align compass")
        }
        if sky.showCamera && !ARWorldTrackingConfiguration.isSupported {
            parts.append("Camera AR unavailable on this device")
        }
        if let cameraError = sky.cameraError {
            parts.append(cameraError)
        }
        if mount.locked {
            parts.append("locked")
        } else if mount.tracking {
            parts.append("tracking")
        }
        return parts.joined(separator: "  ·  ")
    }

    private var locationPill: some View {
        Button {
            if location.placeName != nil {
                settings.showCoordinates.toggle()
            }
        } label: {
            pill(location.fix == nil ? location.statusText : location.displayText(showCoordinates: settings.showCoordinates), icon: "location.fill")
        }
        .buttonStyle(.plain)
    }

    private func pill(_ text: String, icon: String) -> some View {
        HStack(spacing: 5) {
            Image(systemName: icon)
            Text(text)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .font(.caption2.weight(.semibold))
        .foregroundStyle(Palette.text)
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(Color.white.opacity(0.06), in: Capsule())
    }

    private func bottomControls(compact: Bool) -> some View {
        let lookRow = HStack(spacing: 8) {
            if sky.arEnabled && !sky.showCamera {
                miniButton("location.north.line", title: "Set N", selected: attitude.calibrated) {
                    attitude.alignHeading(to: 0)
                }
            } else if !sky.arEnabled {
                miniButton("location.north.line", title: "N") { sky.lookNorth() }
                miniButton("arrow.up.to.line", title: "Zenith") { sky.lookZenith() }
            }
            miniButton("arkit", title: "AR", selected: sky.arEnabled) {
                sky.arEnabled.toggle()
            }
            .disabled(!attitude.available)
            .opacity(attitude.available ? 1 : 0.42)
            if sky.arEnabled {
                miniButton("camera.fill", title: "Camera", selected: sky.showCamera) {
                    sky.showCamera.toggle()
                }
                .disabled(!ARWorldTrackingConfiguration.isSupported)
                .opacity(ARWorldTrackingConfiguration.isSupported ? 1 : 0.42)
            }
            clockModeControl
            if !compact { Spacer(minLength: 0) }
        }
        let scopeRow = HStack(spacing: 8) {
            if sky.showTelescope {
                miniButton("move.3d", title: "Place") {
                    sky.placeTelescopeInFront()
                }
            }
            miniButton("scope", title: sky.showTelescope ? "Hide" : "Scope") {
                sky.showTelescope.toggle()
                if sky.showTelescope {
                    sky.placeTelescopeInFront()
                }
            }
            miniButton(mount.locked ? "lock.fill" : "lock.open", title: mount.locked ? "Unlock" : "Lock", selected: mount.locked) {
                mount.toggleLocked()
            }
            miniButton("arrow.triangle.2.circlepath", title: mount.tracking ? "Tracking" : "Track", selected: mount.tracking) {
                mount.toggleTracking()
            }
            .disabled(mount.locked || !(mount.yaw.connected && mount.pitch.connected))
            .opacity((mount.locked || !(mount.yaw.connected && mount.pitch.connected)) ? 0.42 : 1)
            if compact { Spacer(minLength: 0) }
        }
        return Group {
            if compact {
                VStack(alignment: .leading, spacing: 8) {
                    lookRow
                    scopeRow
                }
            } else {
                HStack(spacing: 8) {
                    lookRow
                    scopeRow
                }
            }
        }
    }

    private var clockModeControl: some View {
        HStack(spacing: 0) {
            clockModeButton("Live", selected: sky.liveClock) {
                sky.setLiveClock(true)
                refresh()
            }
            clockModeButton("Custom", selected: !sky.liveClock) {
                if sky.liveClock {
                    catalogDate = Date()
                    sky.simulatedDate = catalogDate
                }
                sky.setLiveClock(false)
                refresh()
            }
        }
        .background(Color.white.opacity(0.06), in: Capsule())
    }

    private func clockModeButton(_ title: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.caption.weight(.semibold))
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(selected ? Palette.yaw.opacity(0.28) : Color.clear, in: Capsule())
        }
        .foregroundStyle(selected ? Palette.text : Palette.muted)
        .buttonStyle(.plain)
    }

    private func miniButton(_ icon: String, title: String, selected: Bool = false, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: icon)
                .font(.caption.weight(.semibold))
                .padding(.horizontal, 10)
                .padding(.vertical, 8)
                .background(selected ? Palette.yaw.opacity(0.28) : Color.white.opacity(0.08), in: Capsule())
        }
        .foregroundStyle(Palette.text)
    }

    private func metric(_ title: String, _ value: String) -> some View {
        VStack(spacing: 2) {
            Text(title)
                .font(.caption2.weight(.bold))
                .tracking(0.8)
                .foregroundStyle(Palette.muted)
            Text(value)
                .font(.caption.weight(.semibold).monospacedDigit())
                .foregroundStyle(Palette.text)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(Color.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private func formatRA(_ degrees: Double) -> String {
        let hours = wrapDegrees(degrees) / 15
        let h = Int(hours)
        let m = Int((hours - Double(h)) * 60)
        return String(format: "%dh%02dm", h, m)
    }

    private func formatDec(_ degrees: Double) -> String {
        String(format: "%+.1f°", degrees)
    }
}

private struct PlanetariumSceneView: UIViewRepresentable {
    let controller: PlanetariumController
    let attitude: SkyAttitude
    var onSelect: (SkyTarget?, Bool) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(controller: controller, attitude: attitude, onSelect: onSelect)
    }

    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView()
        view.scene = controller.scene
        view.pointOfView = controller.scene.pointOfView
        view.automaticallyUpdatesLighting = false
        view.autoenablesDefaultLighting = false
        view.isOpaque = false
        view.rendersCameraGrain = false
        view.rendersMotionBlur = false
        view.backgroundColor = .clear
        view.antialiasingMode = .multisampling2X
        view.isPlaying = true
        let pan = UIPanGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.handlePan))
        let pinch = UIPinchGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.handlePinch))
        let tap = UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.handleTap))
        let doubleTap = UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.handleDoubleTap))
        doubleTap.numberOfTapsRequired = 2
        tap.require(toFail: doubleTap)
        view.addGestureRecognizer(pan)
        view.addGestureRecognizer(pinch)
        view.addGestureRecognizer(doubleTap)
        view.addGestureRecognizer(tap)
        context.coordinator.view = view
        context.coordinator.syncSession(on: view)
        return view
    }

    func updateUIView(_ uiView: ARSCNView, context: Context) {
        context.coordinator.controller = controller
        context.coordinator.attitude = attitude
        context.coordinator.onSelect = onSelect
        uiView.pointOfView = controller.scene.pointOfView
        context.coordinator.syncSession(on: uiView)
    }

    static func dismantleUIView(_ uiView: ARSCNView, coordinator: Coordinator) {
        uiView.session.pause()
        coordinator.sessionRunning = false
    }

    final class Coordinator: NSObject, ARSessionDelegate {
        var controller: PlanetariumController
        var attitude: SkyAttitude
        var onSelect: (SkyTarget?, Bool) -> Void
        weak var view: ARSCNView?
        var sessionRunning = false

        init(controller: PlanetariumController, attitude: SkyAttitude, onSelect: @escaping (SkyTarget?, Bool) -> Void) {
            self.controller = controller
            self.attitude = attitude
            self.onSelect = onSelect
        }

        func syncSession(on view: ARSCNView) {
            let wantsCamera = controller.arEnabled && controller.showCamera && ARWorldTrackingConfiguration.isSupported
            guard wantsCamera != sessionRunning else { return }
            if wantsCamera {
                let configuration = ARWorldTrackingConfiguration()
                configuration.worldAlignment = .gravityAndHeading
                configuration.planeDetection = []
                if let format = ARWorldTrackingConfiguration.supportedVideoFormats.first(where: { $0.framesPerSecond == 30 })
                    ?? ARWorldTrackingConfiguration.supportedVideoFormats.first {
                    configuration.videoFormat = format
                }
                view.session.delegate = self
                controller.cameraError = nil
                view.session.run(configuration, options: [.resetTracking, .removeExistingAnchors])
                sessionRunning = true
            } else {
                view.session.pause()
                sessionRunning = false
            }
        }

        func session(_ session: ARSession, didUpdate frame: ARFrame) {
            let camera = frame.camera
            let viewport = view?.bounds.size ?? CGSize(width: camera.imageResolution.width, height: camera.imageResolution.height)
            let interfaceOrientation = view?.window?.windowScene?.effectiveGeometry.interfaceOrientation ?? .portrait
            let fov = cameraVerticalFieldOfView(camera, viewport: viewport, orientation: interfaceOrientation)
            let orientation = SkyAttitude.skyOrientation(from: camera.transform)
            let look = SkyMath.lookAngles(from: orientation.forward)
            Task { @MainActor in
                guard self.controller.arEnabled, self.controller.showCamera else { return }
                self.attitude.calibrated = true
                self.controller.fieldOfView = fov
                self.controller.applyAttitude(SkyLook(
                    azimuth: look.azimuth,
                    altitude: look.altitude,
                    roll: orientation.roll
                ))
            }
        }

        func session(_ session: ARSession, didFailWithError error: Error) {
            Task { @MainActor in
                self.controller.cameraError = "Camera unavailable: \(error.localizedDescription)"
                self.controller.showCamera = false
            }
        }

        private func cameraVerticalFieldOfView(_ camera: ARCamera, viewport: CGSize, orientation: UIInterfaceOrientation) -> Double {
            let projection = camera.projectionMatrix(for: orientation, viewportSize: viewport, zNear: 0.01, zFar: 40)
            let yScale = Double(projection.columns.1.y)
            guard yScale > 0.01 else { return controller.fieldOfView }
            return min(110, max(18, 2 * atan(1 / yScale) / SkyMath.deg))
        }

        @objc func handlePan(_ gesture: UIPanGestureRecognizer) {
            guard !controller.arEnabled else { return }
            let translation = gesture.translation(in: gesture.view)
            let scale = controller.fieldOfView / 240
            controller.pan(azimuthDelta: -Double(translation.x) * scale, altitudeDelta: Double(translation.y) * scale)
            gesture.setTranslation(.zero, in: gesture.view)
        }

        @objc func handlePinch(_ gesture: UIPinchGestureRecognizer) {
            guard !controller.arEnabled else { return }
            controller.zoom(Double(gesture.scale))
            gesture.scale = 1
        }

        @objc func handleTap(_ gesture: UITapGestureRecognizer) {
            guard let view else { return }
            let point = gesture.location(in: view)
            onSelect(controller.scene.pick(at: point, in: view, allowSkyPoint: false), !controller.arEnabled)
        }

        @objc func handleDoubleTap(_ gesture: UITapGestureRecognizer) {
            guard let view else { return }
            let point = gesture.location(in: view)
            onSelect(controller.scene.pick(at: point, in: view, allowSkyPoint: true), false)
        }
    }
}

private extension SkyObjectKind {
    var sortRank: Int {
        switch self {
        case .sun: return 0
        case .moon: return 1
        case .planet: return 2
        case .star: return 3
        case .dso: return 4
        case .constellation: return 5
        case .sky: return 6
        }
    }
}

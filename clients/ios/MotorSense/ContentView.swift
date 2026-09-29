import SwiftUI
import UIKit

struct ContentView: View {
    @EnvironmentObject private var mount: MountController
    @EnvironmentObject private var location: LocationProvider
    @EnvironmentObject private var settings: AppSettings
    @State private var step = 1.0
    @State private var duty = 70
    @State private var showZeroDialog = false
    @State private var showLog = false
    private let steps = [0.25, 1.0, 5.0, 15.0]

    var body: some View {
        ZStack {
            Palette.background.ignoresSafeArea()
            ambientGlow
            Starfield()
                .opacity(0.55)
                .ignoresSafeArea()
                .allowsHitTesting(false)

            GeometryReader { geo in
                let landscape = geo.size.width > geo.size.height && geo.size.width >= 700
                Group {
                    if landscape {
                        landscapeBody
                    } else {
                        portraitBody
                    }
                }
                .frame(width: geo.size.width, height: geo.size.height)
            }
        }
        .preferredColorScheme(.dark)
        .onAppear { location.start() }
        .onChange(of: location.fix) { _, fix in
            if let fix { mount.updateLocation(fix) }
        }
        .sheet(isPresented: $showLog) {
            NavigationStack {
                LogView()
            }
            .environmentObject(mount)
        }
    }

    private var portraitBody: some View {
        VStack(spacing: 14) {
            header
            telescopeCard(expands: false)
            axisReadouts
            gpsCard
            Spacer(minLength: 2)
            controlCluster
            stopButton
            footer
        }
        .padding(.horizontal, 20)
        .padding(.top, 10)
        .padding(.bottom, 10)
    }

    private var landscapeBody: some View {
        HStack(alignment: .top, spacing: 16) {
            VStack(spacing: 12) {
                header
                telescopeCard(expands: true)
                axisReadouts
                gpsCard
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            VStack(spacing: 12) {
                controlCluster
                stopButton
                footer
                Spacer(minLength: 0)
            }
            .frame(maxWidth: 420)
        }
        .padding(.horizontal, 20)
        .padding(.top, 10)
        .padding(.bottom, 10)
    }

    private var ambientGlow: some View {
        ZStack {
            RadialGradient(
                colors: [Palette.yaw.opacity(0.16), .clear],
                center: .topTrailing,
                startRadius: 10,
                endRadius: 280
            )
            RadialGradient(
                colors: [Palette.pitch.opacity(0.14), .clear],
                center: .bottomLeading,
                startRadius: 20,
                endRadius: 320
            )
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                Text("MOTOR SENSE")
                    .font(.caption.weight(.semibold))
                    .tracking(2.4)
                    .foregroundStyle(Palette.muted)
                HStack(spacing: 8) {
                    Circle()
                        .fill(statusColor)
                        .frame(width: 8, height: 8)
                        .shadow(color: statusColor.opacity(0.8), radius: 6)
                    Text(statusText)
                        .font(.title3.weight(.semibold))
                }
            }
            Spacer()
            Button {
                showLog = true
            } label: {
                Image(systemName: "terminal")
                    .font(.subheadline.weight(.semibold))
                    .frame(width: 42, height: 44)
                    .background(Color.white.opacity(0.10), in: Capsule())
                    .overlay(Capsule().stroke(Color.white.opacity(0.12), lineWidth: 1))
            }
            .foregroundStyle(.white)
            .accessibilityLabel("Command log")
            Button(action: mount.connectControllers) {
                HStack(spacing: 8) {
                    if connecting {
                        ProgressView().tint(.white)
                    } else {
                        Image(systemName: connectedHealthy ? "dot.radiowaves.left.and.right" : "antenna.radiowaves.left.and.right")
                    }
                    Text(connectTitle)
                        .font(.subheadline.weight(.semibold))
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 11)
                .background(connectBackground, in: Capsule())
                .overlay(Capsule().stroke(Color.white.opacity(0.12), lineWidth: 1))
            }
            .foregroundStyle(.white)
            .disabled(connecting)
        }
    }

    private func telescopeCard(expands: Bool) -> some View {
        TelescopeView(
            yaw: mount.yaw.angle,
            pitch: mount.pitch.angle,
            targetYaw: mount.targetYaw,
            targetPitch: mount.targetPitch,
            yawConnected: mount.yaw.connected,
            pitchConnected: mount.pitch.connected
        )
        .frame(minHeight: 180)
        .frame(maxHeight: expands ? .infinity : 280)
        .glassCard(cornerRadius: 28, stroke: Color.white.opacity(0.08))
        .clipShape(RoundedRectangle(cornerRadius: 28, style: .continuous))
    }

    private var axisReadouts: some View {
        HStack(spacing: 10) {
            AxisCard(axis: mount.yaw, target: mount.targetYaw, tint: Palette.yaw)
            AxisCard(axis: mount.pitch, target: mount.targetPitch, tint: Palette.pitch)
        }
    }

    private var gpsCard: some View {
        HStack(spacing: 12) {
            Image(systemName: "location.fill")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(location.fix == nil ? Palette.muted : Palette.good)
                .frame(width: 28, height: 28)
                .background(Color.white.opacity(0.06), in: Circle())
            VStack(alignment: .leading, spacing: 2) {
                Text(location.fix == nil ? location.statusText : location.displayText(showCoordinates: settings.showCoordinates))
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(Palette.text)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                Text(location.fix == nil ? "Observer location" : "Alt \(location.elevationText)")
                    .font(.caption2)
                    .foregroundStyle(Palette.muted)
            }
            Spacer(minLength: 0)
            if location.fix != nil, location.placeName != nil {
                Button {
                    settings.showCoordinates.toggle()
                } label: {
                    Image(systemName: settings.showCoordinates ? "text.format" : "globe")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Palette.muted)
                        .frame(width: 28, height: 28)
                        .background(Color.white.opacity(0.06), in: Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(settings.showCoordinates ? "Show place name" : "Show coordinates")
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .glassCard(cornerRadius: 18)
    }

    private var controlCluster: some View {
        VStack(spacing: 16) {
            if !connected {
                Label(
                    disconnectedControlMessage,
                    systemImage: connecting ? "antenna.radiowaves.left.and.right" : "antenna.radiowaves.left.and.right.slash"
                )
                .font(.caption.weight(.semibold))
                .foregroundStyle(connecting ? Palette.warn : Palette.muted)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 9)
                .background(Color.white.opacity(0.05), in: Capsule())
            }
            mountModeRow
                .disabled(!connected)
                .opacity(connected ? 1 : 0.35)
            DPad(
                step: step,
                enabled: connected && !mount.locked,
                onNudge: { yaw, pitch in nudge(yaw: yaw, pitch: pitch) },
                onHome: home
            )
            stepPicker
                .disabled(!connected)
                .opacity(connected ? 1 : 0.35)
            dutyControl
                .disabled(!connected)
                .opacity(connected ? 1 : 0.35)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .glassCard(cornerRadius: 28)
    }

    private var mountModeRow: some View {
        HStack(spacing: 8) {
            modeButton(
                title: mount.locked ? "Locked" : "Lock",
                icon: mount.locked ? "lock.fill" : "lock.open",
                selected: mount.locked,
                tint: Palette.warn
            ) {
                if settings.hapticsEnabled {
                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                }
                mount.toggleLocked()
            }
            modeButton(
                title: mount.tracking ? "Tracking" : "Track",
                icon: mount.tracking ? "location.north.line.fill" : "location.north.line",
                selected: mount.tracking,
                tint: Palette.good
            ) {
                if settings.hapticsEnabled {
                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                }
                mount.toggleTracking()
            }
            .disabled(!connected || mount.locked)
            .opacity((!connected || mount.locked) ? 0.42 : 1)
            modeButton(
                title: "Zero",
                icon: "0.circle",
                selected: false,
                tint: Palette.yaw
            ) {
                if settings.hapticsEnabled {
                    UISelectionFeedbackGenerator().selectionChanged()
                }
                showZeroDialog = true
            }
            .disabled(!anyAxisConnected || mount.firmwareUpdate.active)
            .opacity((!anyAxisConnected || mount.firmwareUpdate.active) ? 0.42 : 1)
        }
        .confirmationDialog(
            "Zero axes",
            isPresented: $showZeroDialog,
            titleVisibility: .visible
        ) {
            Button("Zero Yaw: current position as 0°") { mount.zeroAxis(.yaw) }
                .disabled(!mount.yaw.connected)
            Button("Zero Pitch: current position as 0°") { mount.zeroAxis(.pitch) }
                .disabled(!mount.pitch.connected)
            Button("Zero both axes") { mount.zeroAxis(nil) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Stops motion, then sets the current position as the 0° reference. Do this with the telescope aimed at a known reference.")
        }
    }

    private func modeButton(title: String, icon: String, selected: Bool, tint: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: icon)
                .font(.caption.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
                .background(selected ? tint.opacity(0.22) : Color.white.opacity(0.04), in: Capsule())
                .overlay(Capsule().stroke(selected ? tint.opacity(0.6) : Palette.cardStroke, lineWidth: 1))
        }
        .foregroundStyle(selected ? Palette.text : Palette.muted)
        .buttonStyle(PressableStyle())
    }

    private var stepPicker: some View {
        HStack(spacing: 8) {
            ForEach(steps, id: \.self) { value in
                Button {
                    step = value
                    if settings.hapticsEnabled {
                        UISelectionFeedbackGenerator().selectionChanged()
                    }
                } label: {
                    Text(String(format: "%g°", value))
                        .font(.subheadline.weight(.semibold).monospacedDigit())
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 9)
                        .background(step == value ? Palette.yaw.opacity(0.22) : Color.white.opacity(0.04), in: Capsule())
                        .overlay(
                            Capsule().stroke(step == value ? Palette.yaw.opacity(0.55) : Palette.cardStroke, lineWidth: 1)
                        )
                }
                .foregroundStyle(step == value ? Palette.text : Palette.muted)
            }
        }
    }

    private var dutyControl: some View {
        HStack(spacing: 12) {
            Text("DUTY")
                .font(.caption.weight(.semibold))
                .tracking(1.2)
                .foregroundStyle(Palette.muted)
            Slider(value: dutyBinding, in: 10...100, step: 5)
                .tint(Palette.yaw)
            Text("\(duty)%")
                .font(.subheadline.weight(.semibold).monospacedDigit())
                .foregroundStyle(Palette.text)
                .frame(width: 48, alignment: .trailing)
        }
    }

    private var stopButton: some View {
        Button(action: mount.stop) {
            HStack(spacing: 10) {
                Image(systemName: "stop.fill")
                Text("STOP")
            }
            .font(.title3.weight(.bold))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
            .background(
                LinearGradient(colors: [Palette.bad, Palette.bad.opacity(0.82)], startPoint: .top, endPoint: .bottom),
                in: RoundedRectangle(cornerRadius: 20, style: .continuous)
            )
            .shadow(color: Palette.bad.opacity(0.35), radius: 16, y: 6)
        }
        .foregroundStyle(.white)
        .buttonStyle(PressableStyle())
        .disabled(!anyAxisConnected)
        .opacity(anyAxisConnected ? 1 : 0.35)
    }

    private var footer: some View {
        Text(mount.logLines.last?.text ?? mount.lastCommand)
            .font(.caption)
            .foregroundStyle(Palette.muted)
            .lineLimit(1)
            .frame(maxWidth: .infinity)
    }

    private var connected: Bool { mount.yaw.connected && mount.pitch.connected }
    private var connectedHealthy: Bool { mount.yaw.healthy && mount.pitch.healthy }
    private var connecting: Bool { mount.scanning || mount.yaw.connecting || mount.pitch.connecting }
    private var anyAxisConnected: Bool { mount.yaw.connected || mount.pitch.connected }

    private var disconnectedControlMessage: String {
        if connecting { return "Connecting Telescope Control…" }
        if anyAxisConnected { return "Connect both axes to enable movement" }
        return "Connect Telescope Control to enable movement"
    }

    private var connectTitle: String {
        if connectedHealthy { return "Live" }
        if connecting { return "Linking" }
        return "Connect"
    }

    private var connectBackground: Color {
        if connectedHealthy { return Palette.good.opacity(0.78) }
        if connecting { return Palette.warn.opacity(0.85) }
        return Color.white.opacity(0.10)
    }

    private var statusText: String {
        if mount.locked { return "Telescope Control locked" }
        if mount.tracking { return "Sidereal tracking" }
        if connectedHealthy { return "Telescope Control ready" }
        if connecting { return "Finding axes" }
        return "Telescope Control not connected"
    }

    private var statusColor: Color {
        if mount.locked { return Palette.warn }
        if mount.tracking { return Palette.yaw }
        if connectedHealthy { return Palette.good }
        if connecting { return Palette.warn }
        return Palette.bad
    }

    private var dutyBinding: Binding<Double> {
        Binding(get: { Double(duty) }, set: { duty = Int($0) })
    }

    private func nudge(yaw: Double = 0, pitch: Double = 0) {
        if settings.hapticsEnabled {
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        }
        mount.moveRelative(yawDelta: yaw, pitchDelta: pitch, duty: duty)
    }

    private func home() {
        if settings.hapticsEnabled {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        }
        mount.home()
    }
}

private struct AxisCard: View {
    let axis: AxisSnapshot
    let target: Double?
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(axis.name.title.uppercased())
                    .font(.caption2.weight(.bold))
                    .tracking(1.2)
                    .foregroundStyle(tint)
                Spacer()
                Circle()
                    .fill(statusColor)
                    .frame(width: 7, height: 7)
                Text(axisStatusText)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(Palette.muted)
            }
            Text(axis.connected ? String(format: "%.2f°", axis.angle) : "—")
                .font(.system(size: 22, weight: .semibold, design: .rounded).monospacedDigit())
                .foregroundStyle(Palette.text)
                .minimumScaleFactor(0.7)
                .lineLimit(1)
            Text(axis.connected ? (target.map { String(format: "→ %.1f°", $0) } ?? "No target") : "Connect to view position")
                .font(.caption2.weight(.medium))
                .foregroundStyle(Palette.muted)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .glassCard(cornerRadius: 18, stroke: tint.opacity(0.28))
    }

    private var statusColor: Color {
        if axis.healthy { return Palette.good }
        if axis.connecting { return Palette.warn }
        return Palette.bad
    }

    private var axisStatusText: String {
        if axis.connecting { return "CONNECTING" }
        if !axis.connected { return "NOT CONNECTED" }
        if !axis.healthy { return "NO SIGNAL" }
        return axis.motionState
    }
}

private struct DPad: View {
    let step: Double
    let enabled: Bool
    let onNudge: (Double, Double) -> Void
    let onHome: () -> Void

    var body: some View {
        VStack(spacing: 10) {
            padKey("chevron.up", tint: Palette.pitch) { onNudge(0, step) }
            HStack(spacing: 8) {
                padKey("chevron.left", tint: Palette.yaw) { onNudge(-step, 0) }
                Button(action: onHome) {
                    VStack(spacing: 2) {
                        Text(String(format: "%g°", step))
                            .font(.headline.weight(.bold).monospacedDigit())
                        Text("HOME")
                            .font(.caption2.weight(.bold))
                            .tracking(1.1)
                            .foregroundStyle(Palette.muted)
                    }
                    .frame(width: 68, height: 68)
                    .background(
                        RadialGradient(colors: [Color.white.opacity(0.12), Color.white.opacity(0.04)], center: .center, startRadius: 4, endRadius: 40),
                        in: Circle()
                    )
                    .overlay(Circle().stroke(Color.white.opacity(0.16), lineWidth: 1))
                }
                .buttonStyle(PressableStyle())
                .disabled(!enabled)
                padKey("chevron.right", tint: Palette.yaw) { onNudge(step, 0) }
            }
            padKey("chevron.down", tint: Palette.pitch) { onNudge(0, -step) }
        }
        .opacity(enabled ? 1 : 0.42)
        .disabled(!enabled)
        .accessibilityElement(children: .contain)
    }

    private func padKey(_ system: String, tint: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: system)
                .font(.title2.weight(.semibold))
                .foregroundStyle(tint)
                .frame(width: 68, height: 52)
                .background(Color.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .stroke(tint.opacity(0.28), lineWidth: 1)
                )
        }
        .buttonStyle(PressableStyle())
    }
}

private struct PressableStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.94 : 1)
            .opacity(configuration.isPressed ? 0.88 : 1)
            .animation(.easeOut(duration: 0.14), value: configuration.isPressed)
    }
}

private struct Starfield: View {
    var body: some View {
        Canvas { context, size in
            var rng = SplitMix64(seed: 0x4D07_0E15)
            for index in 0..<90 {
                let x = rng.next() * size.width
                let y = rng.next() * size.height
                let radius = 0.4 + rng.next() * 1.3
                let opacity = 0.08 + rng.next() * (index.isMultiple(of: 7) ? 0.45 : 0.18)
                let rect = CGRect(x: x, y: y, width: radius, height: radius)
                context.fill(Path(ellipseIn: rect), with: .color(.white.opacity(opacity)))
            }
        }
    }
}

private struct SplitMix64 {
    var state: UInt64

    init(seed: UInt64) {
        state = seed
    }

    mutating func next() -> CGFloat {
        state &+= 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        z = z ^ (z >> 31)
        return CGFloat(z % 10_000) / 10_000
    }
}

#Preview {
    ContentView()
        .environmentObject(MountController())
        .environmentObject(LocationProvider())
        .environmentObject(AppSettings())
        .preferredColorScheme(.dark)
}

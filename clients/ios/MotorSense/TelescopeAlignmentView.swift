import CoreMotion
import SwiftUI
import UIKit

struct TelescopeAlignmentView: View {
    @EnvironmentObject private var mount: MountController
    @EnvironmentObject private var settings: AppSettings
    @StateObject private var attitude = TelescopeAttitude()
    @State private var showConfirmation = false

    private var connected: Bool { mount.yaw.connected && mount.pitch.connected }
    private var canAlign: Bool {
        connected && attitude.look != nil && !mount.locked && !mount.firmwareUpdate.active
    }

    var body: some View {
        ZStack {
            Palette.background.ignoresSafeArea()
            RadialGradient(
                colors: [Palette.yaw.opacity(0.16), .clear],
                center: .topTrailing,
                startRadius: 10,
                endRadius: 360
            )
            .ignoresSafeArea()
            .allowsHitTesting(false)
            ScrollView {
                VStack(spacing: 18) {
                    header
                    directionCard
                    instructions
                    alignButton
                }
                .padding(20)
            }
        }
        .preferredColorScheme(.dark)
        .onAppear { attitude.start() }
        .onDisappear { attitude.stop() }
        .confirmationDialog("Align telescope to iPhone?", isPresented: $showConfirmation, titleVisibility: .visible) {
            Button("Zero axes and align") { performAlignment() }
            Button("Cancel", role: .cancel) {}
        } message: {
            if let look = attitude.look {
                Text(String(format: "The telescope will be recorded at azimuth %.1f° and altitude %.1f°. Both motors must be stopped.", look.azimuth, look.altitude))
            }
        }
    }

    private var header: some View {
        VStack(spacing: 8) {
            Image(systemName: "iphone.gen3.radiowaves.left.and.right")
                .font(.system(size: 34, weight: .medium))
                .foregroundStyle(Palette.yaw)
            Text("TELESCOPE ALIGNMENT")
                .font(.caption.weight(.bold)).tracking(2)
                .foregroundStyle(Palette.muted)
            Text("Use the mounted iPhone as an inclinometer and compass")
                .font(.headline).multilineTextAlignment(.center)
        }
    }

    private var directionCard: some View {
        VStack(spacing: 16) {
            HStack(spacing: 12) {
                reading(title: "AZIMUTH", value: attitude.look?.azimuth, tint: Palette.yaw)
                reading(title: "ALTITUDE", value: attitude.look?.altitude, tint: Palette.pitch)
            }
            Divider().overlay(Color.white.opacity(0.1))
            Label(sensorStatus, systemImage: sensorStatusIcon)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(sensorStatusColor)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(18)
        .glassCard(cornerRadius: 24, stroke: Palette.yaw.opacity(0.25))
    }

    private func reading(title: String, value: Double?, tint: Color) -> some View {
        VStack(spacing: 6) {
            Text(title).font(.caption2.weight(.bold)).tracking(1).foregroundStyle(tint)
            Text(value.map { String(format: "%.1f°", $0) } ?? "—")
                .font(.system(.largeTitle, design: .rounded).weight(.semibold).monospacedDigit())
                .foregroundStyle(Palette.text)
        }
        .frame(maxWidth: .infinity)
    }

    private var instructions: some View {
        VStack(alignment: .leading, spacing: 12) {
            instruction(1, "Mount the iPhone in portrait orientation, with its top edge pointing through the telescope toward the sky.")
            instruction(2, "Keep it away from steel hardware, motors, and magnets where practical; wait for the readings to settle.")
            instruction(3, "Stop the mount, then align. This sets the current encoder positions to the measured azimuth and altitude.")
        }
        .padding(18)
        .glassCard(cornerRadius: 24)
    }

    private func instruction(_ number: Int, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text("\(number)").font(.caption.bold()).foregroundStyle(Palette.background)
                .frame(width: 24, height: 24).background(Palette.yaw, in: Circle())
            Text(text).font(.subheadline).foregroundStyle(Palette.text)
        }
    }

    private var alignButton: some View {
        Button { showConfirmation = true } label: {
            Label(buttonTitle, systemImage: "scope")
                .font(.headline)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 15)
                .background(Palette.yaw.opacity(canAlign ? 0.3 : 0.08), in: Capsule())
                .overlay(Capsule().stroke(Palette.yaw.opacity(canAlign ? 0.7 : 0.2), lineWidth: 1))
        }
        .foregroundStyle(canAlign ? Palette.text : Palette.muted)
        .disabled(!canAlign)
    }

    private var buttonTitle: String {
        if !connected { return "Connect both controllers to align" }
        if mount.locked { return "Unlock Telescope Control to align" }
        if attitude.look == nil { return "Waiting for compass" }
        return "Align telescope to iPhone"
    }

    private var sensorStatus: String {
        if let error = attitude.errorMessage { return error }
        guard attitude.look != nil else { return "Waiting for motion and compass data…" }
        let north = attitude.usesTrueNorth ? "True north" : "Magnetic north"
        switch attitude.compassAccuracy {
        case .high: return "\(north) · high compass accuracy"
        case .medium: return "\(north) · medium compass accuracy"
        case .low: return "\(north) · low accuracy; move away from magnetic interference"
        case .uncalibrated: return "\(north) · compass needs calibration"
        @unknown default: return north
        }
    }

    private var sensorStatusIcon: String {
        attitude.compassAccuracy == .high ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
    }

    private var sensorStatusColor: Color {
        attitude.compassAccuracy == .high ? Palette.good : Palette.warn
    }

    private func performAlignment() {
        guard let look = attitude.look else { return }
        if settings.hapticsEnabled { UINotificationFeedbackGenerator().notificationOccurred(.success) }
        mount.alignToPhone(azimuth: look.azimuth, altitude: look.altitude)
    }
}

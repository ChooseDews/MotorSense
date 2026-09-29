import SwiftUI

struct TelescopeView: View {
    let yaw: Double
    let pitch: Double
    let targetYaw: Double?
    let targetPitch: Double?
    let yawConnected: Bool
    let pitchConnected: Bool

    var body: some View {
        HStack(spacing: 0) {
            AzimuthCompass(yaw: wrapDegrees(yaw), target: targetYaw.map(wrapDegrees), connected: yawConnected)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            Rectangle()
                .fill(Color.white.opacity(0.08))
                .frame(width: 1)
                .padding(.vertical, 16)
            AltitudeProfile(pitch: pitch, target: targetPitch, connected: pitchConnected)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .animation(.easeOut(duration: 0.2), value: yaw)
        .animation(.easeOut(duration: 0.2), value: pitch)
    }
}

private struct AzimuthCompass: View {
    let yaw: Double
    let target: Double?
    let connected: Bool

    var body: some View {
        VStack(spacing: 8) {
            paneHeader(title: "YAW  ·  AZIMUTH", value: yaw, tint: Palette.yaw, connected: connected)
            GeometryReader { geo in
                let side = min(geo.size.width, geo.size.height)
                ZStack {
                    CompassRose(yaw: yaw, target: target)
                        .opacity(connected ? 1 : 0.18)
                    if !connected { disconnectedDialLabel }
                }
                .frame(width: side, height: side)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(12)
    }
}

private struct AltitudeProfile: View {
    let pitch: Double
    let target: Double?
    let connected: Bool

    var body: some View {
        VStack(spacing: 8) {
            paneHeader(title: "PITCH  ·  ALTITUDE", value: pitch, tint: Palette.pitch, connected: connected)
            ZStack {
                SideTelescope(pitch: pitch, target: target)
                    .opacity(connected ? 1 : 0.18)
                if !connected { disconnectedDialLabel }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(12)
    }
}

private func paneHeader(title: String, value: Double, tint: Color, connected: Bool) -> some View {
    HStack(alignment: .firstTextBaseline) {
        Text(title)
            .font(.caption2.weight(.bold))
            .tracking(0.8)
            .foregroundStyle(tint)
        Spacer()
        Text(connected ? String(format: "%.1f°", value) : "—")
            .font(.system(.title3, design: .rounded).monospacedDigit().weight(.semibold))
            .foregroundStyle(Palette.text)
    }
}

private var disconnectedDialLabel: some View {
    VStack(spacing: 5) {
        Image(systemName: "antenna.radiowaves.left.and.right.slash")
            .font(.title3.weight(.semibold))
        Text("NOT CONNECTED")
            .font(.caption2.weight(.bold))
            .tracking(0.8)
    }
    .foregroundStyle(Palette.muted)
}

private struct CompassRose: View {
    let yaw: Double
    let target: Double?

    var body: some View {
        GeometryReader { geo in
            let size = min(geo.size.width, geo.size.height)
            let radius = size * 0.42
            ZStack {
                Circle()
                    .fill(Color(red: 0.06, green: 0.09, blue: 0.14))
                Circle()
                    .stroke(Color.white.opacity(0.08), lineWidth: 1)
                Circle()
                    .stroke(Palette.yaw.opacity(0.18), lineWidth: 10)
                    .padding(10)

                ticks(radius: radius)

                if let target {
                    needle(at: target, color: Palette.warn, radius: radius, dashed: true)
                }
                Wedge(end: yaw)
                    .fill(Palette.yaw.opacity(0.16))
                needle(at: yaw, color: Palette.yaw, radius: radius, dashed: false)

                Circle()
                    .fill(Palette.yaw)
                    .frame(width: 10, height: 10)
                    .shadow(color: Palette.yaw.opacity(0.7), radius: 5)

                cardinal("N", at: 0, radius: radius * 0.72, emphasize: true)
                cardinal("E", at: 90, radius: radius * 0.72, emphasize: false)
                cardinal("S", at: 180, radius: radius * 0.72, emphasize: false)
                cardinal("W", at: 270, radius: radius * 0.72, emphasize: false)
            }
            .frame(width: size, height: size)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func ticks(radius: CGFloat) -> some View {
        Canvas { context, size in
            let center = CGPoint(x: size.width / 2, y: size.height / 2)
            for degree in stride(from: 0, through: 359, by: 15) {
                let major = degree % 90 == 0
                let medium = degree % 45 == 0
                let inner = radius * (major ? 0.78 : medium ? 0.84 : 0.88)
                let outer = radius * 0.96
                var path = Path()
                path.move(to: polar(center: center, azimuth: Double(degree), radius: inner))
                path.addLine(to: polar(center: center, azimuth: Double(degree), radius: outer))
                context.stroke(
                    path,
                    with: .color(.white.opacity(major ? 0.7 : medium ? 0.32 : 0.12)),
                    lineWidth: major ? 2.5 : 1
                )
            }
        }
    }

    private func needle(at azimuth: Double, color: Color, radius: CGFloat, dashed: Bool) -> some View {
        Canvas { context, size in
            let center = CGPoint(x: size.width / 2, y: size.height / 2)
            let tip = polar(center: center, azimuth: azimuth, radius: radius * 0.70)
            let tail = polar(center: center, azimuth: azimuth + 180, radius: radius * 0.16)
            var shaft = Path()
            shaft.move(to: tail)
            shaft.addLine(to: tip)
            context.stroke(
                shaft,
                with: .color(color),
                style: StrokeStyle(lineWidth: dashed ? 2 : 4, lineCap: .round, dash: dashed ? [5, 4] : [])
            )
            let left = polar(center: center, azimuth: azimuth - 14, radius: radius * 0.52)
            let right = polar(center: center, azimuth: azimuth + 14, radius: radius * 0.52)
            var head = Path()
            head.move(to: tip)
            head.addLine(to: left)
            head.addLine(to: right)
            head.closeSubpath()
            if !dashed {
                context.fill(head, with: .color(color))
            } else {
                context.stroke(head, with: .color(color), lineWidth: 1.5)
            }
        }
    }

    private func cardinal(_ text: String, at azimuth: Double, radius: CGFloat, emphasize: Bool) -> some View {
        GeometryReader { geo in
            let center = CGPoint(x: geo.size.width / 2, y: geo.size.height / 2)
            let point = polar(center: center, azimuth: azimuth, radius: radius)
            Text(text)
                .font(.caption.weight(.bold))
                .foregroundStyle(emphasize ? Palette.yaw : Palette.muted)
                .position(point)
        }
    }
}

private struct Wedge: Shape {
    var end: Double

    var animatableData: Double {
        get { end }
        set { end = newValue }
    }

    func path(in rect: CGRect) -> Path {
        var path = Path()
        let center = CGPoint(x: rect.midX, y: rect.midY)
        let radius = min(rect.width, rect.height) / 2
        path.move(to: center)
        path.addArc(
            center: center,
            radius: radius,
            startAngle: .degrees(-90),
            endAngle: .degrees(end - 90),
            clockwise: false
        )
        path.closeSubpath()
        return path
    }
}

private struct SideTelescope: View {
    let pitch: Double
    let target: Double?

    var body: some View {
        GeometryReader { geo in
            let box = geo.size
            Canvas { context, size in
                drawScene(context: &context, size: size, pitch: pitch, target: target)
            }
            .overlay(alignment: .leading) {
                VStack(alignment: .leading, spacing: 0) {
                    Text("90°")
                    Spacer()
                    Text("45°")
                    Spacer()
                    Text("0°")
                }
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Palette.muted)
                .padding(.leading, 6)
                .padding(.vertical, box.height * 0.12)
            }
        }
    }

    private func drawScene(context: inout GraphicsContext, size: CGSize, pitch: Double, target: Double?) {
        let pivot = CGPoint(x: size.width * 0.30, y: size.height * 0.78)
        let tube = min(size.width, size.height) * 0.62
        let horizonY = pivot.y

        var ground = Path()
        ground.addRect(CGRect(x: 0, y: horizonY, width: size.width, height: size.height - horizonY))
        context.fill(ground, with: .color(Color(red: 0.08, green: 0.10, blue: 0.08).opacity(0.85)))

        var horizon = Path()
        horizon.move(to: CGPoint(x: 8, y: horizonY))
        horizon.addLine(to: CGPoint(x: size.width - 8, y: horizonY))
        context.stroke(horizon, with: .color(.white.opacity(0.22)), style: StrokeStyle(lineWidth: 1, dash: [4, 4]))

        context.stroke(
            altitudeArc(pivot: pivot, radius: tube, to: 90),
            with: .color(.white.opacity(0.12)),
            style: StrokeStyle(lineWidth: 8, lineCap: .round)
        )
        context.stroke(
            altitudeArc(pivot: pivot, radius: tube, to: clamped(pitch)),
            with: .color(Palette.pitch.opacity(0.85)),
            style: StrokeStyle(lineWidth: 4, lineCap: .round)
        )

        for mark in [0.0, 45.0, 90.0] {
            let point = tubePoint(pivot: pivot, length: tube + 8, pitch: mark)
            var tick = Path()
            tick.move(to: tubePoint(pivot: pivot, length: tube - 8, pitch: mark))
            tick.addLine(to: point)
            context.stroke(tick, with: .color(.white.opacity(0.35)), lineWidth: 1.5)
        }

        drawPier(context: &context, pivot: pivot)

        if let target {
            drawTube(context: &context, pivot: pivot, length: tube, pitch: clamped(target), color: Palette.warn, filled: false)
        }
        drawTube(context: &context, pivot: pivot, length: tube, pitch: clamped(pitch), color: Palette.pitch, filled: true)
    }

    private func drawPier(context: inout GraphicsContext, pivot: CGPoint) {
        let base = CGRect(x: pivot.x - 22, y: pivot.y - 4, width: 44, height: 14)
        context.fill(Path(roundedRect: base, cornerRadius: 4), with: .color(Color(white: 0.22)))
        let column = CGRect(x: pivot.x - 7, y: pivot.y - 22, width: 14, height: 22)
        context.fill(Path(roundedRect: column, cornerRadius: 3), with: .color(Color(white: 0.32)))
        context.fill(Path(ellipseIn: CGRect(x: pivot.x - 5, y: pivot.y - 5, width: 10, height: 10)), with: .color(Palette.pitch))
    }

    private func drawTube(context: inout GraphicsContext, pivot: CGPoint, length: CGFloat, pitch: Double, color: Color, filled: Bool) {
        let tip = tubePoint(pivot: pivot, length: length, pitch: pitch)
        let angle = Angle(degrees: -pitch).radians
        let nx = CGFloat(-sin(angle))
        let ny = CGFloat(-cos(angle))
        let half: CGFloat = filled ? 7 : 5
        let p1 = CGPoint(x: pivot.x + nx * half, y: pivot.y + ny * half)
        let p2 = CGPoint(x: pivot.x - nx * half, y: pivot.y - ny * half)
        let p3 = CGPoint(x: tip.x - nx * (half - 1), y: tip.y - ny * (half - 1))
        let p4 = CGPoint(x: tip.x + nx * (half - 1), y: tip.y + ny * (half - 1))
        var body = Path()
        body.move(to: p1)
        body.addLine(to: p2)
        body.addLine(to: p3)
        body.addLine(to: p4)
        body.closeSubpath()
        if filled {
            context.fill(body, with: .color(Color(white: 0.72)))
            context.stroke(body, with: .color(color.opacity(0.9)), lineWidth: 2)
            context.fill(
                Path(ellipseIn: CGRect(x: tip.x - 6, y: tip.y - 6, width: 12, height: 12)),
                with: .color(color)
            )
            var shine = Path()
            shine.move(to: p1)
            shine.addLine(to: p4)
            context.stroke(shine, with: .color(.white.opacity(0.35)), lineWidth: 2)
        } else {
            context.stroke(body, with: .color(color), style: StrokeStyle(lineWidth: 2, dash: [5, 4]))
        }
    }

    private func altitudeArc(pivot: CGPoint, radius: CGFloat, to: Double) -> Path {
        var path = Path()
        path.addArc(
            center: pivot,
            radius: radius,
            startAngle: .degrees(0),
            endAngle: .degrees(-clamped(to)),
            clockwise: true
        )
        return path
    }

    private func tubePoint(pivot: CGPoint, length: CGFloat, pitch: Double) -> CGPoint {
        let angle = Angle(degrees: -clamped(pitch)).radians
        return CGPoint(
            x: pivot.x + CGFloat(cos(angle)) * length,
            y: pivot.y + CGFloat(sin(angle)) * length
        )
    }

    private func clamped(_ value: Double) -> Double {
        min(90, max(0, value))
    }
}

private func polar(center: CGPoint, azimuth: Double, radius: CGFloat) -> CGPoint {
    let radians = Angle(degrees: azimuth - 90).radians
    return CGPoint(
        x: center.x + CGFloat(cos(radians)) * radius,
        y: center.y + CGFloat(sin(radians)) * radius
    )
}

#Preview {
    ZStack {
        Palette.background.ignoresSafeArea()
        TelescopeView(yaw: 42, pitch: 28, targetYaw: 90, targetPitch: 45, yawConnected: true, pitchConnected: true)
            .frame(height: 240)
            .glassCard(cornerRadius: 28)
            .padding()
    }
    .preferredColorScheme(.dark)
}

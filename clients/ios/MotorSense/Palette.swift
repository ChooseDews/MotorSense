import SwiftUI

enum Palette {
    static let backgroundTop = Color(red: 0.04, green: 0.05, blue: 0.08)
    static let backgroundBottom = Color(red: 0.02, green: 0.02, blue: 0.04)
    static let card = Color.white.opacity(0.06)
    static let cardFill = Color(red: 0.10, green: 0.12, blue: 0.16).opacity(0.88)
    static let cardStroke = Color.white.opacity(0.10)
    static let yaw = Color(red: 0.45, green: 0.82, blue: 0.98)
    static let pitch = Color(red: 0.86, green: 0.68, blue: 1.00)
    static let good = Color(red: 0.38, green: 0.88, blue: 0.64)
    static let warn = Color(red: 0.98, green: 0.78, blue: 0.28)
    static let bad = Color(red: 0.96, green: 0.28, blue: 0.36)
    static let muted = Color.white.opacity(0.52)
    static let text = Color.white

    static var background: LinearGradient {
        LinearGradient(
            colors: [backgroundTop, backgroundBottom],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}

struct RedModeOverlay: ViewModifier {
    var enabled: Bool

    func body(content: Content) -> some View {
        content
            .preferredColorScheme(.dark)
            .overlay {
                if enabled {
                    Color.red
                        .opacity(0.42)
                        .blendMode(.multiply)
                        .ignoresSafeArea()
                        .allowsHitTesting(false)
                }
            }
            .saturation(enabled ? 0.08 : 1)
            .contrast(enabled ? 1.08 : 1)
            .colorMultiply(enabled ? Color(red: 1, green: 0.28, blue: 0.18) : .white)
    }
}

extension View {
    func redModeAware(_ enabled: Bool) -> some View {
        modifier(RedModeOverlay(enabled: enabled))
    }
}

struct GlassBackground: ViewModifier {
    var cornerRadius: CGFloat = 22
    var stroke: Color = Palette.cardStroke

    func body(content: Content) -> some View {
        content
            .background(.ultraThinMaterial.opacity(0.55), in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .background(Palette.cardFill, in: RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(stroke, lineWidth: 1)
            )
    }
}

extension View {
    func glassCard(cornerRadius: CGFloat = 22, stroke: Color = Palette.cardStroke) -> some View {
        modifier(GlassBackground(cornerRadius: cornerRadius, stroke: stroke))
    }
}

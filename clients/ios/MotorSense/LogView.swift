import SwiftUI

/// Raw BLE transcript: every command line sent to a controller and every line
/// received back, newest at the bottom.
struct LogView: View {
    @EnvironmentObject private var mount: MountController
    @State private var hideAxisUpdates = true
    @State private var showTimestamps = true

    private var filtered: [TrafficEntry] {
        hideAxisUpdates ? mount.traffic.filter { !$0.isAxisUpdate } : mount.traffic
    }

    var body: some View {
        Group {
            if filtered.isEmpty {
                ContentUnavailableView(
                    "No traffic yet",
                    systemImage: "waveform.path.ecg",
                    description: Text("Connect a controller; commands and replies appear here.")
                )
            } else {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 6) {
                            ForEach(filtered) { entry in
                                TrafficRow(entry: entry, showTimestamps: showTimestamps)
                                    .id(entry.id)
                            }
                        }
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)
                    }
                    .onChange(of: filtered.count) { _, _ in
                        proxy.scrollTo(filtered.last?.id, anchor: .bottom)
                    }
                    .onAppear {
                        proxy.scrollTo(filtered.last?.id, anchor: .bottom)
                    }
                }
            }
        }
        .background(Palette.background)
        .navigationTitle("Command log")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Toggle("Hide axis updates", isOn: $hideAxisUpdates)
                    Toggle("Timestamps", isOn: $showTimestamps)
                } label: {
                    Image(systemName: "line.3.horizontal.decrease.circle")
                        .foregroundStyle(hideAxisUpdates ? Palette.yaw : Palette.muted)
                }
                .accessibilityLabel("Log filters")
            }
        }
        .preferredColorScheme(.dark)
    }
}

private struct TrafficRow: View {
    let entry: TrafficEntry
    let showTimestamps: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if showTimestamps {
                Text(entry.timeText)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(Palette.muted)
                    .frame(minWidth: 62, alignment: .leading)
            }
            Image(systemName: entry.sent ? "arrow.up.right" : "arrow.down.left")
                .font(.caption2.weight(.bold))
                .foregroundStyle(entry.sent ? Palette.yaw : Palette.good)
                .frame(width: 14)
                .accessibilityLabel(entry.sent ? "Sent" : "Received")
            VStack(alignment: .leading, spacing: 2) {
                if let axis = entry.axis {
                    Text(axis.rawValue.uppercased())
                        .font(.caption2.weight(.bold))
                        .tracking(1)
                        .foregroundStyle(axis == .yaw ? Palette.yaw : Palette.pitch)
                }
                Text(entry.text)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(textColor)
                    .textSelection(.enabled)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    private var textColor: Color {
        if entry.text.hasPrefix("ERR") || entry.text.hasPrefix("ESP-ROM") { return Palette.bad }
        if !entry.sent { return Palette.text }
        return Palette.muted
    }
}

private let trafficTimeFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.dateFormat = "HH:mm:ss.SSS"
    return formatter
}()

extension TrafficEntry {
    var timeText: String {
        trafficTimeFormatter.string(from: date)
    }
}

#Preview {
    NavigationStack {
        LogView()
    }
    .environmentObject(MountController())
}

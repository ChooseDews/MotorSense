import SwiftUI
import UniformTypeIdentifiers

struct FirmwareUpdateView: View {
    @EnvironmentObject private var mount: MountController
    @State private var axis: AxisName = .yaw
    @State private var image: FirmwareImage?
    @State private var importing = false
    @State private var loading = false
    @State private var fileError: String?
    @State private var firmwareURL = ""
    @State private var downloadTask: Task<Void, Never>?
    @State private var downloadedBytes = 0
    @State private var downloadTotal: Int?
    @State private var downloadID: UUID?

    private var connected: Bool { axis == .yaw ? mount.yaw.connected : mount.pitch.connected }
    private var status: FirmwareUpdateStatus { mount.firmwareUpdate }

    var body: some View {
        List {
            Section("Controller") {
                Picker("Update", selection: $axis) {
                    ForEach(AxisName.allCases, id: \.self) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                .disabled(status.active)
                LabeledContent("Connection", value: connected ? "Connected" : "Not connected")
                if !connected {
                    Button(mount.scanning ? "Connecting…" : "Connect \(axis.title)") {
                        mount.connectFirmwareController(axis)
                    }
                    .disabled(mount.scanning || status.active)
                }
            }
            Section("Firmware link") {
                TextField("https://example.com/motorsense.bin", text: $firmwareURL)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .disabled(loading || status.active)
                Button("Download firmware", systemImage: "arrow.down.doc") { downloadFirmware() }
                    .disabled(firmwareURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || loading || status.active)
                if downloadTask != nil {
                    if let total = downloadTotal {
                        ProgressView(value: Double(downloadedBytes), total: Double(total))
                    } else {
                        ProgressView()
                    }
                    Text("Downloaded \(ByteCountFormatter.string(fromByteCount: Int64(downloadedBytes), countStyle: .file))")
                        .font(.caption.monospacedDigit())
                    Button("Cancel download", role: .cancel) { cancelDownload() }
                }
                Text("Use a direct HTTPS download link. After validation, tap Update below to install it on the selected controller.")
                    .font(.caption).foregroundStyle(Palette.muted)
            }
            Section("Firmware file") {
                Button(loading && downloadTask == nil ? "Reading file…" : "Choose .bin file") { importing = true }
                    .disabled(status.active || loading)
                if let image {
                    Text(image.filename).font(.headline)
                    LabeledContent("Version", value: image.version)
                    LabeledContent("Size", value: ByteCountFormatter.string(fromByteCount: Int64(image.data.count), countStyle: .file))
                    Text("SHA-256: \(image.sha256)")
                        .font(.caption.monospaced()).textSelection(.enabled)
                }
                if let fileError { Text(fileError).foregroundStyle(Palette.bad) }
            }
            Section {
                Button("Update \(axis.title)", systemImage: "arrow.down.circle") {
                    if let image { mount.updateFirmware(axis, image: image) }
                }
                .disabled(image == nil || !connected || mount.scanning || status.active || loading)
            } footer: {
                Text("The same firmware file works on both controllers. Updating stops motion and locks Telescope Control. Keep this app open and the controller powered. Existing boards need the initial wired OTA migration first.")
            }
            Section("Update status") {
                if let target = status.axis { LabeledContent("Controller", value: target.title) }
                Text(status.message).foregroundStyle(status.failed ? Palette.bad : Palette.text)
                if status.total > 0 {
                    ProgressView(value: status.progress)
                    Text("\(status.acknowledged.formatted()) / \(status.total.formatted()) bytes acknowledged")
                        .font(.caption.monospacedDigit())
                }
                if status.active && status.canCancel {
                    Button("Cancel update", role: .destructive) { mount.cancelFirmwareUpdate() }
                }
                if status.active && !status.canCancel {
                    Text("Finishing installation. Waiting for controller verification.").font(.caption)
                }
            }
            Section {
                Text("After reboot, the app reconnects to the same controller and checks the reported version. Telescope Control stays locked afterward. Re-zero the axis before unlocking and moving.")
                Text("An interrupted upload can be restarted from the beginning. If verification fails after reboot, check the reported firmware version: the board may have rolled back.")
            }
            .font(.caption)
        }
        .navigationTitle("Firmware Update")
        .navigationBarTitleDisplayMode(.inline)
        .fileImporter(isPresented: $importing, allowedContentTypes: [.data]) { result in
            switch result {
            case .success(let url):
                loading = true
                fileError = nil
                image = nil
                Task {
                    do {
                        let loaded = try await Task.detached(priority: .userInitiated) {
                            let access = url.startAccessingSecurityScopedResource()
                            defer { if access { url.stopAccessingSecurityScopedResource() } }
                            let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                            guard size <= 0xf0000 else {
                                throw FirmwareError.invalid("Firmware exceeds the controller’s 960 KiB OTA slot.")
                            }
                            return try FirmwareImage(data: Data(contentsOf: url), filename: url.lastPathComponent)
                        }.value
                        image = loaded
                    } catch { fileError = error.localizedDescription }
                    loading = false
                }
            case .failure(let error): fileError = error.localizedDescription
            }
        }
        .onAppear { if status.active, let target = status.axis { axis = target } }
        .onDisappear { cancelDownload() }

    }
    private func downloadFirmware() {
        image = nil
        fileError = nil
        let url: URL
        do { url = try FirmwareDownload.url(from: firmwareURL) }
        catch { fileError = error.localizedDescription; return }
        downloadedBytes = 0
        downloadTotal = nil
        loading = true
        let id = UUID()
        downloadID = id
        downloadTask = Task {
            do {
                let loaded = try await FirmwareDownload.fetch(from: url) { received, total in
                    Task { @MainActor in
                        guard downloadID == id else { return }
                        downloadedBytes = received
                        downloadTotal = total
                    }
                }
                try Task.checkCancellation()
                guard downloadID == id else { return }
                image = loaded
            } catch {
                guard downloadID == id else { return }
                fileError = Task.isCancelled ? "Download cancelled." : error.localizedDescription
            }
            guard downloadID == id else { return }
            downloadID = nil
            loading = false
            downloadTask = nil
        }
    }

    private func cancelDownload() {
        guard downloadTask != nil else { return }
        downloadID = nil
        downloadTask?.cancel()
        downloadTask = nil
        loading = false
        fileError = "Download cancelled."
    }

}

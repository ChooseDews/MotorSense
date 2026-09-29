import Foundation

nonisolated enum FirmwareDownload {
    static let maximumSize = 0xf0000
    private static let downloadSession: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 60
        configuration.timeoutIntervalForResource = 180
        return URLSession(configuration: configuration)
    }()

    static func url(from text: String) throws -> URL {
        guard let url = URL(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              url.scheme?.lowercased() == "https", let host = url.host, !host.isEmpty,
              url.user == nil, url.password == nil else {
            throw FirmwareError.invalid("Enter a direct HTTPS link to a firmware .bin file.")
        }
        return url
    }

    static func fetch(from url: URL, session: URLSession = downloadSession,
                      progress: @escaping @Sendable (Int, Int?) -> Void) async throws -> FirmwareImage {
        _ = try self.url(from: url.absoluteString)
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 60)
        request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
        request.setValue("application/octet-stream", forHTTPHeaderField: "Accept")
        let (bytes, response) = try await session.bytes(for: request)
        defer { bytes.task.cancel() }
        guard let response = response as? HTTPURLResponse,
              response.url?.scheme?.lowercased() == "https" else {
            throw FirmwareError.invalid("The download did not return an HTTPS response.")
        }
        guard response.statusCode == 200 else {
            throw FirmwareError.invalid("Download failed (HTTP \(response.statusCode)). Use a direct download link, not a sharing page.")
        }
        guard response.expectedContentLength <= maximumSize else {
            throw FirmwareError.invalid("Firmware exceeds the controller’s 960 KiB OTA slot.")
        }
        let total = response.expectedContentLength > 0 ? Int(response.expectedContentLength) : nil
        var data = Data()
        data.reserveCapacity(total ?? maximumSize)
        progress(0, total)
        for try await byte in bytes {
            try Task.checkCancellation()
            guard data.count < maximumSize else {
                throw FirmwareError.invalid("Firmware exceeds the controller’s 960 KiB OTA slot.")
            }
            data.append(byte)
            if data.count % 16384 == 0 { progress(data.count, total) }
        }
        try Task.checkCancellation()
        if let total, data.count != total {
            throw FirmwareError.invalid("The firmware download was incomplete. Try again.")
        }
        let filename = [response.suggestedFilename, response.url?.lastPathComponent, url.lastPathComponent]
            .compactMap { $0 }.first { $0.lowercased().hasSuffix(".bin") } ?? "motorsense.bin"
        let image = try FirmwareImage(data: data, filename: filename)
        progress(data.count, total)
        return image
    }
}

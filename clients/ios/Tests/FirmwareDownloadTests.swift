import Foundation

nonisolated final class FirmwareHTTPFixture: URLProtocol, @unchecked Sendable {
    static var firmware: Data {
        var data = Data(repeating: 0, count: 350)
        data[0] = 0xe9; data[12] = 9
        data.replaceSubrange(32..<36, with: [0x32, 0x54, 0xcd, 0xab])
        data.replaceSubrange(48..<53, with: Array("1.2.3".utf8))
        data.replaceSubrange(80..<90, with: Array("motorsense".utf8))
        return data
    }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let path = request.url!.path
        if path == "/hang" { return }
        var headers = ["Content-Type": "application/octet-stream"]
        var data = Self.firmware
        if path == "/oversize" { headers["Content-Length"] = "9999999" }
        if path == "/stream-oversize" { data = Data(repeating: 0, count: FirmwareDownload.maximumSize + 1) }
        if path == "/html" { data = Data("<html>Sharing page</html>".utf8) }
        if path == "/truncated" { headers["Content-Length"] = "1000" }
        if path == "/download" { headers["Content-Disposition"] = "attachment; filename=release.bin" }
        let response = HTTPURLResponse(url: request.url!, statusCode: path == "/missing" ? 404 : 200,
                                       httpVersion: "HTTP/1.1", headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

@main
struct FirmwareDownloadTests {
    static func main() async throws {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [FirmwareHTTPFixture.self]
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        func fetch(_ path: String) async throws -> FirmwareImage {
            try await FirmwareDownload.fetch(from: URL(string: "https://firmware.example\(path)")!, session: session) { _, _ in }
        }
        let image = try await fetch("/firmware.bin?token=example")
        assert(image.data == FirmwareHTTPFixture.firmware && image.version == "1.2.3")
        let named = try await fetch("/download")
        assert(named.filename == "release.bin")
        let fallback = try await fetch("/raw")
        assert(fallback.filename == "motorsense.bin")
        for path in ["/missing", "/oversize", "/stream-oversize", "/html", "/truncated"] {
            do { _ = try await fetch(path); fatalError("Expected rejection: \(path)") } catch {}
        }
        for link in ["", "not a url", "file:///firmware.bin", "http://example.com/firmware.bin", "https://user:password@example.com/file.bin"] {
            do { _ = try FirmwareDownload.url(from: link); fatalError("Expected URL rejection") } catch {}
        }
        let url = try FirmwareDownload.url(from: "  https://example.com/firmware.bin\n")
        assert(url.host == "example.com")
        let task = Task { try await fetch("/hang") }
        try await Task.sleep(for: .milliseconds(100))
        task.cancel()
        do { _ = try await task.value; fatalError("Expected cancellation") } catch {}
        print("Firmware URL download tests passed")
    }
}

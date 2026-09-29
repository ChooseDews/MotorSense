import Foundation

@main
struct FirmwareTransferTests {
    static func main() throws {
        func check(_ result: Bool) { assert(result) }
        var data = Data(repeating: 0, count: 350)
        data[0] = 0xe9; data[12] = 9
        data.replaceSubrange(32..<36, with: [0x32, 0x54, 0xcd, 0xab])
        data.replaceSubrange(48..<53, with: Array("1.2.3".utf8))
        data.replaceSubrange(80..<90, with: Array("motorsense".utf8))
        let image = try FirmwareImage(data: data, filename: "motorsense.bin")
        assert(image.version == "1.2.3" && image.sha256.count == 64)
        func rejects(_ body: () throws -> Void) {
            do { try body(); fatalError("Expected rejection") } catch {}
        }
        rejects { _ = try FirmwareImage(data: Data(), filename: "empty.bin") }
        rejects { _ = try FirmwareImage(data: data, filename: "image.txt") }
        var wrongChip = data; wrongChip[12] = 0
        rejects { _ = try FirmwareImage(data: wrongChip, filename: "wrong.bin") }
        var wrongProject = data; wrongProject[80] = 0
        rejects { _ = try FirmwareImage(data: wrongProject, filename: "wrong.bin") }
        rejects { _ = try FirmwareImage(data: Data(repeating: 0, count: 0xf0001), filename: "large.bin") }
        for axis in AxisName.allCases {
            let transfer = FirmwareTransfer(image: image, axis: axis)
            assert(transfer.command == "DEVICE INFO")
            check(try !transfer.receive("ENC pos0=42 pos1=0"))
            check(try transfer.receive("DEVICE role=\(axis.rawValue.uppercased()) firmware=1.0.0 hardware=unknown project=motorsense"))
            assert(transfer.command == "OTA ABORT")
            check(try transfer.receive("OK OTA ABORT"))
            assert(transfer.command == "OTA BIN BEGIN 350 \(image.sha256)")
            assert(transfer.timeout == 120)
            check(try transfer.receive("OK OTA BIN BEGIN"))
            var restored = Data()
            while restored.count < data.count {
                let frames = transfer.dataFrames(maxPayload: 20)
                assert(frames.count <= 8 && !frames.isEmpty)
                for (index, frame) in frames.enumerated() {
                    assert(frame[0] == 0x4d && frame[1] == 0x53)
                    let offset = Int(frame[2]) | (Int(frame[3]) << 8) | (Int(frame[4]) << 16) | (Int(frame[5]) << 24)
                    let count = Int(frame[6]) | (Int(frame[7]) << 8)
                    assert(offset == restored.count && frame.count == count + 9)
                    assert(frame[8] == (index == frames.count - 1 ? 1 : 0))
                    restored.append(frame[9...])
                }
                check(try transfer.receive("OK OTA BIN \(restored.count)"))
                assert(transfer.acknowledged == restored.count)
            }
            assert(restored == data)
            assert(transfer.command == "OTA END")
            check(try transfer.receive("OK OTA REBOOT"))
            assert(transfer.phase == .reboot && transfer.command == nil)
            transfer.reconnecting()
            assert(transfer.command == "DEVICE INFO")
            rejects { _ = try transfer.receive("DEVICE role=\(axis.rawValue.uppercased()) firmware=1.0.0 project=motorsense") }
            check(try transfer.receive("DEVICE role=\(axis.rawValue.uppercased()) firmware=1.2.3 project=motorsense"))
            assert(transfer.phase == .complete)
        }
        let transfer = FirmwareTransfer(image: image, axis: .yaw)
        rejects { _ = try transfer.receive("DEVICE role=PITCH firmware=1.2.3 project=motorsense") }
        rejects { _ = try transfer.receive("ERR unknown command (type HELP)") }
        _ = try transfer.receive("DEVICE role=YAW firmware=1.0.0 project=motorsense")
        _ = try transfer.receive("OK OTA ABORT")
        _ = try transfer.receive("OK OTA BIN BEGIN")
        _ = transfer.dataFrames(maxPayload: 20)
        rejects { _ = try transfer.receive("OK OTA BIN 87") }
        assert(transfer.acknowledged == 0)
        _ = try transfer.receive("OK OTA BIN 88")
        _ = transfer.dataFrames(maxPayload: 20)
        rejects { _ = try transfer.receive("OK OTA BIN 88") }
        assert(transfer.acknowledged == 88)
        if CommandLine.arguments.count > 1 {
            let url = URL(fileURLWithPath: CommandLine.arguments[1])
            let actual = try FirmwareImage(data: Data(contentsOf: url), filename: url.lastPathComponent)
            print("Validated built firmware \(actual.version): \(actual.sha256)")
        }
        print("Firmware image and OTA protocol tests passed")
    }
}

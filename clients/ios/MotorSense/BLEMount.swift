@preconcurrency import CoreBluetooth
import Foundation

nonisolated final class BLEMount: NSObject, @unchecked Sendable {
    private let queue = DispatchQueue(label: "motorsense.ble")
    private let events: @Sendable (MountEvent) -> Void
    private var ota: FirmwareTransfer?
    private var otaTimeout: DispatchWorkItem?
    private var otaReconnect: DispatchWorkItem?
    private var requestedAxes = AxisName.allCases
    private var central: CBCentralManager!
    private var sessions: [AxisName: AxisSession] = [
        .yaw: AxisSession(name: .yaw),
        .pitch: AxisSession(name: .pitch),
    ]
    private var discovered: [UUID: DiscoveredPeripheral] = [:]
    private var pendingConnect: [AxisName: DiscoveredPeripheral] = [:]
    private var connectingAxis: AxisName?
    private var connecting = false
    private var scanTimeout: DispatchWorkItem?
    private var connectTimeout: DispatchWorkItem?
    private var bluetoothWait: DispatchWorkItem?
    private var pollTimer: DispatchSourceTimer?
    private let encoderRegex = try! NSRegularExpression(pattern: #"(?:\bENC\s+pos0=|\bENCODER0:.*?\bpos=|quad_enc: enc=0 pos=)(-?\d+)"#)
    private let statusRegex = try! NSRegularExpression(
        pattern: #"AXIS: state=(\w+) start=(-?\d+) target=(-?\d+) position=(-?\d+) duty=(-?\d+) corrections=(\d+)"#
    )

    init(events: @escaping @Sendable (MountEvent) -> Void) {
        self.events = events
        super.init()
        central = CBCentralManager(delegate: self, queue: queue)
    }

    func scanAndConnect() {
        queue.async {
            guard self.ota == nil else { return }
            self.requestedAxes = AxisName.allCases
            self.beginConnect()
        }
    }

    func connectFirmwareController(_ axis: AxisName) {
        queue.async {
            guard self.ota == nil else { return }
            self.requestedAxes = [axis]
            self.beginConnect()
        }
    }

    func updateFirmware(_ axis: AxisName, image: FirmwareImage) {
        queue.async {
            guard self.ota == nil, !self.connecting,
                  let session = self.sessions[axis], session.connected,
                  session.write?.properties.contains(.write) == true else {
                self.events(.firmware(FirmwareUpdateStatus(axis: axis, message: "Connect the selected controller before updating.", failed: true)))
                return
            }
            // Stop both axes before suppressing regular commands and status polling.
            for name in AxisName.allCases { self.write(name, "AXIS STOP") }
            self.ota = FirmwareTransfer(image: image, axis: axis)
            self.sendOTACommand()
        }
    }

    func cancelFirmwareUpdate() {
        queue.async {
            guard let ota = self.ota, ota.phase != .end, ota.phase != .reboot,
                  ota.phase != .verify else { return }
            self.failOTA("Update cancelled. Reconnect before retrying.")
        }
    }

    private func publishOTA() {
        guard let ota else { return }
        events(.firmware(FirmwareUpdateStatus(axis: ota.axis, message: ota.message,
            acknowledged: ota.acknowledged, total: ota.image.data.count,
            active: ota.phase != .complete,
            canCancel: ota.phase != .end && ota.phase != .reboot && ota.phase != .verify && ota.phase != .complete)))
    }

    private func sendOTACommand() {
        guard let ota else { return }
        publishOTA()
        if ota.isSendingData {
            guard let session = sessions[ota.axis], let peripheral = session.peripheral,
                  let characteristic = session.write,
                  characteristic.properties.contains(.writeWithoutResponse) else {
                failOTA("Controller does not support pipelined BLE writes.")
                return
            }
            let frames = ota.dataFrames(maxPayload: peripheral.maximumWriteValueLength(for: .withoutResponse))
            guard !frames.isEmpty else {
                failOTA("BLE MTU is too small for firmware data.")
                return
            }
            session.writeQueue.append(contentsOf: frames.map {
                QueuedBLEWrite(data: $0, type: .withoutResponse)
            })
            flush(session)
        } else if let command = ota.command {
            write(ota.axis, command, firmware: true)
        }
        otaTimeout?.cancel()
        let timeout = DispatchWorkItem { [weak self] in
            self?.failOTA("Timed out waiting for the controller. Reconnect and check its firmware version before retrying.")
        }
        otaTimeout = timeout
        queue.asyncAfter(deadline: .now() + ota.timeout, execute: timeout)
    }

    private func failOTA(_ message: String) {
        guard let transfer = ota else { return }
        otaTimeout?.cancel(); otaTimeout = nil
        otaReconnect?.cancel(); otaReconnect = nil
        connectTimeout?.cancel(); connectTimeout = nil
        connectingAxis = nil; connecting = false
        // Disconnect aborts the firmware session, including any partially sent line.
        // Never append ABORT to a command whose final fragment may not have arrived.
        if let session = sessions[transfer.axis] {
            session.writeQueue.removeAll()
            if let peripheral = session.peripheral { central.cancelPeripheralConnection(peripheral) }
        }
        events(.firmware(FirmwareUpdateStatus(axis: transfer.axis, message: message,
            acknowledged: transfer.acknowledged, total: transfer.image.data.count, failed: true)))
        ota = nil
    }

    private func receiveOTA(_ line: String, session: AxisSession) -> Bool {
        guard let transfer = ota, transfer.axis == session.name else { return false }
        do {
            guard try transfer.receive(line) else { return true }
            otaTimeout?.cancel(); otaTimeout = nil
            publishOTA()
            if transfer.phase == .reboot {
                // The firmware reboots after one second, then runs ten-second diagnostics.
                let reconnect = DispatchWorkItem { [weak self] in
                    guard let self, let transfer = self.ota,
                          let session = self.sessions[transfer.axis], let peripheral = session.peripheral else { return }
                    guard peripheral.state == .disconnected else {
                        self.failOTA("Firmware was uploaded, but reboot was not observed. Reconnect and verify the version.")
                        return
                    }
                    transfer.reconnecting()
                    self.publishOTA()
                    session.buffer = ""
                    session.connecting = true
                    self.connecting = true
                    self.connectingAxis = transfer.axis
                    self.events(.connecting(transfer.axis))
                    self.central.connect(peripheral, options: nil)
                }
                otaReconnect = reconnect
                queue.asyncAfter(deadline: .now() + 15, execute: reconnect)
                let timeout = DispatchWorkItem { [weak self] in
                    self?.failOTA("Firmware was uploaded, but reconnect verification timed out. Check the controller before moving.")
                }
                otaTimeout = timeout
                queue.asyncAfter(deadline: .now() + 60, execute: timeout)
            } else if transfer.phase == .complete {
                ota = nil
                connecting = false
                startPolling()
            } else { sendOTACommand() }
        } catch { failOTA(error.localizedDescription) }
        return true
    }

    func stop() {
        queue.async {
            for axis in AxisName.allCases {
                self.write(axis, "AXIS STOP")
            }
        }
    }

    func command(_ axis: AxisName, _ text: String) {
        queue.async { self.write(axis, text) }
    }

    private func beginConnect() {
        guard !connecting else { return }
        connecting = true
        bluetoothWait?.cancel()
        switch central.state {
        case .poweredOn:
            startScan()
        case .poweredOff:
            failConnect(MountError.bluetoothOff)
        case .unauthorized:
            failConnect(MountError.bluetoothUnauthorized)
        case .unsupported:
            failConnect(MountError.bluetoothUnavailable)
        default:
            events(.log("Waiting for Bluetooth…"))
            let wait = DispatchWorkItem { [weak self] in
                self?.failConnect(MountError.timedOut("Bluetooth did not become ready"))
            }
            bluetoothWait = wait
            queue.asyncAfter(deadline: .now() + 8, execute: wait)
        }
    }

    private func startScan() {
        discovered.removeAll()
        pendingConnect.removeAll()
        connectingAxis = nil
        events(.log("Scanning for MotorSense controllers…"))
        central.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
        let timeout = DispatchWorkItem { [weak self] in
            self?.finishScan()
        }
        scanTimeout = timeout
        queue.asyncAfter(deadline: .now() + 6, execute: timeout)
    }

    private func finishScan() {
        scanTimeout?.cancel()
        scanTimeout = nil
        central.stopScan()
        var selected: [AxisName: DiscoveredPeripheral] = [:]
        for axis in requestedAxes {
            let matches = discovered.values.filter { $0.matches(axis) }
            guard matches.count <= 1 else {
                failConnect(MountError.failed("Multiple \(axis.title) controllers found. Power on only the controller you want to connect."))
                return
            }
            guard let match = matches.first else {
                failConnect(MountError.missingController(axis))
                return
            }
            selected[axis] = match
        }
        pendingConnect = selected
        events(.log("Found \(selected.values.map(\.name).joined(separator: ", "))"))
        connectNext()
    }

    private func connectNext() {
        guard connectingAxis == nil else { return }
        guard let axis = pendingConnect.keys.first, let item = pendingConnect.removeValue(forKey: axis) else {
            connecting = false
            startPolling()
            events(.log("Controllers connected"))
            return
        }
        let session = sessions[axis]!
        disconnect(session)
        session.connecting = true
        session.peripheral = item.peripheral
        session.deviceName = item.name
        item.peripheral.delegate = self
        connectingAxis = axis
        events(.connecting(axis))
        central.connect(item.peripheral, options: nil)
        let timeout = DispatchWorkItem { [weak self] in
            guard let self, self.connectingAxis == axis else { return }
            self.failCurrentConnect(MountError.timedOut("\(axis.rawValue) did not connect"))
        }
        connectTimeout = timeout
        queue.asyncAfter(deadline: .now() + 20, execute: timeout)
    }

    private func failCurrentConnect(_ error: Error) {
        connectTimeout?.cancel()
        connectTimeout = nil
        if let axis = connectingAxis {
            sessions[axis]?.connecting = false
            connectingAxis = nil
        }
        failConnect(error)
    }

    private func failConnect(_ error: Error) {
        if ota != nil { failOTA(error.localizedDescription) }
        scanTimeout?.cancel()
        scanTimeout = nil
        bluetoothWait?.cancel()
        bluetoothWait = nil
        connectTimeout?.cancel()
        connectTimeout = nil
        central.stopScan()
        connecting = false
        connectingAxis = nil
        pendingConnect.removeAll()
        for session in sessions.values {
            session.connecting = false
        }
        events(.log("Connect failed: \(error.localizedDescription)"))
    }

    private func disconnect(_ session: AxisSession) {
        if let peripheral = session.peripheral {
            if let characteristic = session.notify {
                peripheral.setNotifyValue(false, for: characteristic)
            }
            if peripheral.state != .disconnected {
                central.cancelPeripheralConnection(peripheral)
            }
        }
        session.reset()
    }

    private func startPolling() {
        pollTimer?.cancel()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + 0.5, repeating: 0.5)
        timer.setEventHandler { [weak self] in
            guard let self else { return }
            for axis in AxisName.allCases where self.sessions[axis]?.connected == true {
                self.write(axis, "AXIS STATUS")
            }
        }
        timer.resume()
        pollTimer = timer
    }

    private func write(_ axis: AxisName, _ text: String, firmware: Bool = false) {
        guard ota == nil || firmware else { return }
        guard let session = sessions[axis], session.connected, session.write != nil else { return }
        let bytes = Data((text + "\n").utf8)
        // The no-response limit reflects a single ATT packet. Applying it to writes
        // with response avoids CoreBluetooth prepared/long writes (unsupported by firmware).
        let peripheral = session.peripheral!
        let fragmentSize = max(1, min(244, peripheral.maximumWriteValueLength(for: .withoutResponse),
                                      peripheral.maximumWriteValueLength(for: .withResponse)))
        for offset in stride(from: 0, to: bytes.count, by: fragmentSize) {
            session.writeQueue.append(QueuedBLEWrite(
                data: bytes.subdata(in: offset..<min(offset + fragmentSize, bytes.count)), type: .withResponse))
        }
        flush(session)
        if !firmware && text != "STATUS" && text != "AXIS STATUS" {
            events(.command("\(axis.rawValue.uppercased()): \(text)"))
        }
        // Transcript: every line actually handed to the controller, polls and
        // OTA command lines included. Binary OTA data frames bypass write().
        events(.traffic(axis: axis, sent: true, text: text))
    }

    private func flush(_ session: AxisSession) {
        guard !session.writeBusy,
              let peripheral = session.peripheral,
              let characteristic = session.write,
              !session.writeQueue.isEmpty else { return }
        let queued = session.writeQueue[0]
        let writeType = queued.type
        if writeType == .withoutResponse && !peripheral.canSendWriteWithoutResponse {
            return
        }
        if writeType == .withoutResponse && !characteristic.properties.contains(.writeWithoutResponse) {
            if ota?.axis == session.name { failOTA("Controller does not support pipelined BLE writes.") }
            return
        }
        let payload = session.writeQueue.removeFirst().data
        session.writeBusy = writeType == .withResponse
        peripheral.writeValue(payload, for: characteristic, type: writeType)
        if writeType == .withoutResponse {
            flush(session)
        }
    }

    private func handleNotification(_ session: AxisSession, data: Data) {
        session.buffer += String(decoding: data, as: UTF8.self)
        while let range = session.buffer.range(of: "\n") {
            let line = String(session.buffer[..<range.lowerBound]).trimmingCharacters(in: CharacterSet(charactersIn: "\r"))
            session.buffer = String(session.buffer[range.upperBound...])
            parse(line, session: session)
        }
    }

    private func parse(_ line: String, session: AxisSession) {
        // Transcript first: even OTA-handled lines are controller replies.
        if !line.isEmpty {
            events(.traffic(axis: session.name, sent: false, text: line))
        }
        if receiveOTA(line, session: session) { return }
        let nsLine = line as NSString
        let full = NSRange(location: 0, length: nsLine.length)
        if let match = encoderRegex.firstMatch(in: line, range: full), match.numberOfRanges > 1 {
            events(.encoder(session.name, Int(nsLine.substring(with: match.range(at: 1))) ?? 0))
        }
        if let match = statusRegex.firstMatch(in: line, range: full), match.numberOfRanges > 6 {
            events(.status(
                session.name,
                state: nsLine.substring(with: match.range(at: 1)),
                targetTicks: Int(nsLine.substring(with: match.range(at: 3))) ?? 0,
                position: Int(nsLine.substring(with: match.range(at: 4))) ?? 0,
                duty: Int(nsLine.substring(with: match.range(at: 5))) ?? 0,
                corrections: Int(nsLine.substring(with: match.range(at: 6))) ?? 0
            ))
        }
        if line.hasPrefix("ERR") {
            events(.axisError(session.name, line))
        }
    }

    private func session(for peripheral: CBPeripheral) -> AxisSession? {
        sessions.values.first { $0.peripheral?.identifier == peripheral.identifier }
    }

    private func finishCurrentConnect(error: Error?) {
        connectTimeout?.cancel()
        connectTimeout = nil
        guard let axis = connectingAxis else { return }
        connectingAxis = nil
        if let error {
            sessions[axis]?.connecting = false
            failConnect(error)
            return
        }
        let session = sessions[axis]!
        session.connected = true
        session.connecting = false
        session.error = nil
        events(.connected(axis, session.deviceName ?? "unknown"))
        if ota?.axis == axis, ota?.phase == .verify {
            connecting = false
            sendOTACommand()
            return
        }
        write(axis, "STATUS")
        write(axis, "AXIS STATUS")
        connectNext()
    }
}

nonisolated private final class AxisSession {
    let name: AxisName
    var peripheral: CBPeripheral?
    var write: CBCharacteristic?
    var notify: CBCharacteristic?
    var writeQueue: [QueuedBLEWrite] = []
    var writeBusy = false
    var buffer = ""
    var connected = false
    var connecting = false
    var error: String?
    var deviceName: String?

    init(name: AxisName) {
        self.name = name
    }

    func reset() {
        peripheral = nil
        write = nil
        notify = nil
        writeQueue = []
        writeBusy = false
        buffer = ""
        connected = false
        connecting = false
    }
}

nonisolated private struct QueuedBLEWrite {
    let data: Data
    let type: CBCharacteristicWriteType
}

nonisolated private struct DiscoveredPeripheral {
    let peripheral: CBPeripheral
    let name: String

    func matches(_ axis: AxisName) -> Bool {
        let lowered = name.lowercased()
        return lowered.hasPrefix("motorsense-\(axis.rawValue)-") ||
            (lowered.contains("motorsense") && lowered.hasSuffix(axis.deviceSuffix))
    }
}

nonisolated extension BLEMount: CBCentralManagerDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        events(.bluetooth(central.state))
        if central.state != .poweredOn, ota != nil { failOTA("Bluetooth became unavailable. Reconnect before retrying.") }
        if connecting, bluetoothWait != nil, central.state == .poweredOn {
            bluetoothWait?.cancel()
            bluetoothWait = nil
            startScan()
        } else if connecting, bluetoothWait != nil {
            switch central.state {
            case .poweredOff:
                failConnect(MountError.bluetoothOff)
            case .unauthorized:
                failConnect(MountError.bluetoothUnauthorized)
            case .unsupported:
                failConnect(MountError.bluetoothUnavailable)
            default:
                break
            }
        }
    }

    func centralManager(
        _ central: CBCentralManager,
        didDiscover peripheral: CBPeripheral,
        advertisementData: [String: Any],
        rssi RSSI: NSNumber
    ) {
        let name = (advertisementData[CBAdvertisementDataLocalNameKey] as? String) ?? peripheral.name ?? ""
        discovered[peripheral.identifier] = DiscoveredPeripheral(peripheral: peripheral, name: name)
        let found = requestedAxes.allSatisfy { axis in
            discovered.values.contains { $0.matches(axis) }
        }
        if found { events(.log("Controllers discovered; finishing scan…")) }
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        peripheral.discoverServices([NUS.service])
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        finishCurrentConnect(error: error ?? MountError.timedOut("Connection failed"))
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        guard let session = session(for: peripheral) else { return }
        session.connected = false
        session.connecting = false
        session.write = nil
        session.notify = nil
        session.writeQueue = []
        session.writeBusy = false
        session.buffer = ""
        if let transfer = ota, transfer.axis == session.name, transfer.phase != .reboot {
            failOTA("Connection lost during the update. Reconnect and check the firmware version before retrying.")
        }
        events(.disconnected(session.name))
        events(.log("\(session.name.title) disconnected"))
    }
}

nonisolated extension BLEMount: CBPeripheralDelegate {
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        if let error {
            finishCurrentConnect(error: error)
            return
        }
        guard let service = peripheral.services?.first(where: { $0.uuid == NUS.service }) else {
            finishCurrentConnect(error: MountError.failed("NUS service missing"))
            return
        }
        peripheral.discoverCharacteristics([NUS.rx, NUS.tx], for: service)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        if let error {
            finishCurrentConnect(error: error)
            return
        }
        guard let session = session(for: peripheral) else { return }
        for characteristic in service.characteristics ?? [] {
            if characteristic.uuid == NUS.rx {
                session.write = characteristic
            } else if characteristic.uuid == NUS.tx {
                session.notify = characteristic
                peripheral.setNotifyValue(true, for: characteristic)
            }
        }
        if session.write == nil {
            finishCurrentConnect(error: MountError.failed("NUS RX characteristic missing"))
        } else if session.notify == nil {
            finishCurrentConnect(error: MountError.failed("NUS TX characteristic missing"))
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        if let error {
            finishCurrentConnect(error: error)
            return
        }
        guard let session = session(for: peripheral) else { return }
        if characteristic.uuid == NUS.tx, characteristic.isNotifying, session.write != nil {
            finishCurrentConnect(error: nil)
        }
    }

    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard error == nil, let data = characteristic.value, let session = session(for: peripheral) else { return }
        handleNotification(session, data: data)
    }

    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        guard let session = session(for: peripheral) else { return }
        session.writeBusy = false
        if let error {
            if ota?.axis == session.name { failOTA("BLE write failed: \(error.localizedDescription)"); return }
            events(.log("\(session.name.title) write failed: \(error.localizedDescription)"))
        }
        flush(session)
    }

    func peripheralIsReady(toSendWriteWithoutResponse peripheral: CBPeripheral) {
        guard let session = session(for: peripheral) else { return }
        flush(session)
    }
}

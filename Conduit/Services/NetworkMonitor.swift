import Foundation
import Network
import Observation

@MainActor
@Observable
final class NetworkMonitor {
    private(set) var isConnected: Bool = true
    private(set) var connectionType: NWInterface.InterfaceType?

    var onNetworkRestored: (() -> Void)?

    private nonisolated(unsafe) let monitor = NWPathMonitor()
    private let queue = DispatchQueue(label: "com.josephburton.conduit.networkmonitor")
    private var wasDisconnected = false

    init() {
        monitor.pathUpdateHandler = { [weak self] path in
            Task { @MainActor [weak self] in
                guard let self = self else { return }
                let nowConnected = path.status == .satisfied
                let interfaceType = path.availableInterfaces.first?.type

                let previouslyDisconnected = self.wasDisconnected

                self.isConnected = nowConnected
                self.connectionType = interfaceType

                if !nowConnected {
                    self.wasDisconnected = true
                } else if previouslyDisconnected {
                    self.wasDisconnected = false
                    self.onNetworkRestored?()
                }
            }
        }
        monitor.start(queue: queue)
    }

    deinit {
        monitor.cancel()
    }
}

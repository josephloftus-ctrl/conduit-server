import Foundation
import SwiftData

@MainActor
@Observable
final class ConnectionManager {
    let webSocket = WebSocketService()

    private(set) var currentServer: Server?
    private(set) var currentCwd: String?
    private(set) var streamingContent: String = ""
    private(set) var isStreaming: Bool = false
    private(set) var pendingPermission: PendingPermission?
    private(set) var errorMessage: String?
    private(set) var isReconnecting: Bool = false

    var connectionState: WebSocketService.State {
        webSocket.state
    }

    var onMessageComplete: ((String) -> Void)?

    struct PendingPermission: Identifiable {
        let id: String
        let action: String
        let detail: PermissionDetail
    }

    init() {
        setupHandlers()
    }

    private func setupHandlers() {
        webSocket.onMessage = { [weak self] message in
            self?.handleServerMessage(message)
        }

        webSocket.onStateChange = { [weak self] state in
            guard let self = self else { return }
            self.isReconnecting = state == .reconnecting
            if case .ready = state {
                self.errorMessage = nil
            }
        }

        webSocket.onError = { [weak self] error in
            self?.errorMessage = error
        }
    }

    func connect(to server: Server) {
        currentServer = server
        currentCwd = server.defaultCwd
        errorMessage = nil
        webSocket.resetRetryCount()
        webSocket.connect(to: server.url, token: server.token)
    }

    func disconnect() {
        webSocket.disconnect()
        currentServer = nil
        currentCwd = nil
        errorMessage = nil
        isReconnecting = false
    }

    func retry() {
        guard let server = currentServer else { return }
        errorMessage = nil
        webSocket.resetRetryCount()
        webSocket.connect(to: server.url, token: server.token)
    }

    func sendMessage(_ content: String) {
        streamingContent = ""
        isStreaming = true
        webSocket.send(.message(content: content, cwd: currentCwd))
    }

    func setCwd(_ cwd: String) {
        currentCwd = cwd
        webSocket.send(.setCwd(cwd: cwd))
    }

    func respondToPermission(granted: Bool) {
        guard let permission = pendingPermission else { return }
        webSocket.send(.permissionResponse(id: permission.id, granted: granted))
        pendingPermission = nil
    }

    private func handleServerMessage(_ message: ServerMessage) {
        switch message {
        case .hello(let server, let version, let capabilities):
            print("Connected to \(server) v\(version), capabilities: \(capabilities)")

        case .chunk(let content):
            streamingContent += content

        case .done:
            isStreaming = false
            onMessageComplete?(streamingContent)
            streamingContent = ""

        case .permission(let id, let action, let detail):
            if currentServer?.yoloMode == true {
                webSocket.send(.permissionResponse(id: id, granted: true))
            } else {
                pendingPermission = PendingPermission(id: id, action: action, detail: detail)
            }

        case .error(let errorMessage):
            print("Server error: \(errorMessage)")
            isStreaming = false
        }
    }
}

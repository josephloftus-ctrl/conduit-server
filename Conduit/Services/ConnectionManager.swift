import Foundation
import SwiftData

@MainActor
@Observable
final class ConnectionManager {
    let webSocket = WebSocketService()
    let claudeAPI = ClaudeAPIService()

    private(set) var currentServer: Server?
    private(set) var currentCwd: String?
    private(set) var streamingContent: String = ""
    private(set) var isStreaming: Bool = false
    var pendingPermission: PendingPermission?
    private(set) var errorMessage: String?
    private(set) var isReconnecting: Bool = false

    var connectionState: WebSocketService.State {
        guard let server = currentServer else { return .disconnected }
        switch server.type {
        case .websocket:
            return webSocket.state
        case .claudeAPI:
            switch claudeAPI.state {
            case .disconnected: return .disconnected
            case .ready: return .ready
            case .streaming: return .ready
            case .failed(let msg): return .failed(msg)
            }
        }
    }

    var onMessageComplete: ((String) -> Void)?

    struct PendingPermission: Identifiable {
        let id: String
        let action: String
        let detail: PermissionDetail
    }

    init() {
        setupWebSocketHandlers()
        setupClaudeAPIHandlers()
    }

    private func setupWebSocketHandlers() {
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

    private func setupClaudeAPIHandlers() {
        claudeAPI.onChunk = { [weak self] text in
            self?.streamingContent += text
        }

        claudeAPI.onComplete = { [weak self] fullText in
            self?.isStreaming = false
            self?.onMessageComplete?(fullText)
            self?.streamingContent = ""
        }

        claudeAPI.onError = { [weak self] error in
            self?.isStreaming = false
            self?.errorMessage = error
            self?.streamingContent = ""
        }
    }

    func connect(to server: Server) {
        currentServer = server
        currentCwd = server.defaultCwd
        errorMessage = nil

        switch server.type {
        case .websocket:
            webSocket.resetRetryCount()
            webSocket.connect(to: server.url, token: server.token)
        case .claudeAPI:
            guard let apiKey = server.token, !apiKey.isEmpty else {
                errorMessage = "API key is required"
                return
            }
            claudeAPI.configure(apiKey: apiKey, model: server.model ?? "claude-sonnet-4-5-20250929")
        }
    }

    func disconnect() {
        guard let server = currentServer else { return }
        switch server.type {
        case .websocket:
            webSocket.disconnect()
        case .claudeAPI:
            claudeAPI.disconnect()
        }
        currentServer = nil
        currentCwd = nil
        errorMessage = nil
        isReconnecting = false
    }

    func retry() {
        guard let server = currentServer else { return }
        errorMessage = nil

        switch server.type {
        case .websocket:
            webSocket.resetRetryCount()
            webSocket.connect(to: server.url, token: server.token)
        case .claudeAPI:
            guard let apiKey = server.token, !apiKey.isEmpty else {
                errorMessage = "API key is required"
                return
            }
            claudeAPI.configure(apiKey: apiKey, model: server.model ?? "claude-sonnet-4-5-20250929")
        }
    }

    func sendMessage(_ content: String, conversationHistory: [ClaudeAPIService.APIMessage] = []) {
        streamingContent = ""
        isStreaming = true

        guard let server = currentServer else { return }

        switch server.type {
        case .websocket:
            webSocket.send(.message(content: content, cwd: currentCwd))
        case .claudeAPI:
            var messages = conversationHistory
            messages.append(ClaudeAPIService.APIMessage(role: "user", content: content))
            claudeAPI.sendMessage(messages: messages)
        }
    }

    func setCwd(_ cwd: String) {
        currentCwd = cwd
        if currentServer?.type == .websocket {
            webSocket.send(.setCwd(cwd: cwd))
        }
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

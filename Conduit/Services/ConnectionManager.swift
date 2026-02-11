import Foundation
import SwiftData

@MainActor
@Observable
final class ConnectionManager {
    let webSocket = WebSocketService()
    let claudeAPI = ClaudeAPIService()
    let networkMonitor = NetworkMonitor()

    private(set) var currentServer: Server?
    private(set) var currentCwd: String?
    private(set) var streamingContent: String = ""
    private(set) var isStreaming: Bool = false
    var pendingPermission: PendingPermission?
    private(set) var errorMessage: String?
    private(set) var isReconnecting: Bool = false
    private(set) var activeToolCalls: [ToolCallState] = []
    private(set) var lastModel: String?

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

    var onMessageComplete: ((String, [ToolCallState]) -> Void)?

    struct PendingPermission: Identifiable {
        let id: String
        let action: String
        let detail: PermissionDetail
    }

    init() {
        setupWebSocketHandlers()
        setupClaudeAPIHandlers()
        setupNetworkMonitor()
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
            self?.onMessageComplete?(fullText, [])
            self?.streamingContent = ""
        }

        claudeAPI.onError = { [weak self] error in
            self?.isStreaming = false
            self?.errorMessage = error
            self?.streamingContent = ""
        }
    }

    private func setupNetworkMonitor() {
        networkMonitor.onNetworkRestored = { [weak self] in
            guard let self = self else { return }
            // Auto-retry if connected to a server but not in ready state
            if self.currentServer != nil && self.connectionState != .ready {
                self.retry()
            }
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

    func sendMessage(_ content: String, conversationHistory: [ClaudeAPIService.APIMessage] = [], systemPrompt: String? = nil) {
        streamingContent = ""
        activeToolCalls = []
        isStreaming = true

        guard let server = currentServer else { return }

        switch server.type {
        case .websocket:
            webSocket.send(.message(content: content, cwd: currentCwd))
        case .claudeAPI:
            var messages = conversationHistory
            messages.append(ClaudeAPIService.APIMessage(role: "user", content: content))
            claudeAPI.sendMessage(messages: messages, systemPrompt: systemPrompt)
        }
    }

    func stopGeneration() {
        guard let server = currentServer else { return }

        switch server.type {
        case .websocket:
            webSocket.cancelCurrentStream()
        case .claudeAPI:
            claudeAPI.cancelStreaming()
        }

        isStreaming = false

        // Save partial content if any
        let partial = streamingContent
        let tools = activeToolCalls
        if !partial.isEmpty {
            onMessageComplete?(partial, tools)
        }
        streamingContent = ""
        activeToolCalls = []
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

        case .typing:
            // Server indicates the model is thinking
            break

        case .chunk(let content):
            streamingContent += content

        case .done:
            isStreaming = false
            let tools = activeToolCalls
            onMessageComplete?(streamingContent, tools)
            streamingContent = ""
            activeToolCalls = []

        case .meta(let model, _, _):
            lastModel = model

        case .toolStart(let toolCallId, let name, let arguments):
            let tc = ToolCallState(
                id: toolCallId,
                name: name,
                arguments: arguments,
                status: .running
            )
            activeToolCalls.append(tc)

        case .toolDone(let toolCallId, let name, let result, let error):
            if let idx = activeToolCalls.firstIndex(where: { $0.id == toolCallId }) {
                activeToolCalls[idx].status = error != nil ? .failed : .done
                activeToolCalls[idx].result = result
                activeToolCalls[idx].error = error
            }

        case .permission(let id, let action, let detail):
            if currentServer?.yoloMode == true {
                webSocket.send(.permissionResponse(id: id, granted: true))
            } else {
                pendingPermission = PendingPermission(id: id, action: action, detail: detail)
            }

        case .push(let content, let title):
            // Could show a local notification — for now just log
            print("Push: \(title) — \(content.prefix(100))")

        case .error(let errorMessage):
            print("Server error: \(errorMessage)")
            isStreaming = false

        case .unknown(let type):
            print("Unknown server message type: \(type)")
        }
    }
}

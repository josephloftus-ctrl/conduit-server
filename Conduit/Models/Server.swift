import Foundation
import SwiftData

enum ServerType: String, Codable, CaseIterable {
    case websocket
    case claudeAPI
}

@Model
final class Server {
    var id: UUID
    var name: String
    var url: String
    var defaultCwd: String?
    var yoloMode: Bool
    var lastConnected: Date?
    var serverType: String = ServerType.websocket.rawValue
    var model: String?
    var systemPrompt: String?

    @Relationship(deleteRule: .cascade, inverse: \Conversation.server)
    var conversations: [Conversation] = []

    var type: ServerType {
        get { ServerType(rawValue: serverType) ?? .websocket }
        set { serverType = newValue.rawValue }
    }

    var token: String? {
        get { KeychainManager.loadToken(for: id) }
        set {
            if let value = newValue, !value.isEmpty {
                KeychainManager.saveToken(value, for: id)
            } else {
                KeychainManager.deleteToken(for: id)
            }
        }
    }

    var activeConversation: Conversation? {
        conversations.sorted { ($0.updatedAt) > ($1.updatedAt) }.first
    }

    init(
        id: UUID = UUID(),
        name: String,
        url: String,
        defaultCwd: String? = nil,
        yoloMode: Bool = false,
        type: ServerType = .websocket,
        model: String? = nil,
        systemPrompt: String? = nil
    ) {
        self.id = id
        self.name = name
        self.url = url
        self.defaultCwd = defaultCwd
        self.yoloMode = yoloMode
        self.serverType = type.rawValue
        self.model = model
        self.systemPrompt = systemPrompt
    }
}

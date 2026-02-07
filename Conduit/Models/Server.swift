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
    var token: String?
    var defaultCwd: String?
    var yoloMode: Bool
    var lastConnected: Date?
    var serverType: String = ServerType.websocket.rawValue
    var model: String?

    @Relationship(deleteRule: .cascade, inverse: \Message.server)
    var messages: [Message] = []

    var type: ServerType {
        get { ServerType(rawValue: serverType) ?? .websocket }
        set { serverType = newValue.rawValue }
    }

    init(
        id: UUID = UUID(),
        name: String,
        url: String,
        token: String? = nil,
        defaultCwd: String? = nil,
        yoloMode: Bool = false,
        type: ServerType = .websocket,
        model: String? = nil
    ) {
        self.id = id
        self.name = name
        self.url = url
        self.token = token
        self.defaultCwd = defaultCwd
        self.yoloMode = yoloMode
        self.serverType = type.rawValue
        self.model = model
    }
}

import Foundation
import SwiftData

@Model
final class Server {
    var id: UUID
    var name: String
    var url: String
    var token: String?
    var defaultCwd: String?
    var yoloMode: Bool
    var lastConnected: Date?

    @Relationship(deleteRule: .cascade, inverse: \Message.server)
    var messages: [Message] = []

    init(
        id: UUID = UUID(),
        name: String,
        url: String,
        token: String? = nil,
        defaultCwd: String? = nil,
        yoloMode: Bool = false
    ) {
        self.id = id
        self.name = name
        self.url = url
        self.token = token
        self.defaultCwd = defaultCwd
        self.yoloMode = yoloMode
    }
}

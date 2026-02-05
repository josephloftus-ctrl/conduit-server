import Foundation
import SwiftData

@Model
final class Message {
    var id: UUID
    var role: MessageRole
    var content: String
    var timestamp: Date
    var server: Server?

    init(
        id: UUID = UUID(),
        role: MessageRole,
        content: String,
        timestamp: Date = Date(),
        server: Server? = nil
    ) {
        self.id = id
        self.role = role
        self.content = content
        self.timestamp = timestamp
        self.server = server
    }
}

enum MessageRole: String, Codable {
    case user
    case assistant
}

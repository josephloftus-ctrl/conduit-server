import Foundation

// MARK: - Client -> Server Messages

enum ClientMessage: Encodable {
    case message(content: String, cwd: String?)
    case permissionResponse(id: String, granted: Bool)
    case setCwd(cwd: String)

    enum CodingKeys: String, CodingKey {
        case type, content, cwd, id, granted
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)

        switch self {
        case .message(let content, let cwd):
            try container.encode("message", forKey: .type)
            try container.encode(content, forKey: .content)
            try container.encodeIfPresent(cwd, forKey: .cwd)

        case .permissionResponse(let id, let granted):
            try container.encode("permission_response", forKey: .type)
            try container.encode(id, forKey: .id)
            try container.encode(granted, forKey: .granted)

        case .setCwd(let cwd):
            try container.encode("set_cwd", forKey: .type)
            try container.encode(cwd, forKey: .cwd)
        }
    }
}

// MARK: - Server -> Client Messages

enum ServerMessage: Decodable {
    case hello(server: String, version: String, capabilities: [String])
    case chunk(content: String)
    case done
    case permission(id: String, action: String, detail: PermissionDetail)
    case error(message: String)

    enum CodingKeys: String, CodingKey {
        case type, server, version, capabilities, content, id, action, detail, message
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(String.self, forKey: .type)

        switch type {
        case "hello":
            let server = try container.decode(String.self, forKey: .server)
            let version = try container.decode(String.self, forKey: .version)
            let capabilities = try container.decodeIfPresent([String].self, forKey: .capabilities) ?? []
            self = .hello(server: server, version: version, capabilities: capabilities)

        case "chunk":
            let content = try container.decode(String.self, forKey: .content)
            self = .chunk(content: content)

        case "done":
            self = .done

        case "permission":
            let id = try container.decode(String.self, forKey: .id)
            let action = try container.decode(String.self, forKey: .action)
            let detail = try container.decode(PermissionDetail.self, forKey: .detail)
            self = .permission(id: id, action: action, detail: detail)

        case "error":
            let message = try container.decode(String.self, forKey: .message)
            self = .error(message: message)

        default:
            throw DecodingError.dataCorruptedError(
                forKey: .type,
                in: container,
                debugDescription: "Unknown message type: \(type)"
            )
        }
    }
}

struct PermissionDetail: Decodable {
    let path: String?
    let diff: String?
    let command: String?
}

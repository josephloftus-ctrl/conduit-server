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
    case typing
    case chunk(content: String)
    case done
    case meta(model: String, inputTokens: Int, outputTokens: Int)
    case toolStart(toolCallId: String, name: String, arguments: [String: AnyCodable])
    case toolDone(toolCallId: String, name: String, result: String?, error: String?)
    case permission(id: String, action: String, detail: PermissionDetail)
    case push(content: String, title: String)
    case error(message: String)
    case unknown(type: String)

    enum CodingKeys: String, CodingKey {
        case type, server, version, capabilities, content, id, action, detail, message
        case model, inputTokens = "input_tokens", outputTokens = "output_tokens"
        case toolCallId = "tool_call_id", name, arguments, result, error, title
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

        case "typing":
            self = .typing

        case "chunk":
            let content = try container.decode(String.self, forKey: .content)
            self = .chunk(content: content)

        case "done":
            self = .done

        case "meta":
            let model = try container.decodeIfPresent(String.self, forKey: .model) ?? ""
            let inputTokens = try container.decodeIfPresent(Int.self, forKey: .inputTokens) ?? 0
            let outputTokens = try container.decodeIfPresent(Int.self, forKey: .outputTokens) ?? 0
            self = .meta(model: model, inputTokens: inputTokens, outputTokens: outputTokens)

        case "tool_start":
            let toolCallId = try container.decode(String.self, forKey: .toolCallId)
            let name = try container.decode(String.self, forKey: .name)
            let arguments = try container.decodeIfPresent([String: AnyCodable].self, forKey: .arguments) ?? [:]
            self = .toolStart(toolCallId: toolCallId, name: name, arguments: arguments)

        case "tool_done":
            let toolCallId = try container.decode(String.self, forKey: .toolCallId)
            let name = try container.decode(String.self, forKey: .name)
            let result = try container.decodeIfPresent(String.self, forKey: .result)
            let error = try container.decodeIfPresent(String.self, forKey: .error)
            self = .toolDone(toolCallId: toolCallId, name: name, result: result, error: error)

        case "permission":
            let id = try container.decode(String.self, forKey: .id)
            let action = try container.decode(String.self, forKey: .action)
            let detail = try container.decode(PermissionDetail.self, forKey: .detail)
            self = .permission(id: id, action: action, detail: detail)

        case "push":
            let content = try container.decodeIfPresent(String.self, forKey: .content) ?? ""
            let title = try container.decodeIfPresent(String.self, forKey: .title) ?? ""
            self = .push(content: content, title: title)

        case "error":
            let message = try container.decode(String.self, forKey: .message)
            self = .error(message: message)

        default:
            self = .unknown(type: type)
        }
    }
}

struct PermissionDetail: Decodable {
    let path: String?
    let diff: String?
    let command: String?
    let content: String?
    let oldText: String?
    let newText: String?

    enum CodingKeys: String, CodingKey {
        case path, diff, command, content
        case oldText = "old_text"
        case newText = "new_text"
    }
}

// MARK: - Tool Call State

struct ToolCallState: Identifiable {
    let id: String
    let name: String
    let arguments: [String: AnyCodable]
    var status: Status = .running
    var result: String?
    var error: String?

    enum Status {
        case running, done, failed
    }
}

// MARK: - AnyCodable helper for JSON arguments

struct AnyCodable: Decodable, CustomStringConvertible {
    let value: Any

    var description: String {
        if let str = value as? String { return str }
        if let num = value as? NSNumber { return num.stringValue }
        if let bool = value as? Bool { return bool ? "true" : "false" }
        return String(describing: value)
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let str = try? container.decode(String.self) {
            value = str
        } else if let int = try? container.decode(Int.self) {
            value = int
        } else if let double = try? container.decode(Double.self) {
            value = double
        } else if let bool = try? container.decode(Bool.self) {
            value = bool
        } else {
            value = ""
        }
    }
}

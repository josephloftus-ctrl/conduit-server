import Foundation

@MainActor
@Observable
final class ClaudeAPIService {
    enum State: Equatable {
        case disconnected
        case ready
        case streaming
        case failed(String)

        static func == (lhs: State, rhs: State) -> Bool {
            switch (lhs, rhs) {
            case (.disconnected, .disconnected),
                 (.ready, .ready),
                 (.streaming, .streaming):
                return true
            case (.failed(let a), .failed(let b)):
                return a == b
            default:
                return false
            }
        }
    }

    private(set) var state: State = .disconnected

    private var apiKey: String?
    private var modelId: String?
    private var currentTask: Task<Void, Never>?

    var onChunk: ((String) -> Void)?
    var onComplete: ((String) -> Void)?
    var onError: ((String) -> Void)?

    func configure(apiKey: String, model: String) {
        self.apiKey = apiKey
        self.modelId = model
        state = .ready
    }

    func disconnect() {
        currentTask?.cancel()
        currentTask = nil
        apiKey = nil
        modelId = nil
        state = .disconnected
    }

    func cancelStreaming() {
        currentTask?.cancel()
        currentTask = nil
        state = .ready
    }

    struct APIMessage: Encodable {
        let role: String
        let content: String
    }

    func sendMessage(messages: [APIMessage], systemPrompt: String? = nil) {
        guard let apiKey, let modelId else {
            onError?("Claude API not configured")
            return
        }

        currentTask?.cancel()

        state = .streaming
        var accumulated = ""

        currentTask = Task { [weak self] in
            do {
                let url = URL(string: "https://api.anthropic.com/v1/messages")!
                var request = URLRequest(url: url)
                request.httpMethod = "POST"
                request.setValue(apiKey, forHTTPHeaderField: "x-api-key")
                request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
                request.setValue("application/json", forHTTPHeaderField: "content-type")

                var body: [String: Any] = [
                    "model": modelId,
                    "max_tokens": 8192,
                    "stream": true,
                    "messages": messages.map { ["role": $0.role, "content": $0.content] }
                ]

                if let systemPrompt, !systemPrompt.isEmpty {
                    body["system"] = systemPrompt
                }

                request.httpBody = try JSONSerialization.data(withJSONObject: body)

                let (bytes, response) = try await URLSession.shared.bytes(for: request)

                guard let httpResponse = response as? HTTPURLResponse else {
                    throw ClaudeAPIError.invalidResponse
                }

                if httpResponse.statusCode != 200 {
                    var errorBody = ""
                    for try await line in bytes.lines {
                        errorBody += line
                    }
                    throw ClaudeAPIError.httpError(httpResponse.statusCode, errorBody)
                }

                for try await line in bytes.lines {
                    if Task.isCancelled { break }

                    guard line.hasPrefix("data: ") else { continue }
                    let jsonString = String(line.dropFirst(6))
                    if jsonString == "[DONE]" { break }

                    guard let data = jsonString.data(using: .utf8),
                          let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                          let type = event["type"] as? String else { continue }

                    if type == "content_block_delta",
                       let delta = event["delta"] as? [String: Any],
                       let text = delta["text"] as? String {
                        accumulated += text
                        self?.onChunk?(text)
                    }
                }

                if !Task.isCancelled {
                    self?.state = .ready
                    self?.onComplete?(accumulated)
                }
            } catch is CancellationError {
                // Task was cancelled, no action needed
            } catch {
                if !Task.isCancelled {
                    let message: String
                    if let apiError = error as? ClaudeAPIError {
                        message = apiError.localizedDescription
                    } else {
                        message = error.localizedDescription
                    }
                    self?.state = .failed(message)
                    self?.onError?(message)
                }
            }
        }
    }
}

enum ClaudeAPIError: LocalizedError {
    case invalidResponse
    case httpError(Int, String)

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "Invalid response from Claude API"
        case .httpError(let code, let body):
            return "HTTP \(code): \(body)"
        }
    }
}

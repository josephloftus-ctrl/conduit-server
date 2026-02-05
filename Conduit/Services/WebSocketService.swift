import Foundation

@MainActor
@Observable
final class WebSocketService {
    enum State: Equatable {
        case disconnected
        case connecting
        case ready
        case reconnecting
        case failed(String)

        static func == (lhs: State, rhs: State) -> Bool {
            switch (lhs, rhs) {
            case (.disconnected, .disconnected),
                 (.connecting, .connecting),
                 (.ready, .ready),
                 (.reconnecting, .reconnecting):
                return true
            case (.failed(let a), .failed(let b)):
                return a == b
            default:
                return false
            }
        }
    }

    private static let maxRetries = 5
    private static let retryDelay: TimeInterval = 2.0

    private(set) var state: State = .disconnected
    private(set) var errorMessage: String?
    private var webSocketTask: URLSessionWebSocketTask?
    private var session: URLSession
    private var url: URL?
    private var token: String?
    private var retryCount = 0

    var onMessage: ((ServerMessage) -> Void)?
    var onStateChange: ((State) -> Void)?
    var onError: ((String) -> Void)?

    init() {
        self.session = URLSession(configuration: .default)
    }

    func connect(to urlString: String, token: String? = nil) {
        guard let url = URL(string: urlString) else {
            print("Invalid URL: \(urlString)")
            return
        }

        self.url = url
        self.token = token

        var request = URLRequest(url: url)
        if let token = token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        state = .connecting
        onStateChange?(.connecting)

        webSocketTask = session.webSocketTask(with: request)
        webSocketTask?.resume()

        receiveMessage()
        // State will transition to .ready when hello message is received
    }

    func disconnect() {
        webSocketTask?.cancel(with: .normalClosure, reason: nil)
        webSocketTask = nil
        state = .disconnected
        onStateChange?(.disconnected)
    }

    func send(_ message: ClientMessage) {
        guard let task = webSocketTask else { return }

        do {
            let data = try JSONEncoder().encode(message)
            let string = String(data: data, encoding: .utf8) ?? ""
            task.send(.string(string)) { error in
                if let error = error {
                    print("Send error: \(error)")
                }
            }
        } catch {
            print("Encode error: \(error)")
        }
    }

    private func receiveMessage() {
        webSocketTask?.receive { [weak self] result in
            Task { @MainActor [weak self] in
                guard let self = self else { return }

                switch result {
                case .success(let message):
                    switch message {
                    case .string(let text):
                        self.handleMessage(text)
                    case .data(let data):
                        if let text = String(data: data, encoding: .utf8) {
                            self.handleMessage(text)
                        }
                    @unknown default:
                        break
                    }
                    self.receiveMessage()

                case .failure(let error):
                    print("Receive error: \(error)")
                    self.handleDisconnect()
                }
            }
        }
    }

    private func handleMessage(_ text: String) {
        guard let data = text.data(using: .utf8) else { return }

        do {
            let message = try JSONDecoder().decode(ServerMessage.self, from: data)

            if case .hello = message {
                retryCount = 0  // Reset retry count on successful connection
                errorMessage = nil
                state = .ready
                onStateChange?(.ready)
            }

            onMessage?(message)
        } catch {
            print("Decode error: \(error)")
        }
    }

    private func handleDisconnect() {
        retryCount += 1

        if retryCount > Self.maxRetries {
            let error = "Connection failed after \(Self.maxRetries) attempts"
            errorMessage = error
            state = .failed(error)
            onStateChange?(.failed(error))
            onError?(error)
            return
        }

        state = .reconnecting
        onStateChange?(.reconnecting)

        // Auto-reconnect after delay
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(Self.retryDelay))
            guard let self = self,
                  let url = self.url else { return }
            self.connect(to: url.absoluteString, token: self.token)
        }
    }

    func resetRetryCount() {
        retryCount = 0
        errorMessage = nil
    }
}

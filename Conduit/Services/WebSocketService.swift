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

    // Step 1: Exponential backoff constants
    private static let maxRetries = 8
    private static let baseDelay: TimeInterval = 1.0
    private static let maxDelay: TimeInterval = 30.0

    // Step 2: Connection timeout
    private static let connectionTimeout: TimeInterval = 10.0

    // Step 3: Heartbeat
    private static let pingInterval: TimeInterval = 30.0

    private(set) var state: State = .disconnected
    private(set) var errorMessage: String?
    private var webSocketTask: URLSessionWebSocketTask?
    private var session: URLSession
    private(set) var url: URL?
    private(set) var token: String?
    private var retryCount = 0
    private var timeoutTask: Task<Void, Never>?
    private var pingTask: Task<Void, Never>?

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

        // Tear down any existing connection before opening a new one
        cancelTimers()
        webSocketTask?.cancel(with: .normalClosure, reason: nil)
        webSocketTask = nil

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

        // Step 2: Start connection timeout
        startConnectionTimeout()

        receiveMessage()
        // State will transition to .ready when hello message is received
    }

    func disconnect() {
        cancelTimers()
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

    /// Cancel the current stream by disconnecting and immediately reconnecting.
    /// WebSocket has no cancel signal, so we tear down and re-establish.
    func cancelCurrentStream() {
        guard let url = self.url else { return }
        let savedToken = self.token

        cancelTimers()
        webSocketTask?.cancel(with: .normalClosure, reason: nil)
        webSocketTask = nil

        // Immediately reconnect
        connect(to: url.absoluteString, token: savedToken)
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
                // Step 2: Cancel timeout on successful hello
                timeoutTask?.cancel()
                timeoutTask = nil
                state = .ready
                onStateChange?(.ready)
                // Step 3: Start heartbeat on connection
                startHeartbeat()
            }

            onMessage?(message)
        } catch {
            print("Decode error: \(error)")
        }
    }

    private func handleDisconnect() {
        cancelTimers()
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

        // Step 1: Exponential backoff with jitter
        let exponentialDelay = Self.baseDelay * pow(2.0, Double(retryCount - 1))
        let cappedDelay = min(exponentialDelay, Self.maxDelay)
        let jitter = Double.random(in: 0...(cappedDelay * 0.3))
        let delay = cappedDelay + jitter

        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(delay))
            guard let self = self,
                  let url = self.url else { return }
            self.connect(to: url.absoluteString, token: self.token)
        }
    }

    // MARK: - Connection Timeout (Step 2)

    private func startConnectionTimeout() {
        timeoutTask?.cancel()
        timeoutTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .seconds(Self.connectionTimeout))
            guard let self = self, !Task.isCancelled else { return }
            if self.state == .connecting {
                print("Connection timeout after \(Self.connectionTimeout)s")
                self.webSocketTask?.cancel(with: .abnormalClosure, reason: nil)
                self.webSocketTask = nil
                self.handleDisconnect()
            }
        }
    }

    // MARK: - Heartbeat (Step 3)

    private func startHeartbeat() {
        pingTask?.cancel()
        pingTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(Self.pingInterval))
                guard let self = self, !Task.isCancelled else { return }
                guard let task = self.webSocketTask else { return }

                task.sendPing { error in
                    Task { @MainActor [weak self] in
                        if let error = error {
                            print("Ping failed: \(error)")
                            self?.handleDisconnect()
                        }
                    }
                }
            }
        }
    }

    private func cancelTimers() {
        timeoutTask?.cancel()
        timeoutTask = nil
        pingTask?.cancel()
        pingTask = nil
    }

    func resetRetryCount() {
        retryCount = 0
        errorMessage = nil
    }
}

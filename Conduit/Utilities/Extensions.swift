import Foundation

extension String {
    var isValidWebSocketURL: Bool {
        guard let url = URL(string: self) else { return false }
        return url.scheme == "ws" || url.scheme == "wss"
    }
}

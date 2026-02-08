import SwiftUI

struct ServerRowView: View {
    let server: Server

    var body: some View {
        HStack(spacing: 14) {
            // Server type icon with glass backing
            Image(systemName: server.type == .claudeAPI ? "brain" : "antenna.radiowaves.left.and.right")
                .font(.title3)
                .foregroundStyle(server.type == .claudeAPI ? .purple : .blue)
                .frame(width: 40, height: 40)
                .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 10))

            VStack(alignment: .leading, spacing: 4) {
                Text(server.name)
                    .font(.headline)

                if server.type == .claudeAPI, let model = server.model {
                    Text(modelDisplayName(model))
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                } else if let lastMessage = server.messages.last {
                    Text(lastMessage.content)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                } else {
                    Text("No messages")
                        .font(.subheadline)
                        .foregroundStyle(.tertiary)
                }
            }

            Spacer()

            // Status pill with glass backing
            HStack(spacing: 6) {
                if server.yoloMode {
                    Image(systemName: "bolt.fill")
                        .font(.caption2)
                        .foregroundStyle(.yellow)
                }

                Circle()
                    .fill(statusColor)
                    .frame(width: 8, height: 8)

                Text(statusLabel)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .glassEffect(.regular, in: Capsule())
        }
        .padding(.vertical, 6)
    }

    private var statusColor: Color {
        guard let lastConnected = server.lastConnected else {
            return .gray
        }
        let fiveMinutesAgo = Date().addingTimeInterval(-5 * 60)
        return lastConnected > fiveMinutesAgo ? .green : .gray
    }

    private var statusLabel: String {
        guard let lastConnected = server.lastConnected else {
            return "New"
        }
        let fiveMinutesAgo = Date().addingTimeInterval(-5 * 60)
        return lastConnected > fiveMinutesAgo ? "Active" : timeAgo(lastConnected)
    }

    private func timeAgo(_ date: Date) -> String {
        let interval = Date().timeIntervalSince(date)
        if interval < 3600 {
            return "\(Int(interval / 60))m ago"
        } else if interval < 86400 {
            return "\(Int(interval / 3600))h ago"
        } else {
            return "\(Int(interval / 86400))d ago"
        }
    }

    private func modelDisplayName(_ id: String) -> String {
        switch id {
        case "claude-sonnet-4-5-20250929": return "Sonnet 4.5"
        case "claude-opus-4-6": return "Opus 4.6"
        case "claude-haiku-4-5-20251001": return "Haiku 4.5"
        default: return id
        }
    }
}

#Preview {
    ServerRowView(server: Server(name: "Home Server", url: "wss://localhost:8080"))
        .padding()
}

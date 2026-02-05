import SwiftUI

struct ServerRowView: View {
    let server: Server

    var body: some View {
        HStack(spacing: 12) {
            Circle()
                .fill(statusColor)
                .frame(width: 12, height: 12)

            VStack(alignment: .leading, spacing: 4) {
                Text(server.name)
                    .font(.headline)

                if let lastMessage = server.messages.last {
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

            if server.yoloMode {
                Image(systemName: "bolt.fill")
                    .foregroundStyle(.yellow)
                    .font(.caption)
            }
        }
        .padding(.vertical, 4)
    }

    private var statusColor: Color {
        guard let lastConnected = server.lastConnected else {
            return .gray
        }
        // Show green if connected within the last 5 minutes
        let fiveMinutesAgo = Date().addingTimeInterval(-5 * 60)
        return lastConnected > fiveMinutesAgo ? .green : .gray
    }
}

#Preview {
    ServerRowView(server: Server(name: "Home Server", url: "wss://localhost:8080"))
        .padding()
}

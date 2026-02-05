import SwiftUI

struct MessageBubble: View {
    let content: String
    let role: MessageRole
    var isStreaming: Bool = false

    init(message: Message) {
        self.content = message.content
        self.role = message.role
        self.isStreaming = false
    }

    init(content: String, role: MessageRole, isStreaming: Bool = false) {
        self.content = content
        self.role = role
        self.isStreaming = isStreaming
    }

    var body: some View {
        HStack {
            if role == .user {
                Spacer(minLength: 60)
            }

            VStack(alignment: role == .user ? .trailing : .leading, spacing: 4) {
                Text(content)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(backgroundColor)
                    .foregroundStyle(foregroundColor)
                    .clipShape(RoundedRectangle(cornerRadius: 18))

                if isStreaming {
                    HStack(spacing: 4) {
                        ProgressView()
                            .scaleEffect(0.7)
                        Text("Streaming...")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }

            if role == .assistant {
                Spacer(minLength: 60)
            }
        }
    }

    private var backgroundColor: Color {
        role == .user ? .blue : Color(.systemGray5)
    }

    private var foregroundColor: Color {
        role == .user ? .white : .primary
    }
}

#Preview {
    VStack(spacing: 12) {
        MessageBubble(content: "Hello, can you help me?", role: .user)
        MessageBubble(content: "Of course! What do you need?", role: .assistant)
        MessageBubble(content: "Working on it...", role: .assistant, isStreaming: true)
    }
    .padding()
}

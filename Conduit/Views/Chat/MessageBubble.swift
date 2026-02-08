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

            VStack(alignment: role == .user ? .trailing : .leading, spacing: 6) {
                if role == .assistant {
                    formattedContent
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                        .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 18))
                } else {
                    formattedContent
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                        .background(Color.conduitUserBubble)
                        .foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 18))
                }

                if isStreaming {
                    streamingIndicator
                }
            }

            if role == .assistant {
                Spacer(minLength: 60)
            }
        }
    }

    @ViewBuilder
    private var formattedContent: some View {
        if let attributed = try? AttributedString(markdown: content, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
            Text(attributed)
                .font(.body)
                .lineSpacing(2)
        } else {
            Text(content)
                .font(.body)
                .lineSpacing(2)
        }
    }

    private var streamingIndicator: some View {
        HStack(spacing: 6) {
            HStack(spacing: 3) {
                ForEach(0..<3) { i in
                    Circle()
                        .fill(.secondary)
                        .frame(width: 4, height: 4)
                        .opacity(0.5)
                }
            }

            Text("Streaming")
                .font(.system(.caption2, design: .rounded).weight(.medium))
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .glassEffect(.regular, in: Capsule())
    }
}

#Preview {
    VStack(spacing: 12) {
        MessageBubble(content: "Hello, can you help me?", role: .user)
        MessageBubble(content: "Of course! I can help with **bold** and *italic* text.", role: .assistant)
        MessageBubble(content: "Working on it...", role: .assistant, isStreaming: true)
    }
    .padding()
}

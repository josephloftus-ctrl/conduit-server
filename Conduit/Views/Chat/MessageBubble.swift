import SwiftUI

struct MessageBubble: View {
    let content: String
    let role: MessageRole
    var isStreaming: Bool = false
    var onRegenerate: (() -> Void)?

    init(message: Message, onRegenerate: (() -> Void)? = nil) {
        self.content = message.content
        self.role = message.role
        self.isStreaming = false
        self.onRegenerate = onRegenerate
    }

    init(content: String, role: MessageRole, isStreaming: Bool = false, onRegenerate: (() -> Void)? = nil) {
        self.content = content
        self.role = role
        self.isStreaming = isStreaming
        self.onRegenerate = onRegenerate
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
            .contextMenu {
                Button {
                    UIPasteboard.general.string = content
                } label: {
                    Label("Copy", systemImage: "doc.on.doc")
                }

                if role == .assistant, let onRegenerate {
                    Button {
                        onRegenerate()
                    } label: {
                        Label("Regenerate", systemImage: "arrow.clockwise")
                    }
                }
            }

            if role == .assistant {
                Spacer(minLength: 60)
            }
        }
    }

    @ViewBuilder
    private var formattedContent: some View {
        let blocks = MarkdownParser.parse(content)
        if blocks.count == 1, case .text(let text) = blocks[0] {
            // Simple text — use inline markdown
            if let attributed = try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
                Text(attributed)
                    .font(.body)
                    .lineSpacing(2)
            } else {
                Text(text)
                    .font(.body)
                    .lineSpacing(2)
            }
        } else {
            // Mixed content with code blocks
            VStack(alignment: .leading, spacing: 10) {
                ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                    switch block {
                    case .text(let text):
                        if let attributed = try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
                            Text(attributed)
                                .font(.body)
                                .lineSpacing(2)
                        } else {
                            Text(text)
                                .font(.body)
                                .lineSpacing(2)
                        }
                    case .codeBlock(let language, let code):
                        CodeBlockView(language: language, code: code)
                    }
                }
            }
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
        MessageBubble(content: "Of course! Here's some code:\n\n```swift\nlet x = 42\nprint(x)\n```\n\nThat should work!", role: .assistant)
        MessageBubble(content: "Working on it...", role: .assistant, isStreaming: true)
    }
    .padding()
}

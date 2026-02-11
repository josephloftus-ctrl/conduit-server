import SwiftUI

struct MessageBubble: View {
    let content: String
    let role: MessageRole
    var isStreaming: Bool = false
    var toolCalls: [ToolCallState] = []
    var onRegenerate: (() -> Void)?

    init(message: Message, onRegenerate: (() -> Void)? = nil) {
        self.content = message.content
        self.role = message.role
        self.isStreaming = false
        self.onRegenerate = onRegenerate
    }

    init(content: String, role: MessageRole, isStreaming: Bool = false, toolCalls: [ToolCallState] = [], onRegenerate: (() -> Void)? = nil) {
        self.content = content
        self.role = role
        self.isStreaming = isStreaming
        self.toolCalls = toolCalls
        self.onRegenerate = onRegenerate
    }

    var body: some View {
        HStack {
            if role == .user {
                Spacer(minLength: 60)
            }

            VStack(alignment: role == .user ? .trailing : .leading, spacing: 6) {
                // Tool call indicators (before text content)
                if !toolCalls.isEmpty {
                    VStack(spacing: 4) {
                        ForEach(toolCalls) { tc in
                            ToolCallRow(toolCall: tc)
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 12)
                    .padding(.bottom, content.isEmpty ? 12 : 0)
                    .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 18))
                }

                if role == .assistant && !content.isEmpty {
                    formattedContent
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                        .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 18))
                } else if role == .user {
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

// MARK: - Tool Call Row

struct ToolCallRow: View {
    let toolCall: ToolCallState

    @State private var expanded = false

    private var iconName: String {
        switch toolCall.name {
        // Conduit tools
        case "read_file": return "doc.text"
        case "list_directory": return "folder"
        case "glob_files": return "magnifyingglass"
        case "grep": return "text.magnifyingglass"
        case "write_file": return "doc.badge.plus"
        case "edit_file": return "doc.badge.ellipsis"
        case "run_command": return "terminal"
        // Claude Code tools
        case "Read": return "doc.text"
        case "Write": return "doc.badge.plus"
        case "Edit": return "doc.badge.ellipsis"
        case "Bash": return "terminal"
        case "Glob", "Grep": return "magnifyingglass"
        case "WebFetch", "WebSearch": return "globe"
        case "Task": return "list.bullet"
        case "NotebookEdit": return "doc.badge.ellipsis"
        default: return "wrench"
        }
    }

    private var summary: String {
        if let path = toolCall.arguments["path"] {
            return path.description
        }
        if let filePath = toolCall.arguments["file_path"] {
            return filePath.description
        }
        if let pattern = toolCall.arguments["pattern"] {
            return pattern.description
        }
        if let command = toolCall.arguments["command"] {
            let cmd = command.description
            return String(cmd.prefix(50))
        }
        if let query = toolCall.arguments["query"] {
            return String(query.description.prefix(50))
        }
        if let url = toolCall.arguments["url"] {
            return String(url.description.prefix(50))
        }
        return ""
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    expanded.toggle()
                }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: iconName)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .frame(width: 16)

                    Text(toolCall.name)
                        .font(.system(.caption, design: .rounded).weight(.medium))

                    if !summary.isEmpty {
                        Text(summary)
                            .font(.system(.caption2, design: .monospaced))
                            .foregroundStyle(.tertiary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }

                    Spacer()

                    // Status indicator
                    switch toolCall.status {
                    case .running:
                        ProgressView()
                            .scaleEffect(0.6)
                            .frame(width: 14, height: 14)
                    case .done:
                        Image(systemName: "checkmark.circle.fill")
                            .font(.caption)
                            .foregroundStyle(Color.conduitSuccess)
                    case .failed:
                        Image(systemName: "xmark.circle.fill")
                            .font(.caption)
                            .foregroundStyle(Color.conduitError)
                    }

                    Image(systemName: "chevron.right")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .rotationEffect(.degrees(expanded ? 90 : 0))
                }
                .padding(.vertical, 6)
            }
            .buttonStyle(.plain)

            if expanded, let result = toolCall.result ?? toolCall.error {
                Text(result.prefix(500))
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(12)
                    .padding(.vertical, 6)
                    .padding(.leading, 24)
            }
        }
    }
}

#Preview {
    VStack(spacing: 12) {
        MessageBubble(content: "Hello, can you help me?", role: .user)
        MessageBubble(content: "Of course! Here's some code:\n\n```swift\nlet x = 42\nprint(x)\n```\n\nThat should work!", role: .assistant)
        MessageBubble(content: "Working on it...", role: .assistant, isStreaming: true)
        MessageBubble(
            content: "Here are your files.",
            role: .assistant,
            toolCalls: [
                ToolCallState(id: "1", name: "list_directory", arguments: ["path": AnyCodable(from: "~/Documents" as Any)!], status: .done, result: "file1.txt\nfile2.pdf"),
                ToolCallState(id: "2", name: "read_file", arguments: ["path": AnyCodable(from: "~/test.txt" as Any)!], status: .running),
            ]
        )
    }
    .padding()
}

// Helper for preview
private extension AnyCodable {
    init?(from value: Any) {
        self.value = value
    }
}

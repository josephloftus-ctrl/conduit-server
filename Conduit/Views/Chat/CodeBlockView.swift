import SwiftUI

struct CodeBlockView: View {
    let language: String?
    let code: String

    @State private var showCopied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header bar
            HStack {
                Text(language ?? "code")
                    .font(.system(.caption, design: .monospaced).weight(.medium))
                    .foregroundStyle(.secondary)

                Spacer()

                Button {
                    UIPasteboard.general.string = code
                    showCopied = true
                    Task {
                        try? await Task.sleep(for: .seconds(2))
                        showCopied = false
                    }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: showCopied ? "checkmark" : "doc.on.doc")
                            .font(.caption2)
                        Text(showCopied ? "Copied" : "Copy")
                            .font(.system(.caption2, design: .rounded).weight(.medium))
                    }
                    .foregroundStyle(showCopied ? Color.conduitSuccess : .secondary)
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)

            Divider()
                .opacity(0.3)

            // Code body
            ScrollView(.horizontal, showsIndicators: false) {
                Text(code)
                    .font(.system(.callout, design: .monospaced))
                    .textSelection(.enabled)
                    .padding(12)
            }
        }
        .background(Color.conduitCodeBackground)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

#Preview {
    VStack(spacing: 12) {
        CodeBlockView(
            language: "swift",
            code: "let greeting = \"Hello, World!\"\nprint(greeting)"
        )
        CodeBlockView(
            language: nil,
            code: "npm install something"
        )
    }
    .padding()
}

import SwiftUI
import SwiftData

struct ServerSettingsView: View {
    @Bindable var server: Server
    @Environment(\.dismiss) private var dismiss
    @Environment(\.modelContext) private var modelContext

    @State private var showingDeleteConfirmation = false
    @State private var editToken: String = ""
    @State private var editSystemPrompt: String = ""

    private static let claudeModels: [(id: String, label: String)] = [
        ("claude-sonnet-4-5-20250929", "Sonnet 4.5"),
        ("claude-opus-4-6", "Opus 4.6"),
        ("claude-haiku-4-5-20251001", "Haiku 4.5"),
    ]

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    TextField("Name", text: $server.name)

                    LabeledContent("Type") {
                        Text(server.type == .websocket ? "WebSocket" : "Claude API")
                            .foregroundStyle(.secondary)
                    }
                }

                if server.type == .websocket {
                    Section("Connection") {
                        TextField("URL", text: $server.url)
                            .autocapitalization(.none)
                            .autocorrectionDisabled()
                    }

                    Section("Authentication") {
                        SecureField("Token", text: $editToken)
                            .autocapitalization(.none)
                            .autocorrectionDisabled()
                    }

                    Section("Working Directory") {
                        TextField("Default directory", text: Binding(
                            get: { server.defaultCwd ?? "" },
                            set: { server.defaultCwd = $0.isEmpty ? nil : $0 }
                        ))
                        .autocapitalization(.none)
                        .autocorrectionDisabled()
                    }

                    Section {
                        Toggle("YOLO Mode", isOn: $server.yoloMode)
                    } footer: {
                        Text("Auto-approve all permission requests")
                    }
                } else {
                    Section("Authentication") {
                        SecureField("API Key", text: $editToken)
                            .autocapitalization(.none)
                            .autocorrectionDisabled()
                    }

                    Section("Model") {
                        Picker("Model", selection: Binding(
                            get: { server.model ?? "claude-sonnet-4-5-20250929" },
                            set: { server.model = $0 }
                        )) {
                            ForEach(Self.claudeModels, id: \.id) { model in
                                Text(model.label).tag(model.id)
                            }
                        }
                    }

                    Section("System Prompt") {
                        TextField("System prompt (optional)", text: $editSystemPrompt, axis: .vertical)
                            .lineLimit(3...8)
                    } footer: {
                        Text("Instructions sent with every message to set the AI's behavior")
                    }
                }

                Section {
                    Button("Clear All Conversations", role: .destructive) {
                        for conversation in server.conversations {
                            modelContext.delete(conversation)
                        }
                    }
                    .buttonStyle(.glass)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        // Save token to keychain
                        server.token = editToken.isEmpty ? nil : editToken
                        // Save system prompt
                        server.systemPrompt = editSystemPrompt.isEmpty ? nil : editSystemPrompt
                        dismiss()
                    }
                }
            }
            .onAppear {
                editToken = server.token ?? ""
                editSystemPrompt = server.systemPrompt ?? ""
            }
        }
    }
}

#Preview {
    ServerSettingsView(server: Server(name: "Test", url: "wss://localhost:8080"))
        .modelContainer(for: [Server.self, Conversation.self, Message.self], inMemory: true)
}

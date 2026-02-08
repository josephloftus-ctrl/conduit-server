import SwiftUI

struct ServerSettingsView: View {
    @Bindable var server: Server
    @Environment(\.dismiss) private var dismiss

    @State private var showingDeleteConfirmation = false

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
                        SecureField("Token", text: Binding(
                            get: { server.token ?? "" },
                            set: { server.token = $0.isEmpty ? nil : $0 }
                        ))
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
                        SecureField("API Key", text: Binding(
                            get: { server.token ?? "" },
                            set: { server.token = $0.isEmpty ? nil : $0 }
                        ))
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
                }

                Section {
                    Button("Clear Message History", role: .destructive) {
                        server.messages.removeAll()
                    }
                    .buttonStyle(.glass)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
    }
}

#Preview {
    ServerSettingsView(server: Server(name: "Test", url: "wss://localhost:8080"))
}

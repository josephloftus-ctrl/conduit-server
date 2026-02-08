import SwiftUI
import SwiftData

struct AddServerView: View {
    @Environment(\.modelContext) private var modelContext
    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var selectedType: ServerType = .websocket

    // WebSocket fields
    @State private var url = ""
    @State private var token = ""
    @State private var defaultCwd = ""
    @State private var yoloMode = false

    // Claude API fields
    @State private var apiKey = ""
    @State private var selectedModel = "claude-sonnet-4-5-20250929"

    @State private var isConnecting = false
    @State private var errorMessage: String?

    private static let claudeModels: [(id: String, label: String)] = [
        ("claude-sonnet-4-5-20250929", "Sonnet 4.5"),
        ("claude-opus-4-6", "Opus 4.6"),
        ("claude-haiku-4-5-20251001", "Haiku 4.5"),
    ]

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name", text: $name)
                        .textContentType(.name)

                    Picker("Type", selection: $selectedType) {
                        Text("WebSocket").tag(ServerType.websocket)
                        Text("Claude API").tag(ServerType.claudeAPI)
                    }
                    .pickerStyle(.segmented)
                }

                if selectedType == .websocket {
                    Section {
                        TextField("URL", text: $url)
                            .textContentType(.URL)
                            .keyboardType(.URL)
                            .autocapitalization(.none)
                            .autocorrectionDisabled()
                    }

                    Section {
                        SecureField("Token (optional)", text: $token)
                            .autocapitalization(.none)
                            .autocorrectionDisabled()
                    } footer: {
                        Text("Authentication token if your server requires it")
                    }

                    Section {
                        TextField("Default directory (optional)", text: $defaultCwd)
                            .autocapitalization(.none)
                            .autocorrectionDisabled()
                    } footer: {
                        Text("Working directory for commands, e.g. /home/user/projects")
                    }

                    Section {
                        Toggle("YOLO Mode", isOn: $yoloMode)
                    } footer: {
                        Text("Auto-approve all permission requests. Use with caution.")
                    }
                } else {
                    Section {
                        SecureField("API Key", text: $apiKey)
                            .autocapitalization(.none)
                            .autocorrectionDisabled()
                    } footer: {
                        Text("Your Anthropic API key")
                    }

                    Section {
                        Picker("Model", selection: $selectedModel) {
                            ForEach(Self.claudeModels, id: \.id) { model in
                                Text(model.label).tag(model.id)
                            }
                        }
                    }
                }

                if let error = errorMessage {
                    Section {
                        Text(error)
                            .foregroundStyle(Color.conduitError)
                    }
                }
            }
            .navigationTitle("Add Server")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                }

                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        saveServer()
                    }
                    .buttonStyle(.glassProminent)
                    .disabled(!isValid || isConnecting)
                }
            }
        }
    }

    private var isValid: Bool {
        let hasName = !name.trimmingCharacters(in: .whitespaces).isEmpty
        switch selectedType {
        case .websocket:
            let trimmedURL = url.trimmingCharacters(in: .whitespaces)
            return hasName && !trimmedURL.isEmpty && (trimmedURL.hasPrefix("ws://") || trimmedURL.hasPrefix("wss://"))
        case .claudeAPI:
            return hasName && !apiKey.trimmingCharacters(in: .whitespaces).isEmpty
        }
    }

    private func saveServer() {
        let server: Server
        switch selectedType {
        case .websocket:
            server = Server(
                name: name.trimmingCharacters(in: .whitespaces),
                url: url.trimmingCharacters(in: .whitespaces),
                token: token.isEmpty ? nil : token,
                defaultCwd: defaultCwd.isEmpty ? nil : defaultCwd,
                yoloMode: yoloMode,
                type: .websocket
            )
        case .claudeAPI:
            server = Server(
                name: name.trimmingCharacters(in: .whitespaces),
                url: "",
                token: apiKey.trimmingCharacters(in: .whitespaces),
                type: .claudeAPI,
                model: selectedModel
            )
        }

        modelContext.insert(server)
        dismiss()
    }
}

#Preview {
    AddServerView()
        .modelContainer(for: Server.self, inMemory: true)
}

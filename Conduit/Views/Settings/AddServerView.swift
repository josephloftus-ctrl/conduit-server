import SwiftUI
import SwiftData

struct AddServerView: View {
    @Environment(\.modelContext) private var modelContext
    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var url = ""
    @State private var token = ""
    @State private var defaultCwd = ""
    @State private var yoloMode = false

    @State private var isConnecting = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name", text: $name)
                        .textContentType(.name)

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

                if let error = errorMessage {
                    Section {
                        Text(error)
                            .foregroundStyle(.red)
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
                    .disabled(!isValid || isConnecting)
                }
            }
        }
    }

    private var isValid: Bool {
        !name.trimmingCharacters(in: .whitespaces).isEmpty &&
        !url.trimmingCharacters(in: .whitespaces).isEmpty &&
        (url.hasPrefix("ws://") || url.hasPrefix("wss://"))
    }

    private func saveServer() {
        let server = Server(
            name: name.trimmingCharacters(in: .whitespaces),
            url: url.trimmingCharacters(in: .whitespaces),
            token: token.isEmpty ? nil : token,
            defaultCwd: defaultCwd.isEmpty ? nil : defaultCwd,
            yoloMode: yoloMode
        )

        modelContext.insert(server)
        dismiss()
    }
}

#Preview {
    AddServerView()
        .modelContainer(for: Server.self, inMemory: true)
}

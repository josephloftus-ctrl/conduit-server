import SwiftUI

struct ServerSettingsView: View {
    @Bindable var server: Server
    @Environment(\.dismiss) private var dismiss

    @State private var showingDeleteConfirmation = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    TextField("Name", text: $server.name)

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

                Section {
                    Button("Clear Message History", role: .destructive) {
                        server.messages.removeAll()
                    }
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

import SwiftUI
import SwiftData

struct ChatView: View {
    @Environment(\.modelContext) private var modelContext

    let server: Server

    @State private var connectionManager = ConnectionManager()
    @State private var inputText = ""
    @State private var showingSettings = false

    private var isConnected: Bool {
        connectionManager.connectionState == .ready
    }

    var body: some View {
        GlassEffectContainer {
            VStack(spacing: 0) {
                // Connection status banner
                statusBanner

                // Messages
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 12) {
                            ForEach(server.messages) { message in
                                MessageBubble(message: message)
                                    .id(message.id)
                            }

                            // Streaming content
                            if connectionManager.isStreaming && !connectionManager.streamingContent.isEmpty {
                                MessageBubble(
                                    content: connectionManager.streamingContent,
                                    role: .assistant,
                                    isStreaming: true
                                )
                                .id("streaming")
                            }
                        }
                        .padding()
                    }
                    .onChange(of: server.messages.count) {
                        if let lastMessage = server.messages.last {
                            withAnimation {
                                proxy.scrollTo(lastMessage.id, anchor: .bottom)
                            }
                        }
                    }
                    .onChange(of: connectionManager.streamingContent) {
                        withAnimation {
                            proxy.scrollTo("streaming", anchor: .bottom)
                        }
                    }
                }

                // Input bar
                inputBar
            }
        }
        .conduitBackground()
        .navigationTitle(server.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackgroundVisibility(.visible, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                HStack(spacing: 12) {
                    // Status indicator
                    HStack(spacing: 5) {
                        Circle()
                            .fill(isConnected ? .conduitSuccess : .conduitInactive)
                            .frame(width: 7, height: 7)

                        if server.yoloMode {
                            Image(systemName: "bolt.fill")
                                .font(.caption2)
                                .foregroundStyle(.conduitWarning)
                        }
                    }

                    Button(action: { showingSettings = true }) {
                        Image(systemName: "gearshape")
                    }
                }
            }
        }
        .sheet(isPresented: $showingSettings) {
            ServerSettingsView(server: server)
        }
        .sheet(item: $connectionManager.pendingPermission) { permission in
            PermissionModal(
                permission: permission,
                onResponse: { granted in
                    connectionManager.respondToPermission(granted: granted)
                }
            )
        }
        .onAppear {
            setupConnectionManager()
            connectionManager.connect(to: server)
        }
        .onDisappear {
            connectionManager.disconnect()
        }
    }

    @ViewBuilder
    private var statusBanner: some View {
        if let error = connectionManager.errorMessage {
            HStack {
                Image(systemName: "exclamationmark.triangle.fill")
                Text(error)
                    .font(.system(.subheadline, design: .rounded).weight(.medium))
                Spacer()
                Button("Retry") {
                    connectionManager.retry()
                }
                .buttonStyle(.glass)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .glassEffect(.regular.tint(.conduitError), in: RoundedRectangle(cornerRadius: 12))
            .padding(.horizontal)
            .padding(.top, 4)
        } else if connectionManager.isReconnecting {
            HStack(spacing: 8) {
                ProgressView()
                Text("Reconnecting...")
                    .font(.system(.subheadline, design: .rounded).weight(.medium))
            }
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity)
            .glassEffect(.regular.tint(.conduitWarning), in: RoundedRectangle(cornerRadius: 12))
            .padding(.horizontal)
            .padding(.top, 4)
        } else if connectionManager.connectionState == .connecting {
            HStack(spacing: 8) {
                ProgressView()
                Text("Connecting...")
                    .font(.system(.subheadline, design: .rounded).weight(.medium))
            }
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity)
            .glassEffect(.regular.tint(.conduitAccent), in: RoundedRectangle(cornerRadius: 12))
            .padding(.horizontal)
            .padding(.top, 4)
        }
    }

    private var inputBar: some View {
        HStack(spacing: 12) {
            TextField("Message", text: $inputText, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...5)

            Button(action: sendMessage) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.title2)
            }
            .disabled(inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !isConnected)
            .glassEffect(.regular.interactive(), in: Circle())
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 22))
        .padding(.horizontal)
        .padding(.bottom, 8)
    }

    private func setupConnectionManager() {
        connectionManager.onMessageComplete = { content in
            let message = Message(role: .assistant, content: content, server: server)
            modelContext.insert(message)
            server.messages.append(message)
        }
    }

    private func sendMessage() {
        let content = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !content.isEmpty else { return }

        // Add user message
        let userMessage = Message(role: .user, content: content, server: server)
        modelContext.insert(userMessage)
        server.messages.append(userMessage)

        // Send to server
        if server.type == .claudeAPI {
            let history = server.messages.dropLast().map { msg in
                ClaudeAPIService.APIMessage(role: msg.role.rawValue, content: msg.content)
            }
            connectionManager.sendMessage(content, conversationHistory: Array(history))
        } else {
            connectionManager.sendMessage(content)
        }

        // Clear input
        inputText = ""

        // Update last connected
        server.lastConnected = Date()
    }
}

#Preview {
    NavigationStack {
        ChatView(server: Server(name: "Test", url: "wss://localhost:8080"))
    }
    .modelContainer(for: [Server.self, Message.self], inMemory: true)
}

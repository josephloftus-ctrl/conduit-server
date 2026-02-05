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
        VStack(spacing: 0) {
            // Connection status banner
            if let error = connectionManager.errorMessage {
                HStack {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.white)
                    Text(error)
                        .font(.subheadline)
                        .foregroundStyle(.white)
                    Spacer()
                    Button("Retry") {
                        connectionManager.retry()
                    }
                    .buttonStyle(.bordered)
                    .tint(.white)
                }
                .padding()
                .background(.red)
            } else if connectionManager.isReconnecting {
                HStack {
                    ProgressView()
                        .tint(.white)
                    Text("Reconnecting...")
                        .font(.subheadline)
                        .foregroundStyle(.white)
                }
                .padding(.vertical, 8)
                .frame(maxWidth: .infinity)
                .background(.orange)
            } else if connectionManager.connectionState == .connecting {
                HStack {
                    ProgressView()
                        .tint(.white)
                    Text("Connecting...")
                        .font(.subheadline)
                        .foregroundStyle(.white)
                }
                .padding(.vertical, 8)
                .frame(maxWidth: .infinity)
                .background(.blue)
            }

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

            Divider()

            // Input
            HStack(spacing: 12) {
                TextField("Message", text: $inputText, axis: .vertical)
                    .textFieldStyle(.plain)
                    .lineLimit(1...5)

                Button(action: sendMessage) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                }
                .disabled(inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !isConnected)
            }
            .padding()
            .background(.bar)
        }
        .navigationTitle(server.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                HStack(spacing: 16) {
                    Circle()
                        .fill(isConnected ? .green : .gray)
                        .frame(width: 8, height: 8)

                    if server.yoloMode {
                        Image(systemName: "bolt.fill")
                            .foregroundStyle(.yellow)
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
        connectionManager.sendMessage(content)

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

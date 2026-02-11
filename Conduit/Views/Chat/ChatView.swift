import SwiftUI
import SwiftData

struct ChatView: View {
    @Environment(\.modelContext) private var modelContext
    @Environment(\.scenePhase) private var scenePhase

    let server: Server

    @State private var connectionManager = ConnectionManager()
    @State private var inputText = ""
    @State private var showingSettings = false
    @State private var showingConversations = false
    @State private var currentConversation: Conversation?

    private var isConnected: Bool {
        connectionManager.connectionState == .ready
    }

    private var sortedMessages: [Message] {
        currentConversation?.messages.sorted { $0.timestamp < $1.timestamp } ?? []
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
                            ForEach(sortedMessages) { message in
                                MessageBubble(
                                    message: message,
                                    onRegenerate: message.role == .assistant ? {
                                        regenerateResponse(from: message)
                                    } : nil
                                )
                                .id(message.id)
                            }

                            // Streaming content (with tool calls)
                            if connectionManager.isStreaming && (!connectionManager.streamingContent.isEmpty || !connectionManager.activeToolCalls.isEmpty) {
                                MessageBubble(
                                    content: connectionManager.streamingContent,
                                    role: .assistant,
                                    isStreaming: true,
                                    toolCalls: connectionManager.activeToolCalls
                                )
                                .id("streaming")
                            }
                        }
                        .padding()
                    }
                    .onChange(of: sortedMessages.count) {
                        if let lastMessage = sortedMessages.last {
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
        .navigationTitle(currentConversation?.title ?? server.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackgroundVisibility(.visible, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                HStack(spacing: 12) {
                    // Conversation list button
                    Button(action: { showingConversations = true }) {
                        Image(systemName: "bubble.left.and.bubble.right")
                    }

                    // Status indicator
                    HStack(spacing: 5) {
                        Circle()
                            .fill(isConnected ? Color.conduitSuccess : Color.conduitInactive)
                            .frame(width: 7, height: 7)

                        if server.yoloMode {
                            Image(systemName: "bolt.fill")
                                .font(.caption2)
                                .foregroundStyle(Color.conduitWarning)
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
        .sheet(isPresented: $showingConversations) {
            ConversationListSheet(
                server: server,
                currentConversation: $currentConversation
            )
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
            setupConversation()
            if connectionManager.connectionState == .disconnected {
                connectionManager.connect(to: server)
            }
        }
        .onDisappear {
            connectionManager.disconnect()
        }
        .onChange(of: scenePhase) { oldPhase, newPhase in
            if newPhase == .active && oldPhase == .background {
                if connectionManager.connectionState != .ready {
                    connectionManager.retry()
                }
            }
        }
    }

    @ViewBuilder
    private var statusBanner: some View {
        if !connectionManager.networkMonitor.isConnected {
            HStack(spacing: 8) {
                Image(systemName: "wifi.slash")
                Text("No Network")
                    .font(.system(.subheadline, design: .rounded).weight(.medium))
            }
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity)
            .glassEffect(.regular.tint(Color.conduitError), in: RoundedRectangle(cornerRadius: 12))
            .padding(.horizontal)
            .padding(.top, 4)
        } else if let error = connectionManager.errorMessage {
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
            .glassEffect(.regular.tint(Color.conduitError), in: RoundedRectangle(cornerRadius: 12))
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
            .glassEffect(.regular.tint(Color.conduitWarning), in: RoundedRectangle(cornerRadius: 12))
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
            .glassEffect(.regular.tint(Color.conduitAccent), in: RoundedRectangle(cornerRadius: 12))
            .padding(.horizontal)
            .padding(.top, 4)
        }
    }

    private var inputBar: some View {
        HStack(spacing: 12) {
            TextField("Message", text: $inputText, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...5)

            if connectionManager.isStreaming {
                Button(action: { connectionManager.stopGeneration() }) {
                    Image(systemName: "stop.circle.fill")
                        .font(.title2)
                        .foregroundStyle(Color.conduitError)
                }
                .glassEffect(.regular.interactive(), in: Circle())
            } else {
                Button(action: sendMessage) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                }
                .disabled(inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !isConnected)
                .glassEffect(.regular.interactive(), in: Circle())
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 22))
        .padding(.horizontal)
        .padding(.bottom, 8)
    }

    private func setupConversation() {
        if let existing = server.activeConversation {
            currentConversation = existing
        } else {
            let conversation = Conversation(server: server)
            modelContext.insert(conversation)
            currentConversation = conversation
        }
    }

    private func setupConnectionManager() {
        connectionManager.onMessageComplete = { [self] content, toolCalls in
            guard let conversation = currentConversation else { return }
            let message = Message(role: .assistant, content: content, conversation: conversation)
            modelContext.insert(message)
            conversation.updatedAt = Date()

            // Auto-title: after first assistant response, set title from first user message
            if conversation.title == "New Chat" {
                if let firstUserMessage = conversation.messages
                    .sorted(by: { $0.timestamp < $1.timestamp })
                    .first(where: { $0.role == .user }) {
                    let title = String(firstUserMessage.content.prefix(40))
                    conversation.title = title.count < firstUserMessage.content.count ? title + "..." : title
                }
            }
        }
    }

    private func sendMessage() {
        let content = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !content.isEmpty else { return }
        guard let conversation = currentConversation else { return }

        // Add user message
        let userMessage = Message(role: .user, content: content, conversation: conversation)
        modelContext.insert(userMessage)
        conversation.updatedAt = Date()

        // Send to server
        if server.type == .claudeAPI {
            let history = sortedMessages.map { msg in
                ClaudeAPIService.APIMessage(role: msg.role.rawValue, content: msg.content)
            }
            connectionManager.sendMessage(content, conversationHistory: history, systemPrompt: server.systemPrompt)
        } else {
            connectionManager.sendMessage(content)
        }

        // Clear input
        inputText = ""

        // Update last connected
        server.lastConnected = Date()
    }

    private func regenerateResponse(from message: Message) {
        guard let conversation = currentConversation else { return }
        let messages = sortedMessages

        // Find the message index
        guard let messageIndex = messages.firstIndex(where: { $0.id == message.id }) else { return }

        // Find the preceding user message
        let precedingMessages = messages[..<messageIndex]
        guard let userMessage = precedingMessages.last(where: { $0.role == .user }) else { return }

        // Delete the assistant message
        modelContext.delete(message)

        // Resend
        if server.type == .claudeAPI {
            // Build history up to (but not including) the deleted assistant message
            let historyMessages = messages[..<messageIndex].map { msg in
                ClaudeAPIService.APIMessage(role: msg.role.rawValue, content: msg.content)
            }
            connectionManager.sendMessage(
                userMessage.content,
                conversationHistory: Array(historyMessages.dropLast()),
                systemPrompt: server.systemPrompt
            )
        } else {
            connectionManager.sendMessage(userMessage.content)
        }

        conversation.updatedAt = Date()
        server.lastConnected = Date()
    }
}

#Preview {
    NavigationStack {
        ChatView(server: Server(name: "Test", url: "wss://localhost:8080"))
    }
    .modelContainer(for: [Server.self, Conversation.self, Message.self], inMemory: true)
}

import SwiftUI
import SwiftData

struct ConversationListSheet: View {
    let server: Server
    @Binding var currentConversation: Conversation?
    @Environment(\.modelContext) private var modelContext
    @Environment(\.dismiss) private var dismiss

    private var sortedConversations: [Conversation] {
        server.conversations.sorted { $0.updatedAt > $1.updatedAt }
    }

    var body: some View {
        NavigationStack {
            List {
                Button {
                    let conversation = Conversation(server: server)
                    modelContext.insert(conversation)
                    currentConversation = conversation
                    dismiss()
                } label: {
                    Label("New Chat", systemImage: "plus.message")
                        .foregroundStyle(Color.conduitAccent)
                }

                ForEach(sortedConversations) { conversation in
                    Button {
                        currentConversation = conversation
                        dismiss()
                    } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(conversation.title)
                                .font(.system(.headline, design: .rounded))
                                .foregroundStyle(.primary)

                            HStack {
                                if let lastMessage = conversation.messages
                                    .sorted(by: { $0.timestamp > $1.timestamp }).first {
                                    Text(lastMessage.content)
                                        .font(.subheadline)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(1)
                                }

                                Spacer()

                                Text(conversation.updatedAt, style: .relative)
                                    .font(.caption2)
                                    .foregroundStyle(.tertiary)
                            }
                        }
                        .padding(.vertical, 2)
                    }
                    .listRowBackground(
                        conversation.id == currentConversation?.id
                            ? Color.conduitAccent.opacity(0.15)
                            : Color.clear
                    )
                }
                .onDelete { indexSet in
                    for index in indexSet {
                        let conversation = sortedConversations[index]
                        if conversation.id == currentConversation?.id {
                            currentConversation = nil
                        }
                        modelContext.delete(conversation)
                    }
                }
            }
            .navigationTitle("Conversations")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}

#Preview {
    ConversationListSheet(
        server: Server(name: "Test", url: "wss://localhost:8080"),
        currentConversation: .constant(nil)
    )
    .modelContainer(for: [Server.self, Conversation.self, Message.self], inMemory: true)
}

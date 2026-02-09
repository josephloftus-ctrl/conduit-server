import SwiftUI
import SwiftData

struct ContentView: View {
    var body: some View {
        ServerListView()
    }
}

#Preview {
    ContentView()
        .modelContainer(for: [Server.self, Conversation.self, Message.self], inMemory: true)
}

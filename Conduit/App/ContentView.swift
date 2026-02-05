import SwiftUI

struct ContentView: View {
    var body: some View {
        ServerListView()
    }
}

#Preview {
    ContentView()
        .modelContainer(for: [Server.self, Message.self], inMemory: true)
}

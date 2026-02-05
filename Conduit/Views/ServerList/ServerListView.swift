import SwiftUI
import SwiftData

struct ServerListView: View {
    @Environment(\.modelContext) private var modelContext
    @Query(sort: \Server.lastConnected, order: .reverse) private var servers: [Server]

    @State private var showingAddServer = false
    @State private var selectedServer: Server?

    var body: some View {
        NavigationStack {
            List {
                ForEach(servers) { server in
                    NavigationLink(value: server) {
                        ServerRowView(server: server)
                    }
                }
                .onDelete(perform: deleteServers)
            }
            .navigationTitle("Conduit")
            .navigationDestination(for: Server.self) { server in
                ChatView(server: server)
            }
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button(action: { showingAddServer = true }) {
                        Image(systemName: "plus")
                    }
                }
            }
            .sheet(isPresented: $showingAddServer) {
                AddServerView()
            }
            .overlay {
                if servers.isEmpty {
                    ContentUnavailableView(
                        "No Servers",
                        systemImage: "server.rack",
                        description: Text("Add a server to get started")
                    )
                }
            }
        }
    }

    private func deleteServers(at offsets: IndexSet) {
        for index in offsets {
            modelContext.delete(servers[index])
        }
    }
}

#Preview {
    ServerListView()
        .modelContainer(for: Server.self, inMemory: true)
}

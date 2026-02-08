import SwiftUI
import SwiftData

struct ServerListView: View {
    @Environment(\.modelContext) private var modelContext
    @Query(sort: \Server.lastConnected, order: .reverse) private var servers: [Server]

    @State private var showingAddServer = false
    @State private var selectedServer: Server?

    var body: some View {
        NavigationStack {
            GlassEffectContainer {
                ScrollView {
                    LazyVStack(spacing: 12) {
                        if servers.isEmpty {
                            emptyState
                        } else {
                            ForEach(servers) { server in
                                NavigationLink(value: server) {
                                    ServerRowView(server: server)
                                }
                                .buttonStyle(.plain)
                                .padding(.horizontal, 16)
                                .padding(.vertical, 10)
                                .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 16))
                                .contextMenu {
                                    Button("Delete", role: .destructive) {
                                        modelContext.delete(server)
                                    }
                                }
                            }
                        }
                    }
                    .padding(.horizontal)
                    .padding(.top, 8)
                }
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
                    .buttonStyle(.glass)
                }
            }
            .sheet(isPresented: $showingAddServer) {
                AddServerView()
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 20) {
            Spacer()
                .frame(height: 60)

            Image(systemName: "server.rack")
                .font(.system(size: 48))
                .foregroundStyle(.secondary)
                .frame(width: 80, height: 80)
                .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 20))

            VStack(spacing: 8) {
                Text("No Servers")
                    .font(.title2.bold())

                Text("Add a server to start chatting with AI")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }

            Button(action: { showingAddServer = true }) {
                Label("Add Server", systemImage: "plus")
            }
            .buttonStyle(.glassProminent)

            Spacer()
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 32)
    }
}

#Preview {
    ServerListView()
        .modelContainer(for: Server.self, inMemory: true)
}

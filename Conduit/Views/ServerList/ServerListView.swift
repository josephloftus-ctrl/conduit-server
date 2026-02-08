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
                        // Branded header
                        HStack(spacing: 8) {
                            Image(systemName: "bolt.horizontal.circle.fill")
                                .font(.system(size: 28))
                                .foregroundStyle(Color.conduitAccent)
                            Text("Conduit")
                                .font(.system(size: 28, weight: .bold, design: .rounded))
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 4)
                        .padding(.bottom, 4)

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
            .conduitBackground()
            .navigationTitle("Conduit")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Color.clear.frame(width: 0, height: 0)
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button(action: { showingAddServer = true }) {
                        Image(systemName: "plus")
                    }
                    .buttonStyle(.glass)
                }
            }
            .navigationDestination(for: Server.self) { server in
                ChatView(server: server)
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
                    .font(.system(size: 22, weight: .bold, design: .rounded))

                Text("Add a server to start chatting with AI")
                    .font(.system(.subheadline, design: .rounded))
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

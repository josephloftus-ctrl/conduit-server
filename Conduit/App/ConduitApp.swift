import SwiftUI
import SwiftData

@main
struct ConduitApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .tint(Color.conduitAccent)
        }
        .modelContainer(for: [Server.self, Message.self])
    }
}

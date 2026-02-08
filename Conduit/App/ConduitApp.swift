import SwiftUI
import SwiftData

@main
struct ConduitApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .tint(.blue)
        }
        .modelContainer(for: [Server.self, Message.self])
    }
}

import SwiftUI

struct PermissionModal: View {
    let permission: ConnectionManager.PendingPermission
    let onResponse: (Bool) -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            GlassEffectContainer {
                VStack(spacing: 24) {
                    // Icon with glass backing
                    Image(systemName: iconName)
                        .font(.system(size: 32))
                        .foregroundStyle(Color.conduitAccent)
                        .frame(width: 64, height: 64)
                        .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 16))

                    // Title
                    Text(title)
                        .font(.system(size: 22, weight: .bold, design: .rounded))

                    // Detail
                    if let path = permission.detail.path {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("File:")
                                .font(.system(.caption, design: .rounded).weight(.medium))
                                .foregroundStyle(.secondary)
                            Text(path)
                                .font(.system(.body, design: .monospaced))
                                .padding(12)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 10))
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }

                    if let diff = permission.detail.diff {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Changes:")
                                .font(.system(.caption, design: .rounded).weight(.medium))
                                .foregroundStyle(.secondary)

                            ScrollView {
                                Text(diff)
                                    .font(.system(.caption, design: .monospaced))
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .frame(maxHeight: 200)
                            .padding(12)
                            .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 10))
                        }
                    }

                    if let command = permission.detail.command {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Command:")
                                .font(.system(.caption, design: .rounded).weight(.medium))
                                .foregroundStyle(.secondary)
                            Text(command)
                                .font(.system(.body, design: .monospaced))
                                .padding(12)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 10))
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }

                    Spacer()

                    // Buttons
                    HStack(spacing: 16) {
                        Button(action: {
                            onResponse(false)
                            dismiss()
                        }) {
                            Text("Reject")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.glass)
                        .controlSize(.large)

                        Button(action: {
                            onResponse(true)
                            dismiss()
                        }) {
                            Text("Approve")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.glassProminent)
                        .controlSize(.large)
                    }
                }
                .padding(24)
            }
            .navigationBarTitleDisplayMode(.inline)
            .interactiveDismissDisabled()
        }
        .presentationDetents([.medium, .large])
    }

    private var iconName: String {
        switch permission.action {
        case "edit_file":
            return "doc.badge.ellipsis"
        case "create_file":
            return "doc.badge.plus"
        case "delete_file":
            return "doc.badge.minus"
        case "run_command":
            return "terminal"
        default:
            return "questionmark.circle"
        }
    }

    private var title: String {
        switch permission.action {
        case "edit_file":
            return "Edit file?"
        case "create_file":
            return "Create file?"
        case "delete_file":
            return "Delete file?"
        case "run_command":
            return "Run command?"
        default:
            return "Permission requested"
        }
    }
}

#Preview {
    PermissionModal(
        permission: ConnectionManager.PendingPermission(
            id: "123",
            action: "edit_file",
            detail: PermissionDetail(
                path: "/home/user/project/src/main.rs",
                diff: "- old line\n+ new line",
                command: nil
            )
        ),
        onResponse: { _ in }
    )
}

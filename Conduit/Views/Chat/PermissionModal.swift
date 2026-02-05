import SwiftUI

struct PermissionModal: View {
    let permission: ConnectionManager.PendingPermission
    let onResponse: (Bool) -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                // Icon
                Image(systemName: iconName)
                    .font(.system(size: 48))
                    .foregroundStyle(.orange)

                // Title
                Text(title)
                    .font(.title2.bold())

                // Detail
                if let path = permission.detail.path {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("File:")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text(path)
                            .font(.system(.body, design: .monospaced))
                            .padding(12)
                            .background(Color(.systemGray6))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }

                if let diff = permission.detail.diff {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Changes:")
                            .font(.caption)
                            .foregroundStyle(.secondary)

                        ScrollView {
                            Text(diff)
                                .font(.system(.caption, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .frame(maxHeight: 200)
                        .padding(12)
                        .background(Color(.systemGray6))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                }

                if let command = permission.detail.command {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Command:")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text(command)
                            .font(.system(.body, design: .monospaced))
                            .padding(12)
                            .background(Color(.systemGray6))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
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
                            .padding()
                            .background(Color(.systemGray5))
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                    .buttonStyle(.plain)

                    Button(action: {
                        onResponse(true)
                        dismiss()
                    }) {
                        Text("Approve")
                            .frame(maxWidth: .infinity)
                            .padding()
                            .background(.blue)
                            .foregroundStyle(.white)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(24)
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

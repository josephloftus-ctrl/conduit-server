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

                    // Edit: show old_text -> new_text as diff
                    if let oldText = permission.detail.oldText {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Changes:")
                                .font(.system(.caption, design: .rounded).weight(.medium))
                                .foregroundStyle(.secondary)

                            ScrollView {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text("- " + oldText)
                                        .foregroundStyle(Color.conduitError)
                                    Text("+ " + (permission.detail.newText ?? ""))
                                        .foregroundStyle(Color.conduitSuccess)
                                }
                                .font(.system(.caption, design: .monospaced))
                                .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .frame(maxHeight: 200)
                            .padding(12)
                            .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 10))
                        }
                    }

                    // Write: show content preview
                    if permission.detail.oldText == nil, let content = permission.detail.content {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Content:")
                                .font(.system(.caption, design: .rounded).weight(.medium))
                                .foregroundStyle(.secondary)

                            ScrollView {
                                Text(String(content.prefix(500)))
                                    .font(.system(.caption, design: .monospaced))
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .frame(maxHeight: 200)
                            .padding(12)
                            .glassEffect(.regular, in: RoundedRectangle(cornerRadius: 10))
                        }
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
        let action = permission.action
        // New format: "write:write_file", "write:edit_file", "execute:run_command"
        if action.contains("write_file") || action.contains("create_file") {
            return "doc.badge.plus"
        } else if action.contains("edit_file") {
            return "doc.badge.ellipsis"
        } else if action.contains("delete_file") {
            return "doc.badge.minus"
        } else if action.contains("run_command") || action.contains("execute") {
            return "terminal"
        }
        return "questionmark.circle"
    }

    private var title: String {
        let action = permission.action
        if action.contains("write_file") {
            return "Write file?"
        } else if action.contains("edit_file") {
            return "Edit file?"
        } else if action.contains("delete_file") {
            return "Delete file?"
        } else if action.contains("run_command") || action.contains("execute") {
            return "Run command?"
        }
        return "Permission requested"
    }
}

#Preview {
    PermissionModal(
        permission: ConnectionManager.PendingPermission(
            id: "123",
            action: "write:edit_file",
            detail: PermissionDetail(
                path: "/home/user/project/src/main.rs",
                diff: nil,
                command: nil,
                content: nil,
                oldText: "old line",
                newText: "new line"
            )
        ),
        onResponse: { _ in }
    )
}

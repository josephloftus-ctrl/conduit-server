# Conduit

Native iOS chat app for any AI backend. Bring your own brain.

## What is this?

A beautiful, fast chat UI that connects to **any** AI backend over WebSocket. Point it at:
- Your own daemon
- Ollama
- OpenClaw
- vLLM
- Anything that speaks the protocol

## Features

- **Server profiles** - Multiple backends, easy switching
- **Streaming** - Real-time response streaming
- **Permission prompts** - Approve/reject file edits and commands
- **YOLO mode** - Auto-approve everything (per-server toggle)
- **Local history** - Cached conversations, instant load
- **Auto-reconnect** - Handles disconnects gracefully

## The Protocol

Dead simple WebSocket JSON. [Full spec here](docs/PROTOCOL.md).

### Client → Server
```json
{"type": "message", "content": "explain this function", "cwd": "/home/user/project"}
{"type": "permission_response", "id": "abc123", "granted": true}
{"type": "set_cwd", "cwd": "/home/user/other"}
```

### Server → Client
```json
{"type": "hello", "server": "my-daemon", "version": "1.0.0", "capabilities": ["streaming", "permissions"]}
{"type": "chunk", "content": "Here's what..."}
{"type": "done"}
{"type": "permission", "id": "abc123", "action": "edit_file", "detail": {"path": "...", "diff": "..."}}
{"type": "error", "message": "Something went wrong"}
```

Any backend can implement this in ~50 lines.

## Building

This project uses [XcodeGen](https://github.com/yonaskolb/XcodeGen) to generate the Xcode project.

### Local (if you have Xcode)
```bash
brew install xcodegen
xcodegen generate
open Conduit.xcodeproj
```

### CI/CD
GitHub Actions builds automatically on push. See `.github/workflows/build.yml`.

## Requirements

- iOS 17.0+
- Server that speaks the Conduit protocol

## License

[TBD - Source available, App Store binary paid]

## Contributing

PRs welcome. Keep it simple.

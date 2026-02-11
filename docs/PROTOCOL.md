# Conduit Protocol Specification

Version: 1.0.0

## Overview

Simple WebSocket JSON protocol for AI chat. Designed for easy implementation.

## Transport

- **Protocol:** WebSocket over TLS (`wss://`)
- **Auth:** Optional Bearer token in `Authorization` header

## Message Format

All messages are JSON objects with a `type` field.

---

## Client → Server Messages

### message

Send a chat message.

```json
{
  "type": "message",
  "content": "explain this function",
  "cwd": "/home/user/project"  // optional
}
```

### permission_response

Respond to a permission request.

```json
{
  "type": "permission_response",
  "id": "abc123",
  "granted": true
}
```

### set_cwd

Change working directory.

```json
{
  "type": "set_cwd",
  "cwd": "/home/user/other-project"
}
```

---

## Server → Client Messages

### hello

Sent immediately after connection.

```json
{
  "type": "hello",
  "server": "my-daemon",
  "version": "1.0.0",
  "capabilities": ["streaming", "permissions", "cwd"]
}
```

**Capabilities:**
- `streaming` - Server sends `chunk` messages
- `permissions` - Server may request permissions
- `cwd` - Server supports working directory

### chunk

Streaming text content.

```json
{
  "type": "chunk",
  "content": "Here's what that function"
}
```

### done

Stream complete.

```json
{
  "type": "done"
}
```

### permission

Request user approval for an action.

```json
{
  "type": "permission",
  "id": "abc123",
  "action": "edit_file",
  "detail": {
    "path": "/home/user/project/src/main.rs",
    "diff": "- old line\n+ new line"
  }
}
```

**Actions:**
- `edit_file` - Modify existing file (includes `path`, `diff`)
- `create_file` - Create new file (includes `path`, `content`)
- `delete_file` - Delete file (includes `path`)
- `run_command` - Execute shell command (includes `command`)

Client must respond with `permission_response` using the same `id`.

### error

Server error.

```json
{
  "type": "error",
  "message": "Model unavailable"
}
```

---

## Flow Example

```
Client                              Server
   |                                   |
   |-------- [connect] --------------->|
   |                                   |
   |<------- hello --------------------|
   |                                   |
   |-------- message ----------------->|
   |                                   |
   |<------- chunk --------------------|
   |<------- chunk --------------------|
   |<------- chunk --------------------|
   |<------- done ---------------------|
   |                                   |
   |-------- message ----------------->|
   |                                   |
   |<------- chunk --------------------|
   |<------- permission ---------------|
   |                                   |
   |-------- permission_response ----->|
   |                                   |
   |<------- chunk --------------------|
   |<------- done ---------------------|
```

---

## Extensions (v1.1)

The following message types extend the base protocol. Clients that don't recognize them should ignore unknown types gracefully.

### typing (S→C)

Server is processing, sent before the first `chunk`.

```json
{
  "type": "typing"
}
```

### meta (S→C)

Sent after `done` with model info and token usage.

```json
{
  "type": "meta",
  "model": "llama3.1",
  "input_tokens": 512,
  "output_tokens": 204
}
```

### push (S→C)

Server-initiated message (cron results, reminders). Not in response to a client message.

```json
{
  "type": "push",
  "title": "Morning Briefing",
  "content": "Here's your overview..."
}
```

### set_conversation (C→S)

Switch to a different conversation (web UI).

```json
{
  "type": "set_conversation",
  "conversation_id": "abc123def456"
}
```

---

## Reference Implementation

See `server/` for the Python backend implementation.

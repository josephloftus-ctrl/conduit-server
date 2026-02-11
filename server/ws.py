"""WebSocket connection manager — tracks connected clients, broadcasts."""

import asyncio
import json
import logging
import uuid
from fastapi import WebSocket

log = logging.getLogger("conduit.ws")


class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self):
        self.active: list[WebSocket] = []
        self._pending_permissions: dict[str, asyncio.Future] = {}

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        log.info("Client connected (%d total)", len(self.active))

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        log.info("Client disconnected (%d remaining)", len(self.active))

    async def send(self, ws: WebSocket, msg: dict):
        """Send a typed JSON message to one client."""
        await ws.send_json(msg)

    async def broadcast(self, msg: dict):
        """Send a message to all connected clients."""
        disconnected = []
        for ws in self.active:
            try:
                await ws.send_json(msg)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)

    async def send_hello(self, ws: WebSocket):
        await self.send(ws, {
            "type": "hello",
            "server": "conduit",
            "version": "1.0.0",
            "capabilities": ["streaming", "cwd", "push", "typing", "meta", "tools"],
        })

    async def send_chunk(self, ws: WebSocket, content: str):
        await self.send(ws, {"type": "chunk", "content": content})

    async def send_done(self, ws: WebSocket):
        await self.send(ws, {"type": "done"})

    async def send_typing(self, ws: WebSocket):
        await self.send(ws, {"type": "typing"})

    async def send_meta(self, ws: WebSocket, model: str, input_tokens: int, output_tokens: int):
        await self.send(ws, {
            "type": "meta",
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })

    async def send_error(self, ws: WebSocket, message: str):
        await self.send(ws, {"type": "error", "message": message})

    async def push(self, content: str, title: str = ""):
        """Broadcast a push notification to all clients."""
        await self.broadcast({
            "type": "push",
            "content": content,
            "title": title,
        })

    # --- Tool call messages ---

    async def send_tool_start(self, ws: WebSocket, tool_call_id: str, name: str, arguments: dict):
        """Notify client that a tool call is starting."""
        await self.send(ws, {
            "type": "tool_start",
            "tool_call_id": tool_call_id,
            "name": name,
            "arguments": arguments,
        })

    async def send_tool_done(self, ws: WebSocket, tool_call_id: str, name: str,
                             result: str = "", error: str = ""):
        """Notify client that a tool call completed."""
        msg = {
            "type": "tool_done",
            "tool_call_id": tool_call_id,
            "name": name,
        }
        if error:
            msg["error"] = error
        else:
            # Truncate result for WS display (full result goes to model)
            msg["result"] = result[:2000] if result else ""
        await self.send(ws, msg)

    # --- Permission request/response ---

    async def request_permission(self, ws: WebSocket, action: str, detail: dict) -> bool:
        """Request permission from the client. Returns True if granted."""
        perm_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_permissions[perm_id] = future

        await self.send(ws, {
            "type": "permission",
            "id": perm_id,
            "action": action,
            "detail": detail,
        })

        try:
            granted = await asyncio.wait_for(future, timeout=60.0)
            return granted
        except asyncio.TimeoutError:
            log.warning("Permission request %s timed out", perm_id)
            return False
        finally:
            self._pending_permissions.pop(perm_id, None)

    def resolve_permission(self, perm_id: str, granted: bool):
        """Resolve a pending permission request."""
        future = self._pending_permissions.get(perm_id)
        if future and not future.done():
            future.set_result(granted)

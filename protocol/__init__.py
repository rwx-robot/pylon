"""pylon.protocol - Request and Response dataclasses."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Request:
    """Incoming HTTP request."""

    method: str
    path: str
    headers: Dict[str, str] = field(default_factory=dict)
    query_params: Dict[str, str] = field(default_factory=dict)
    path_params: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    #: Client IP address, set by Application from ASGI scope.
    client_ip: Optional[str] = None

    #: Pre-parsed JSON body, populated by BindingMiddleware.
    #: None if body is empty or not valid JSON.
    json_data: Any = None

    @property
    def json(self) -> Any:
        """Parse body as JSON."""
        if not self.body:
            return None
        return json.loads(self.body.decode("utf-8"))

    def __repr__(self) -> str:
        return f"Request(method={self.method!r}, path={self.path!r})"


@dataclass
class Response:
    """Outgoing HTTP response."""

    status_code: int = 200
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def json(self, data: Any) -> Response:
        """Set JSON response body."""
        self.headers["Content-Type"] = "application/json"
        self.body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        return self

    def text(self, data: str) -> Response:
        """Set plain text response body."""
        self.headers["Content-Type"] = "text/plain; charset=utf-8"
        self.body = data.encode("utf-8")
        return self

    def html(self, data: str) -> Response:
        """Set HTML response body."""
        self.headers["Content-Type"] = "text/html; charset=utf-8"
        self.body = data.encode("utf-8")
        return self

    def set_header(self, key: str, value: str) -> Response:
        """Set a response header."""
        self.headers[key] = value
        return self

    def __repr__(self) -> str:
        return f"Response(status_code={self.status_code}, body_len={len(self.body)})"


class WebSocket:
    """
    WebSocket connection wrapper for ASGI websocket scope.

    Provides a high-level interface for WebSocket connections:
    - `accept(subprotocol=None)` — accept the connection
    - `send_text(data)` — send a text message
    - `send_binary(data)` — send a binary message
    - `close(code=1000)` — close the connection
    - `receive()` — receive a message (awaited)

    Example:
        @app.ws("/ws")
        async def echo(ws, req):
            await ws.accept()
            async for msg in ws:
                if msg.type == "text":
                    await ws.send_text(msg.data)
                elif msg.type == "binary":
                    await ws.send_binary(msg.data)
                elif msg.type == "close":
                    break
    """

    def __init__(
        self,
        scope: Dict[str, Any],
        receive: Callable,
        send: Callable,
    ):
        self._scope = scope
        self._receive = receive
        self._send = send
        self._accepted = False
        self._closed = False

    @property
    def path(self) -> str:
        """WebSocket URL path."""
        return self._scope.get("path", "/")

    @property
    def headers(self) -> Dict[str, str]:
        """WebSocket request headers."""
        headers = self._scope.get("headers", [])
        return {k.decode(): v.decode() for k, v in headers}

    @property
    def query_params(self) -> Dict[str, str]:
        """WebSocket query string parameters."""
        raw = self._scope.get("query_string", b"").decode()
        params: Dict[str, str] = {}
        for pair in raw.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k] = v
        return params

    @property
    def client_ip(self) -> Optional[str]:
        """Client IP address from ASGI scope."""
        client = self._scope.get("client")
        if client:
            return client[0]
        return None

    async def accept(self, subprotocol: Optional[str] = None) -> None:
        """Accept the WebSocket connection."""
        if self._accepted:
            return
        msg: Dict[str, Any] = {"type": "websocket.accept"}
        if subprotocol:
            msg["subprotocol"] = subprotocol
        await self._send(msg)
        self._accepted = True

    async def send_text(self, data: str) -> None:
        """Send a text message."""
        if self._closed:
            raise RuntimeError("WebSocket is closed")
        await self._send({
            "type": "websocket.send",
            "text": data,
        })

    async def send_binary(self, data: bytes) -> None:
        """Send a binary message."""
        if self._closed:
            raise RuntimeError("WebSocket is closed")
        await self._send({
            "type": "websocket.send",
            "bytes": data,
        })

    async def close(self, code: int = 1000) -> None:
        """Close the WebSocket connection."""
        if self._closed:
            return
        await self._send({"type": "websocket.close", "code": code})
        self._closed = True

    async def receive(self) -> Dict[str, Any]:
        """
        Receive the next WebSocket message.

        Returns a message dict with keys:
          - type: "text" | "binary" | "close" | "error"
          - data: str | bytes (for text/binary)
          - code: int (for close)
        """
        msg = await self._receive()
        return msg

    async def __aiter__(self) -> "WebSocket":
        return self

    async def __anext__(self) -> Dict[str, Any]:
        msg = await self.receive()
        if msg["type"] == "websocket.disconnect":
            raise StopAsyncIteration()
        return msg

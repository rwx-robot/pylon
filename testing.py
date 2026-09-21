"""
pylon.testing - TestClient for unit/integration testing pylon applications.

Provides an ASGI-based test client that calls the Application directly
without requiring a running server.

Example:
    from pylon import Application, TestClient

    app = Application()
    app.use(SomeMiddleware())

    @app.get("/users/:id")
    async def get_user(req, res):
        res.json({"user_id": req.path_params["id"]})

    client = TestClient(app)

    # Synchronous usage (run handles event loop)
    resp = client.get("/users/42")
    assert resp.status_code == 200
    assert resp.json() == {"user_id": "42"}

    # Can also use the async interface directly
    resp = await client.request("POST", "/data", json={"name": "test"})
    assert resp.status_code == 200
"""

from __future__ import annotations

import asyncio
import json as _json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass
class PylonTestResponse:
    """
    Represents a test HTTP response.

    Mimics the interface of requests.Response / httpx.Response
    for familiarity.
    """

    status_code: int
    headers: Dict[str, str]
    body: bytes
    _json: Optional[Dict[str, Any]] = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        """Return True if status_code is less than 400."""
        return self.status_code < 400

    def json(self) -> Dict[str, Any]:
        """Parse response body as JSON."""
        if self._json is not None:
            return self._json
        self._json = _json.loads(self.body.decode("utf-8"))
        return self._json

    def text(self) -> str:
        """Return response body as decoded text."""
        return self.body.decode("utf-8")

    def raise_for_status(self) -> None:
        """Raise an exception if status_code >= 400."""
        if not self.ok:
            raise PylonTestResponseError(
                f"HTTP {self.status_code}: {self.text()}",
                status_code=self.status_code,
                response=self,
            )


class PylonTestResponseError(Exception):
    """Raised when a test response has status_code >= 400."""

    def __init__(self, message: str, status_code: int, response: PylonTestResponse):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class PylonTestClient:
    """
    ASGI-based test client for pylon applications.

    Makes requests directly to the Application by invoking it as an ASGI
    callable, collecting the response via a mock ``send`` callable.

    Supports: GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD.

    Example:
        client = PylonTestClient(app)
        resp = client.post("/items", json={"name": "widget"})
        assert resp.status_code == 201
    """

    def __init__(
        self,
        app: Any,
        base_url: str = "http://testserver",
    ):
        """
        Args:
            app: A pylon Application instance.
            base_url: Base URL prepended to paths (default "http://testserver").
        """
        self.app = app
        self.base_url = base_url.rstrip("/")

    # -----------------------------------------------------------
    # Public synchronous API (run manages the event loop for you)
    # -----------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        json: Optional[Any] = None,
        data: Optional[Union[str, bytes]] = None,
        timeout: float = 30.0,
    ) -> PylonTestResponse:
        """
        Make a synchronous HTTP request.

        Args:
            method: HTTP method (GET, POST, PUT, etc.).
            path: URL path (prepended with base_url).
            headers: Optional request headers dict.
            params: Optional query string params dict.
            json: Optional JSON-serializable body (sets Content-Type: application/json).
            data: Optional raw string/bytes body.
            timeout: Request timeout in seconds (unused, for API compat).

        Returns:
            PylonTestResponse instance.
        """
        return asyncio.new_event_loop().run_until_complete(
            self._request(method, path, headers=headers, params=params,
                          json=json, data=data)
        )

    def get(self, path: str, **kwargs: Any) -> PylonTestResponse:
        """Make a GET request."""
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> PylonTestResponse:
        """Make a POST request."""
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> PylonTestResponse:
        """Make a PUT request."""
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> PylonTestResponse:
        """Make a PATCH request."""
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> PylonTestResponse:
        """Make a DELETE request."""
        return self.request("DELETE", path, **kwargs)

    def options(self, path: str, **kwargs: Any) -> PylonTestResponse:
        """Make an OPTIONS request."""
        return self.request("OPTIONS", path, **kwargs)

    def head(self, path: str, **kwargs: Any) -> PylonTestResponse:
        """Make a HEAD request."""
        return self.request("HEAD", path, **kwargs)

    # -----------------------------------------------------------
    # WebSocket test interface
    # -----------------------------------------------------------

    def websocket(
        self,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        subprotocols: Optional[List[str]] = None,
    ) -> PylonTestWebSocket:
        """
        Create a WebSocket test session.

        Example:
            ws = client.websocket("/ws")
            await ws.accept()
            await ws.send_text("hello")
            msg = await ws.receive()
            await ws.close()
        """
        return PylonTestWebSocket(self.app, path, headers=headers,
                                  params=params, subprotocols=subprotocols)

    # -----------------------------------------------------------
    # Async API (for use inside async test functions)
    # -----------------------------------------------------------

    async def async_request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        json: Optional[Any] = None,
        data: Optional[Union[str, bytes]] = None,
    ) -> PylonTestResponse:
        """
        Async version of ``request``. Use inside async test functions.

        Example:
            async def test_async():
                resp = await client.async_request("GET", "/users/1")
                assert resp.json()["id"] == 1
        """
        return await self._request(method, path, headers=headers,
                                    params=params, json=json, data=data)

    # -----------------------------------------------------------
    # Internal
    # -----------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        json: Optional[Any] = None,
        data: Optional[Union[str, bytes]] = None,
    ) -> PylonTestResponse:
        """Async request implementation using ASGI."""
        # Build full URL
        if path.startswith("http://") or path.startswith("https://"):
            full_path = path
        else:
            full_path = f"{self.base_url}{path}"

        # Parse out path and query string
        if "?" in full_path:
            url_path, query_string = full_path.split("?", 1)
            query_string = query_string.encode()
        else:
            url_path = full_path
            query_string = b""

        # Merge params into query string
        if params:
            param_str = "&".join(f"{k}={v}" for k, v in params.items())
            if query_string:
                query_string = query_string + b"&" + param_str.encode()
            else:
                query_string = param_str.encode()

        # Build body
        if json is not None:
            body_bytes = _json.dumps(json, separators=(",", ":")).encode()
            _headers = dict(headers) if headers else {}
            _headers.setdefault("Content-Type", "application/json")
        elif data is not None:
            body_bytes = data.encode() if isinstance(data, str) else data
            _headers = dict(headers) if headers else {}
        else:
            body_bytes = b""
            _headers = dict(headers) if headers else {}

        # Prepend base_url path prefix to get clean path
        if path.startswith("http://") or path.startswith("https://"):
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(full_path)
            asgi_path = urlunparse(("", "", parsed.path, parsed.params,
                                     parsed.query, ""))
        elif path.startswith("/"):
            asgi_path = path
        else:
            asgi_path = "/" + path

        # Parse query string from path if present
        if "?" in asgi_path:
            asgi_path, qs = asgi_path.split("?", 1)
            if not query_string:
                query_string = qs.encode()

        # ASGI scope
        scope: Dict[str, Any] = {
            "type": "http",
            "method": method.upper(),
            "path": asgi_path,
            "query_string": query_string,
            "headers": [(k.encode(), v.encode()) for k, v in _headers.items()],
            "root_path": "",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 8000),
        }

        # Collect response via mock send
        response_status_code = 0
        response_headers: Dict[str, str] = {}
        response_body_chunks: List[bytes] = []

        async def mock_send(message: Dict[str, Any]) -> None:
            nonlocal response_status_code, response_headers
            if message["type"] == "http.response.start":
                response_status_code = message["status"]
                raw_headers = message.get("headers", [])
                response_headers = {
                    k.decode(): v.decode() for k, v in raw_headers
                }
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    response_body_chunks.append(body)

        # Receive mock — return body in one chunk
        body_sent = False

        async def mock_receive() -> Dict[str, Any]:
            nonlocal body_sent
            if not body_sent and body_bytes:
                body_sent = True
                return {
                    "type": "http.request",
                    "body": body_bytes,
                    "more_body": False,
                }
            return {"type": "http.request", "body": b"", "more_body": False}

        # Call the ASGI app
        await self.app(scope, mock_receive, mock_send)

        return PylonTestResponse(
            status_code=response_status_code,
            headers=response_headers,
            body=b"".join(response_body_chunks),
        )


class PylonTestWebSocket:
    """
    WebSocket test session for testing WebSocket handlers.

    Example:
        ws = client.websocket("/ws")
        await ws.accept()
        await ws.send_text("hello")
        msg = await ws.receive()
        assert msg["type"] == "websocket.accept"
        await ws.close()
    """

    def __init__(
        self,
        app: Any,
        path: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        subprotocols: Optional[List[str]] = None,
    ):
        self.app = app
        self.path = path
        self.headers = headers or {}
        self.params = params or {}
        self.subprotocols = subprotocols or []

        self._scope: Dict[str, Any] = {}
        self._connect_queue: asyncio.Queue = asyncio.Queue()  # dedicated to websocket.connect for accept()
        self._send_queue: asyncio.Queue = asyncio.Queue()    # handler → test messages (accept/send/close)
        self._receive_queue: asyncio.Queue = asyncio.Queue()  # test → handler messages (receive)
        self._closed = False
        self._accepted = False
        self._handler_task: Optional[asyncio.Task] = None

    async def accept(self, subprotocol: Optional[str] = None) -> None:
        """Accept the WebSocket connection.

        Consumes the websocket.connect message from _connect_queue (confirming
        the handler has been started), then marks the session as accepted.
        The handler's own ws.accept() call is what puts the websocket.accept
        message into _send_queue — this method does NOT.
        """
        if self._accepted:
            return
        msg = await self._connect_queue.get()
        if msg.get("type") != "websocket.connect":
            raise PylonTestWSError(f"Expected websocket.connect, got {msg.get('type')}")
        self._accepted = True

    async def send_text(self, text: str) -> None:
        """Send a text message to the server (via ASGI receive)."""
        await self._receive_queue.put({"type": "websocket.receive", "text": text})

    async def send_binary(self, data: bytes) -> None:
        """Send a binary message to the server (via ASGI receive)."""
        await self._receive_queue.put({"type": "websocket.receive", "bytes": data})

    async def receive(self) -> Dict[str, Any]:
        """Receive the next message from the server (via ASGI send)."""
        return await self._send_queue.get()

    async def close(self, code: int = 1000) -> None:
        """Close the WebSocket connection."""
        if not self._closed:
            self._closed = True
            await self._send_queue.put({"type": "websocket.close", "code": code})

    async def __aenter__(self) -> "PylonTestWebSocket":
        """Enter the async context manager."""
        await self._connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        """Exit the async context manager."""
        if not self._closed:
            await self.close()
        if self._handler_task:
            try:
                await asyncio.wait_for(self._handler_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._handler_task.cancel()

    async def _connect(self) -> None:
        """Establish the WebSocket connection by calling the ASGI app."""
        query_string = "&".join(f"{k}={v}" for k, v in self.params.items())
        scope = {
            "type": "websocket",
            "path": self.path,
            "query_string": query_string.encode(),
            "headers": [(k.encode(), v.encode()) for k, v in self.headers.items()],
            "root_path": "",
            "scheme": "ws",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 8000),
            "subprotocols": self.subprotocols,
        }
        self._scope = scope

        # Pre-populate the connect message in the dedicated connect queue
        await self._connect_queue.put({"type": "websocket.connect"})

        # Start the ASGI handler as a concurrent task so accept()/receive()
        # can be called from the test without blocking on the handler
        self._handler_task = asyncio.create_task(
            self.app(scope, self._ws_receive, self._ws_send)
        )

    async def _ws_receive(self) -> Dict[str, Any]:
        """ASGI receive callable — returns messages queued by the test client."""
        return await self._receive_queue.get()

    async def _ws_send(self, message: Dict[str, Any]) -> None:
        """ASGI send callable — queues messages from the server."""
        await self._send_queue.put(message)


class PylonTestWSError(Exception):
    """Error raised during WebSocket test session."""
    pass


# Backwards-compatible aliases
TestClient = PylonTestClient
TestResponse = PylonTestResponse
TestWebSocket = PylonTestWebSocket
TestResponseError = PylonTestResponseError
TestWSError = PylonTestWSError

"""pylon.server - Server abstraction and async implementations."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

from pylon.protocol import Request, Response
from pylon.router import Router


class Server(ABC):
    """Abstract server interface."""

    @abstractmethod
    async def serve(self) -> None:
        """Start the server and block until shutdown."""
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Gracefully shutdown the server."""
        ...


class UvicornServer(Server):
    """Async server backed by uvicorn."""

    def __init__(
        self,
        app: Any,  # pylon.Application
        host: str = "127.0.0.1",
        port: int = 8000,
        **uvicorn_kwargs: Any,
    ):
        self.app = app
        self.host = host
        self.port = port
        self.uvicorn_kwargs = uvicorn_kwargs
        self._server: Optional[Any] = None
        self._shutdown_event = asyncio.Event()

    async def serve(self) -> None:
        import uvicorn

        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            **self.uvicorn_kwargs,
        )
        self._server = uvicorn.Server(config)
        await self._server.serve()

    async def shutdown(self) -> None:
        if self._server:
            self._server.should_exit = True
        self._shutdown_event.set()


class AsyncioServer(Server):
    """Minimal async server using asyncio and standard library http."""

    def __init__(
        self,
        app: Any,  # pylon.Application
        host: str = "127.0.0.1",
        port: int = 8000,
    ):
        self.app = app
        self.host = host
        self.port = port
        self._shutdown_event = asyncio.Event()
        self._server_socket: Optional[Any] = None

    async def serve(self) -> None:
        import asyncio
        from http.server import HTTPServer
        import threading

        loop = asyncio.get_event_loop()

        class ASGIHTTPRequestHandler:
            """Bridge from stdlib HTTP to ASGI."""

            def __init__(http_self, request: Any, client_address: Any, server: Any):
                http_self.request = request
                http_self.client_address = client_address
                http_self.server = server

            async def handle(http_self) -> None:
                # Read request line
                line = http_self.request.readline().decode()
                method, path, _ = line.split()

                # Read headers
                headers: Dict[str, str] = {}
                while True:
                    line = http_self.request.readline().decode().strip()
                    if not line:
                        break
                    key, value = line.split(":", 1)
                    headers[key.strip()] = value.strip()

                # Read body
                content_length = int(headers.get("Content-Length", 0))
                body = http_self.request.read(content_length) if content_length else b""

                # Build scope
                scope = {
                    "type": "http",
                    "method": method,
                    "path": path,
                    "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
                    "query_string": b"",
                    "body": body,
                }

                # Call ASGI app
                BodyReader(http_self.request, self.app, scope)

        class BodyReader:
            def __init__(http_self, sock: Any, app: Any, scope: Dict) -> None:
                http_self.sock = sock
                http_self.app = app
                http_self.scope = scope
                http_self.response_started = False
                http_self.response_headers: List[Tuple] = []
                http_self.status_code = 200

            async def __call__(http_self, receive: Any, send: Any) -> None:
                message = await receive()
                if message["type"] == "http.request":
                    body = message.get("body", b"")

                    # Build Request
                    req = Request(
                        method=http_self.scope["method"],
                        path=http_self.scope["path"],
                        headers=dict(http_self.scope["headers"]),
                        body=body,
                    )

                    res = Response()

                    # Call app handler
                    await http_self.app(req, res)

                    # Send response
                    status_line = f"HTTP/1.1 {res.status_code}\r\n"
                    header_bytes = "".join(
                        f"{k}: {v}\r\n" for k, v in res.headers.items()
                    ).encode()
                    http_self.sock.sendall(
                        status_line.encode() + header_bytes + b"\r\n" + res.body
                    )

        self._server_socket = HTTPServer((self.host, self.port), ASGIHTTPRequestHandler)
        self._server_socket_thread = threading.Thread(
            target=self._server_socket.serve_forever
        )
        self._server_socket_thread.start()
        await self._shutdown_event.wait()

    async def shutdown(self) -> None:
        if self._server_socket:
            self._server_socket.shutdown()
        self._shutdown_event.set()


# Backward compatibility alias
AsyncServer = UvicornServer

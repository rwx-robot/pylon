"""pylon.application - Main application class with middleware chain support."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from pylon.protocol import Request, Response, WebSocket
from pylon.router import Router, RouterGroup
from pylon.router.radix import Handler
from pylon.server import Server, UvicornServer


@dataclass
class RouteConfig:
    """Configuration for a registered route."""

    method: str
    path: str
    handler: Callable
    middlewares: List[Callable] = field(default_factory=list)
    is_websocket: bool = False


class Application:
    """
    Main pylon application.

    Features:
    - Layered architecture (protocol/transport/router/server)
    - Route tree with parameter routing and groups
    - Three-level middleware: global / group / route-level
    - Type-safe request/response
    - ASGI support for production deployment
    """

    def __init__(
        self,
        name: str = "pylon",
        *,
        debug: bool = False,
        config: Optional[Any] = None,
    ):
        self.name = name
        self.debug = debug
        self._router = Router()
        self._global_middlewares: List[Callable] = []
        self._route_configs: List[RouteConfig] = []
        self._ws_route_configs: List[RouteConfig] = []
        self._startup_hooks: List[Callable] = []
        self._shutdown_hooks: List[Callable] = []
        # Optional Config object
        self._config = config

    @classmethod
    def from_config(cls, config: Any) -> Application:
        """
        Create an Application from a Config object.

        Example:
            from pylon import Application, Config

            config = Config(name="my-app", debug=True, port=9000)
            app = Application.from_config(config)
            app.run()
        """
        app = cls(name=config.name, debug=config.debug, config=config)
        # Register global middlewares from config
        for middleware in config.middleware:
            app.use(middleware)
        # Register startup/shutdown hooks from config
        for hook in config.startup_hooks:
            app.on_startup(hook)
        for hook in config.shutdown_hooks:
            app.on_shutdown(hook)
        return app

    # --- Route Registration ---

    def get(self, path: str) -> Callable[[Any], Any]:
        return self._add_route_decorator("GET", path)

    def post(self, path: str) -> Callable[[Any], Any]:
        return self._add_route_decorator("POST", path)

    def put(self, path: str) -> Callable[[Any], Any]:
        return self._add_route_decorator("PUT", path)

    def delete(self, path: str) -> Callable[[Any], Any]:
        return self._add_route_decorator("DELETE", path)

    def patch(self, path: str) -> Callable[[Any], Any]:
        return self._add_route_decorator("PATCH", path)

    def options(self, path: str) -> Callable[[Any], Any]:
        return self._add_route_decorator("OPTIONS", path)

    def head(self, path: str) -> Callable[[Any], Any]:
        return self._add_route_decorator("HEAD", path)

    def ws(self, path: str) -> Callable[[Any], Any]:
        """
        Register a WebSocket route.

        Example:
            @app.ws("/ws")
            async def echo(ws, req):
                await ws.accept()
                async for msg in ws:
                    if msg["type"] == "text":
                        await ws.send_text(msg["text"])
        """
        def decorator(handler: Any) -> Any:
            self.add_ws_route(path, handler)
            return handler
        return decorator

    def websocket(self, path: str) -> Callable[[Any], Any]:
        """Alias for ws()."""
        return self.ws(path)

    def add_ws_route(
        self,
        path: str,
        handler: Callable,
        *,
        middlewares: Optional[List[Callable]] = None,
    ) -> None:
        """Explicit WebSocket route registration with optional middlewares."""
        self._router.add_route("WS", path, handler)
        config = RouteConfig(
            method="WS",
            path=path,
            handler=handler,
            middlewares=middlewares or [],
            is_websocket=True,
        )
        self._ws_route_configs.append(config)

    def _add_route_decorator(self, method: str, path: str) -> Callable[[Any], Any]:
        """Decorator that registers a route and returns the handler."""
        def decorator(handler: Any) -> Any:
            self.add_route(method, path, handler)
            return handler
        return decorator

    def add_route(
        self,
        method: str,
        path: str,
        handler: Callable,
        *,
        middlewares: Optional[List[Callable]] = None,
    ) -> None:
        """Explicit route registration with optional route-level middlewares."""
        self._router.add_route(method, path, handler)
        config = RouteConfig(
            method=method,
            path=path,
            handler=handler,
            middlewares=middlewares or [],
        )
        self._route_configs.append(config)

    def route(
        self,
        path: str,
        methods: Optional[List[str]] = None,
        middlewares: Optional[List[Callable]] = None,
    ) -> Callable[[Any], Any]:
        """
        Decorator for registering a route with multiple methods.

        Example:
            @app.route("/users", methods=["GET", "POST"])
            async def users_handler(req, res):
                pass
        """
        methods = methods or ["GET"]
        middlewares = middlewares or []

        def decorator(handler: Any) -> Any:
            for method in methods:
                self.add_route(method, path, handler, middlewares=middlewares)
            return handler
        return decorator

    def group(self, prefix: str) -> RouterGroup:
        """Create a route group with shared prefix and middlewares."""
        return _AppRouterGroup(self, prefix)

    # --- Middleware ---

    def use(self, middleware: Callable) -> Application:
        """
        Register a global middleware.

        Global middlewares run for every request before route-specific handlers.

        Returns self for chaining.
        """
        self._global_middlewares.append(middleware)
        return self

    # --- Lifecycle Hooks ---

    def on_startup(self, hook: Callable) -> Callable:
        """Register a startup hook."""
        self._startup_hooks.append(hook)
        return hook

    def on_shutdown(self, hook: Callable) -> None:
        """Register a shutdown hook."""
        self._shutdown_hooks.append(hook)

    async def _run_hooks(self, hooks: List[Callable]) -> None:
        """Execute lifecycle hooks."""
        for hook in hooks:
            if asyncio.iscoroutinefunction(hook):
                await hook()
            else:
                hook()

    # --- Server ---

    def run(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        """Run the application with uvicorn.

        Uses values from self._config if not overridden by arguments.
        """
        if self._config is not None:
            if host is None:
                host = self._config.host
            if port is None:
                port = self._config.port
            if "reload" not in kwargs:
                kwargs["reload"] = self._config.reload
            if "log_level" not in kwargs:
                kwargs["log_level"] = self._config.log_level

        host = host or "127.0.0.1"
        port = port or 8000

        async def serve() -> None:
            await self._run_hooks(self._startup_hooks)
            try:
                server = UvicornServer(self, host=host, port=port, **kwargs)
                await server.serve()
            finally:
                await self._run_hooks(self._shutdown_hooks)

        asyncio.run(serve())

    # --- ASGI Interface ---

    async def __call__(self, scope: Dict, receive: Callable, send: Callable) -> None:
        """ASGI callable interface (HTTP + WebSocket)."""
        # Detect WebSocket scope
        if scope.get("type") == "websocket":
            await self._handle_websocket(scope, receive, send)
            return

        # --- HTTP request ---
        # Extract request info
        method = scope.get("method", "GET")
        path = scope.get("path", "/")
        headers = scope.get("headers", [])

        # Convert headers to dict
        headers_dict = {k.decode(): v.decode() for k, v in headers}

        # Parse query string
        raw_query = scope.get("query_string", b"")
        if raw_query:
            query_params = {}
            for item in raw_query.decode("utf-8").split("&"):
                if "=" in item:
                    k, v = item.split("=", 1)
                    query_params[k] = v
                elif item:
                    query_params[item] = ""
        else:
            query_params = {}

        # Read body
        body = b""
        while True:
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                if not message.get("more_body", False):
                    break

        # Build Request
        client_ip: Optional[str] = None
        if "client" in scope and scope["client"]:
            client_ip = scope["client"][0]  # (host, port) tuple

        request = Request(
            method=method,
            path=path,
            headers=headers_dict,
            query_params=query_params,
            body=body,
            client_ip=client_ip,
        )

        # Build Response
        response = Response()

        # Match route
        result = self._router.match(method, path)
        if result is None:
            response.status_code = 404
            response.json({"error": "Not Found", "path": path})
            await self._send_response(send, response)
            return

        handler, path_params = result
        request.path_params = path_params

        # Get route-level middlewares
        route_middlewares = self._get_route_middlewares(method, path)

        # Build and execute middleware chain
        await self._execute_chain(
            request, response, handler, route_middlewares, send
        )

    def _get_route_middlewares(self, method: str, path: str) -> List[Callable]:
        """Get middlewares for a specific route."""
        for config in self._route_configs:
            if config.method == method and config.path == path:
                return list(config.middlewares)
        return []

    async def _execute_chain(
        self,
        req: Request,
        res: Response,
        handler: Callable,
        route_middlewares: List[Callable],
        send: Callable,
    ) -> None:
        """Execute the full middleware chain and then the handler."""
        # Chain: global middlewares -> route middlewares -> handler
        all_middlewares = self._global_middlewares + route_middlewares

        async def final_handler() -> None:
            try:
                result = handler(req, res)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                if self.debug:
                    import traceback
                    res.status_code = 500
                    res.json({
                        "error": "Internal Server Error",
                        "detail": str(exc),
                        "trace": traceback.format_exc(),
                    })
                else:
                    res.status_code = 500
                    res.json({
                        "error": "Internal Server Error",
                        "detail": "An unexpected error occurred",
                    })

        if not all_middlewares:
            await final_handler()
            await self._send_response(send, res)
            return

        async def recursive_chain(index: int) -> None:
            if index >= len(all_middlewares):
                await final_handler()
                return

            middleware = all_middlewares[index]

            async def next_() -> None:
                await recursive_chain(index + 1)

            try:
                if asyncio.iscoroutinefunction(middleware):
                    await middleware(req, res, next_)
                elif hasattr(middleware, 'dispatch') and asyncio.iscoroutinefunction(middleware.dispatch):
                    # BaseMiddleware instance: call async dispatch method
                    await middleware.dispatch(req, res, next_)
                elif hasattr(middleware, '__call__') and asyncio.iscoroutinefunction(middleware.__call__):
                    # Middleware with async __call__ (e.g. async def __call__(self, req, res, nxt))
                    await middleware(req, res, next_)
                else:
                    # Plain sync callable (function)
                    middleware(req, res, next_)
            except Exception as exc:  # noqa: BLE001
                # Recovery behavior
                if self.debug:
                    import traceback
                    res.status_code = 500
                    res.json({
                        "error": "Middleware Error",
                        "detail": str(exc),
                        "trace": traceback.format_exc(),
                    })
                else:
                    res.status_code = 500
                    res.json({
                        "error": "Middleware Error",
                        "detail": "An unexpected error occurred",
                    })

        await recursive_chain(0)
        await self._send_response(send, res)

    async def _send_response(self, send: Callable, response: Response) -> None:
        """Send HTTP response via ASGI."""
        await send({
            "type": "http.response.start",
            "status": response.status_code,
            "headers": [
                (k.encode(), v.encode())
                for k, v in response.headers.items()
            ],
        })
        await send({
            "type": "http.response.body",
            "body": response.body,
        })

    async def _handle_websocket(
        self, scope: Dict, receive: Callable, send: Callable
    ) -> None:
        """Handle a WebSocket connection."""
        path = scope.get("path", "/")

        # Match route
        result = self._router.match("WS", path)
        if result is None:
            await send({"type": "websocket.close", "code": 404})
            return

        handler, path_params = result

        # Build a minimal Request for middleware compatibility
        headers_dict: Dict[str, str] = {}
        headers = scope.get("headers", [])
        for k, v in headers:
            headers_dict[k.decode()] = v.decode()

        client_ip: Optional[str] = None
        if "client" in scope and scope["client"]:
            client_ip = scope["client"][0]

        request = Request(
            method="WS",
            path=path,
            headers=headers_dict,
            path_params=path_params,
            client_ip=client_ip,
        )

        response = Response()

        # Build WebSocket wrapper
        ws = WebSocket(scope, receive, send)

        # Get route-level middlewares
        route_middlewares = self._get_route_middlewares("WS", path)

        # Execute middleware chain then call handler
        await self._execute_ws_chain(request, response, handler, route_middlewares, ws)

    async def _execute_ws_chain(
        self,
        req: Request,
        res: Response,
        handler: Callable,
        route_middlewares: List[Callable],
        ws: WebSocket,
    ) -> None:
        """Execute middleware chain for WebSocket and then call the handler."""
        all_middlewares = self._global_middlewares + route_middlewares

        async def final_handler() -> None:
            try:
                await handler(ws, req)
            except Exception as exc:  # noqa: BLE001
                if self.debug:
                    import traceback
                    # Try to send error over websocket before closing
                    try:
                        await ws.send_text(
                            f"Server error: {exc}\n{traceback.format_exc()}"
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    await ws.close(1011)
                else:
                    try:
                        await ws.send_text("Internal server error")
                    except Exception:  # noqa: BLE001
                        pass
                    await ws.close(1011)

        if not all_middlewares:
            await final_handler()
            return

        async def recursive_chain(index: int) -> None:
            if index >= len(all_middlewares):
                await final_handler()
                return

            middleware = all_middlewares[index]

            async def next_() -> None:
                await recursive_chain(index + 1)

            try:
                if asyncio.iscoroutinefunction(middleware):
                    await middleware(req, res, next_)
                elif hasattr(middleware, 'dispatch') and asyncio.iscoroutinefunction(middleware.dispatch):
                    # BaseMiddleware instance: call async dispatch method
                    await middleware.dispatch(req, res, next_)
                else:
                    # Plain sync callable (function)
                    middleware(req, res, next_)
            except Exception as exc:  # noqa: BLE001
                if self.debug:
                    import traceback
                    try:
                        await ws.send_text(
                            f"Middleware error: {exc}\n{traceback.format_exc()}"
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    await ws.close(1011)
                else:
                    try:
                        await ws.send_text("Middleware error")
                    except Exception:  # noqa: BLE001
                        pass
                    await ws.close(1011)

        await recursive_chain(0)

    # --- Route introspection ---

    def routes(self) -> List[RouteConfig]:
        """Return all registered routes (HTTP + WebSocket)."""
        return list(self._route_configs) + list(self._ws_route_configs)

    def print_routes(self) -> None:
        """Print all registered routes (debug)."""
        print("Registered Routes:")
        for config in self._route_configs:
            middlewares = f" [+{len(config.middlewares)} middleware]" if config.middlewares else ""
            print(f"  {config.method:7} {config.path}{middlewares}")


class _AppRouterGroup:
    """
    RouterGroup that bridges to Application.add_route (for _route_configs tracking).
    """

    def __init__(self, app: Application, prefix: str):
        self._app = app
        self._prefix = prefix
        self._middlewares: List[Callable] = []

    def _add(self, method: str, path: str) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            self._app.add_route(
                method, self._prefix + path, handler,
                middlewares=list(self._middlewares),
            )
            return handler
        return decorator

    def get(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("GET", path)

    def post(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("POST", path)

    def put(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("PUT", path)

    def delete(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("DELETE", path)

    def patch(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("PATCH", path)

    def options(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("OPTIONS", path)

    def head(self, path: str) -> Callable[[Handler], Handler]:
        return self._add("HEAD", path)

    def use(self, middleware: Callable) -> Callable[[Handler], Handler]:
        """
        Register a group-level middleware.

        Can be used as:
          @api.use(my_mw)
          async def handler(req, res, next_): ...

        Or:
          @api.use(CORSMiddleware(...))
          async def dummy(req, res, next_): ...
        """
        self._middlewares.append(middleware)
        return lambda handler: handler

    def group(self, prefix: str) -> _AppRouterGroup:
        """Create a nested route group."""
        return _AppRouterGroup(self._app, self._prefix + prefix)


# Backward compatibility
App = Application


def run(app: Application, host: str = "127.0.0.1", port: int = 8000, **kwargs: Any) -> None:
    """Run a pylon application."""
    app.run(host=host, port=port, **kwargs)

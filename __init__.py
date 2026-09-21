"""
pylon - A Python web framework inspired by Hertz (CloudWeGo).

A high-performance, asyncio-based HTTP framework with:
- Layered architecture (protocol/transport/router/server)
- Radix tree routing with parameter routes and groups
- Three-level middleware: global / group / route-level
- Type-safe request/response
- pydantic binding and validation
- Built-in ASGI support for production deployment

Example:
    from pylon import Application, Response
    from pylon.middleware import RecoveryMiddleware, LoggerMiddleware

    app = Application()
    app.use(RecoveryMiddleware(debug=True))
    app.use(LoggerMiddleware())

    @app.get("/")
    async def hello(req, res):
        res.json({"message": "Hello, pylon!"})

    @app.get("/users/:id")
    async def get_user(req, res):
        res.json({"user_id": req.path_params["id"]})

    app.run(host="0.0.0.0", port=8080)
"""

__version__ = "5.0.0"
__author__ = "pylon contributors"

from pylon.application import Application, App, run
from pylon.protocol import Request, Response, WebSocket
from pylon.router import Router, RouterGroup
from pylon.server import Server, UvicornServer, AsyncServer
from pylon.client import (
    Client,
    ClientRequest,
    ClientResponse,
    ClientMiddleware,
    RetryStrategy,
    DefaultRetryStrategy,
    ServiceDiscovery,
    StaticServiceDiscovery,
    PylonClientError,
    ClientConnectionError,
    ClientTimeoutError,
    ClientResponseError,
)
from pylon.config import Config
from pylon.binding import bind, bind_query, bind_path
from pylon.exceptions import (
    PylonError,
    BindingError,
    ValidationError,
    NotFoundError,
    UnauthorizedError,
    ConflictError,
)
from pylon.middleware import (
    BaseMiddleware,
    RecoveryMiddleware,
    LoggerMiddleware,
    RequestIDMiddleware,
    CORSMiddleware,
    BindingMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    IPAllowlistMiddleware,
    MetricsMiddleware,
    TracingMiddleware,
    HealthCheckMiddleware,
    StaticMiddleware,
)
from pylon.tools import (
    OpenAPISpec,
    Endpoint,
    Parameter,
    RequestBody,
    ResponseContent,
    parse_openapi_spec,
    generate_pylon_app,
    generate_openapi_spec,
)
from pylon.cli import cli
from pylon.testing import PylonTestClient, PylonTestResponse, PylonTestWebSocket

# Public API surface
__all__ = [
    # Application
    "Application",
    "App",
    "run",
    # Protocol
    "Request",
    "Response",
    "WebSocket",
    # Router
    "Router",
    "RouterGroup",
    # Server
    "Server",
    "UvicornServer",
    "AsyncServer",
    # Client
    "Client",
    "ClientRequest",
    "ClientResponse",
    "ClientMiddleware",
    "RetryStrategy",
    "DefaultRetryStrategy",
    "ServiceDiscovery",
    "StaticServiceDiscovery",
    "PylonClientError",
    "ClientConnectionError",
    "ClientTimeoutError",
    "ClientResponseError",
    # Config
    "Config",
    # Binding
    "bind",
    "bind_query",
    "bind_path",
    # Exceptions
    "PylonError",
    "BindingError",
    "ValidationError",
    "NotFoundError",
    "UnauthorizedError",
    "ConflictError",
    # Middleware
    "BaseMiddleware",
    "RecoveryMiddleware",
    "LoggerMiddleware",
    "RequestIDMiddleware",
    "CORSMiddleware",
    "BindingMiddleware",
    "RateLimitMiddleware",
    "SecurityHeadersMiddleware",
    "IPAllowlistMiddleware",
    "MetricsMiddleware",
    "TracingMiddleware",
    "HealthCheckMiddleware",
    "StaticMiddleware",
    # Tools / CLI
    "OpenAPISpec",
    "Endpoint",
    "Parameter",
    "RequestBody",
    "ResponseContent",
    "parse_openapi_spec",
    "generate_pylon_app",
    "generate_openapi_spec",
    "cli",
    # Testing
    "PylonTestClient",
    "PylonTestResponse",
    "PylonTestWebSocket",
    # Aliases
    "TestClient",
    "TestResponse",
    "TestWebSocket",
    # Version
    "__version__",
]
